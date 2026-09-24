"""
Parse a bench_bridge capture (bench-*.csv) into aligned numpy arrays.

Capture format (one line per serial line, written by bench_bridge.py):
    host_time,type,fields...
  protocol v2 (bench3_live from 14 Sep 2026):
    1726000000.123,S,<ms>,<fsr>,<pulse>,<thresh>[,<ignored>]  100 Hz tick with board time. A 5th field from
                                                            transitional firmware is ignored (EDA was removed 16 Sep 2026).
    1726000000.123,B,<ms>,<bpm>,<ibi_ms>  per beat
  protocol v1 (older boards), still accepted:
    1726000000.123,F,<seq>,<fsr>          100 Hz  FSR raw ADC (10-bit)
    1726000000.123,P,<signal>,<thresh>    100 Hz  PPG waveform sample + beat threshold
    1726000000.123,E,<...>                ignored (legacy EDA)
    1726000000.123,R,<ms>,<rssi_dbm>      link RSSI measured by the board on its connection events (~50 Hz)
    1726000000.123,R,<rssi_dbm>           link RSSI read by the bridge's radio (host readRSSI, ~0.5 Hz on macOS)
    1726000000.123,B,<bpm>,<ibi_ms>       per beat, firmware-detected
    1726000000.123,H,...                  haptic status (ignored here)
    1726000000.123,I,...                  info (ignored)
    1726000000.123,N,<text>               note / label (see LABELS below)

Timeline: v2 S lines carry board ms (steps of 10); v1 F lines carry a sequence counter. Either
way every sample lands on a 100 Hz grid (t = tick / 100 s) immune to host timestamp jitter.
Beats are placed at their board ms (v2) or at the tick current when they arrived (v1).

LABELS (notes written via the bridge /note endpoint, by the GUI or by hand):
    label:awake | label:drowsy | label:halfway | label:asleep   state marks (halfway = Dormio's middle answer)
    report:awake | report:halfway | report:asleep  subjective answer to "And were you asleep?" after a cue
                                                   (Horowitz 2020 sec. 2.4; used by thresholds.py)
    onset                                          ground-truth sleep-onset instant
    probe:hit | probe:miss                         response probe result
    haptic:<preset>                                cue fired (so post-cue data can be masked)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FS = 100.0  # Hz, F/P/E rate


@dataclass
class Session:
    name: str
    t: np.ndarray            # seconds on the seq grid, uniform 1/FS
    fsr: np.ndarray          # raw ADC 0..1023 (nan where missing)
    ppg: np.ndarray          # raw ADC waveform (nan where missing)
    beat_t: np.ndarray       # seconds of firmware beats
    beat_ibi: np.ndarray     # ms
    beat_bpm: np.ndarray
    notes: list[tuple[float, str]] = field(default_factory=list)
    rssi: np.ndarray | None = None   # dBm on the same 100 Hz grid (forward-filled), from a companion rssi-*.csv
    host_t0: float = 0.0             # host_time of grid t = 0 (aligns the RSSI capture, same laptop clock)

    # ---- label helpers -------------------------------------------------
    def note_times(self, prefix: str) -> np.ndarray:
        return np.array([t for t, s in self.notes if s == prefix or s.startswith(prefix + ':')])

    @property
    def onset(self) -> float | None:
        o = self.note_times('onset')
        return float(o[0]) if len(o) else None

    @property
    def duration(self) -> float:
        return float(self.t[-1] - self.t[0]) if len(self.t) else 0.0

    def state_at(self, t: np.ndarray) -> np.ndarray:
        """0 = awake, 1 = drowsy, 2 = asleep, from label:* notes (forward filled). 'onset' => asleep."""
        marks = sorted([(tt, s) for tt, s in self.notes if s.startswith('label:') or s == 'onset'])
        out = np.zeros(len(t), dtype=int)
        cur = 0
        i = 0
        for k, tt in enumerate(t):
            while i < len(marks) and marks[i][0] <= tt:
                s = marks[i][1]
                cur = 2 if s in ('onset', 'label:asleep') else 1 if s in ('label:drowsy', 'label:halfway') else 0
                i += 1
            out[k] = cur
        return out


def load(path: str | Path) -> Session:
    path = Path(path)
    seq_fsr: dict[int, float] = {}
    seq_ppg: dict[int, float] = {}
    seq_rssi: dict[int, float] = {}
    beats: list[tuple[int, float, float]] = []
    notes: list[tuple[int, str]] = []
    cur = 0
    first_seq = None
    host_t0 = 0.0
    with open(path, newline='') as fh:
        rd = csv.reader(fh)
        for row in rd:
            if len(row) < 2 or row[0] == 'host_time':
                continue
            typ = row[1]
            try:
                if typ == 'S':                                  # v2: ms,fsr,pulse,thresh[,ignored]
                    cur = int(row[2]) // 10
                    if first_seq is None:
                        first_seq = cur; host_t0 = float(row[0])
                    seq_fsr[cur] = float(row[3]); seq_ppg[cur] = float(row[4])
                elif typ == 'F':
                    cur = int(row[2])
                    if first_seq is None:
                        first_seq = cur; host_t0 = float(row[0])
                    seq_fsr[cur] = float(row[3])
                elif typ == 'P':
                    seq_ppg[cur] = float(row[2])
                elif typ == 'R':                                # R,<ms>,<rssi> (board) or R,<rssi> (bridge)
                    if len(row) >= 4:
                        seq_rssi[int(row[2]) // 10] = float(row[3])
                    else:
                        seq_rssi[cur] = float(row[2])
                elif typ == 'B':
                    if len(row) >= 5:                           # v2: ms,bpm,ibi
                        beats.append((int(row[2]) // 10, float(row[3]), float(row[4])))
                    else:                                       # v1: bpm,ibi
                        beats.append((cur, float(row[2]), float(row[3])))
                elif typ == 'N':
                    notes.append((cur, ','.join(row[2:]).strip()))
            except (ValueError, IndexError):
                continue
    if first_seq is None:
        raise ValueError(f'{path}: no F lines')
    last_seq = max(seq_fsr)
    n = last_seq - first_seq + 1
    t = np.arange(n) / FS
    fsr = np.full(n, np.nan); ppg = np.full(n, np.nan)
    for s, v in seq_fsr.items(): fsr[s - first_seq] = v
    for s, v in seq_ppg.items(): ppg[s - first_seq] = v
    bt = np.array([(s - first_seq) / FS for s, _, _ in beats])
    bpm = np.array([b for _, b, _ in beats]); ibi = np.array([i for _, _, i in beats])
    sess = Session(path.stem, t, fsr, ppg, bt, ibi, bpm,
                   [((s - first_seq) / FS, txt) for s, txt in notes], host_t0=host_t0)
    if seq_rssi:                                                # in-file R lines (bridge readRSSI), forward-filled
        r = np.full(n, np.nan); last = np.nan; prev = 0
        for sq in sorted(seq_rssi):
            i = sq - first_seq
            if 0 <= i < n:
                if not np.isnan(last): r[prev:i] = last
                r[i] = seq_rssi[sq]; last = seq_rssi[sq]; prev = i
        if not np.isnan(last): r[prev:] = last
        sess.rssi = r
    else:
        comp = find_rssi_companion(path, host_t0, host_t0 + t[-1])
        if comp is not None:
            sess.rssi = load_rssi_on_grid(comp, host_t0, n)
    return sess


def find_rssi_companion(bench_path: Path, t_start: float, t_end: float) -> Path | None:
    """rssi-<stamp>.csv next to the bench capture: exact stamp first, else host-time overlap."""
    d = bench_path.parent
    stem = bench_path.stem
    for pre in ('bench-', 'synth-'):                   # bench-<stamp> -> rssi-<stamp>; synth-onset-03 -> rssi-onset-03
        if stem.startswith(pre):
            cand = d / f'rssi-{stem[len(pre):]}.csv'
            if cand.exists():
                return cand
    best = None
    for c in sorted(d.glob('rssi-*.csv')):
        try:
            with open(c) as fh:
                next(fh)
                first = float(next(fh).split(',')[0])
            with open(c, 'rb') as fh:
                fh.seek(-200, 2) if c.stat().st_size > 200 else None
                last = float(fh.read().decode(errors='ignore').strip().splitlines()[-1].split(',')[0])
        except (StopIteration, ValueError, IndexError):
            continue
        if first <= t_end and last >= t_start:
            best = c
    return best


def load_rssi_on_grid(path: Path, host_t0: float, n: int) -> np.ndarray:
    """Forward-fill S rows of an rssi capture onto the 100 Hz grid starting at host_t0."""
    out = np.full(n, np.nan)
    last = np.nan; i_prev = 0
    with open(path, newline='') as fh:
        rd = csv.reader(fh)
        for row in rd:
            if len(row) < 4 or row[1] != 'S':
                continue
            try:
                i = int(round((float(row[0]) - host_t0) * FS)); v = float(row[3])
            except ValueError:
                continue
            if i < 0:
                last = v; continue
            if i >= n:
                break
            if not np.isnan(last):
                out[i_prev:i] = last
            out[i] = v; last = v; i_prev = i
    if not np.isnan(last):
        out[i_prev:] = last
    return out


def load_dir(d: str | Path) -> list[Session]:
    """Every bench capture in a directory. rssi-*.csv are companions, not sessions."""
    return [load(p) for p in sorted(Path(d).glob('*.csv')) if p.name.startswith(('bench-', 'synth-'))]
