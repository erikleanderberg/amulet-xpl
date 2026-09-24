"""
Synthetic nap sessions in the exact bench_bridge capture format.

Purpose: exercise the whole pipeline (parse -> features -> model -> evaluate -> GUI replay)
before any real recording exists, and provide a floor of "obvious" onsets that any
detector must catch. The physiology is a caricature and is documented as such: the point
is the wiring, not the realism. Parameters are drawn per session so no two look alike.

Modelled effects at sleep onset (direction and rough scale from the literature; tune later
against real captures):
  FSR   grip force decays toward a relaxed floor (muscle tone loss), tau ~ 30-90 s;
        awake: small periodic re-grips; occasional hypnic jerk spike after onset.
  PPG   heart rate falls ~4-10 bpm over ~2 min; RSA (breath modulation of IBI) grows;
        breathing slows slightly; pulse amplitude rises a little (vasodilation).

Firmware behaviour reproduced (protocol v2, 16 Sep 2026): S,<ms>,<fsr>,<pulse>,<thresh> at 100 Hz,
B,<ms>,<bpm>,<ibi> per beat with BPM = 60000 / mean(last 10 IBI) and a 40-180 bpm plausibility gate.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

FS = 100


def _pulse_shape(n: int) -> np.ndarray:
    """One cardiac cycle of a PPG-ish waveform, n samples, peak 1 at ~20 % of the cycle."""
    x = np.linspace(0, 1, n, endpoint=False)
    systole = np.exp(-((x - 0.18) / 0.07) ** 2)
    dicrotic = 0.35 * np.exp(-((x - 0.42) / 0.09) ** 2)
    decay = np.exp(-x * 2.2)
    return (systole + dicrotic) * (0.6 + 0.4 * decay)


def make_session(rng: np.random.Generator, minutes: float = 8.0, onset_at: float | None = 240.0,
                 base_bpm: float = 68.0, jerks: bool = True) -> tuple[list[str], dict]:
    """Return capture lines (without host_time) and a dict of hidden parameters."""
    n = int(minutes * 60 * FS)
    t = np.arange(n) / FS
    onset = onset_at if onset_at is not None else np.inf
    # progress of the transition 0..1 (logistic around onset, ~60 s wide)
    prog = 1 / (1 + np.exp(-(t - onset) / 15.0))

    # ---------------- FSR ----------------
    grip0 = rng.uniform(450, 700)
    relaxed = rng.uniform(80, 200)
    tau = rng.uniform(30, 90)
    fsr = np.full(n, grip0, dtype=float)
    fsr += 12 * np.sin(2 * np.pi * t / rng.uniform(20, 60))          # slow sway
    for ts in np.arange(0, minutes * 60, rng.uniform(25, 70)):         # awake re-grips (+) and loosenings (-)
        if ts < onset:
            i = int(ts * FS); w = int(rng.uniform(1.5, 6) * FS)
            fsr[i:i + w] += rng.choice([1, -1], p=[0.6, 0.4]) * rng.uniform(30, 110) * np.hanning(min(w, n - i))
    if np.isfinite(onset) and rng.random() < 0.5:                       # a long awake 'relax' dip, false-alarm bait
        ts = rng.uniform(60, max(61, onset - 90)); i = int(ts * FS); w = int(rng.uniform(15, 40) * FS)
        fsr[i:i + w] -= rng.uniform(80, 200) * np.hanning(min(w, n - i))
    after = t > onset
    fsr[after] = relaxed + (fsr[after] - relaxed) * np.exp(-(t[after] - onset) / tau)
    if jerks and np.isfinite(onset):
        for _ in range(rng.integers(0, 3)):
            ts = onset + rng.uniform(20, 120)
            i = int(ts * FS)
            if i < n - FS:
                fsr[i:i + 40] += 250 * np.hanning(40)
    fsr += rng.normal(0, 4, n) + 20 * np.convolve(rng.normal(0, 1, n), np.ones(200) / 200, 'same')
    fsr = np.clip(fsr, 0, 1023)

    # ---------------- heart / PPG ----------------
    bpm_drop = rng.uniform(4, 10)
    resp_hz = 0.25 - 0.04 * prog
    rsa_amp = 25 + 8 * prog                                           # ms (RMSSD barely moves at N1)
    # build beats sequentially
    beats_t, ibis = [], []
    tb = 0.3
    phase_resp = rng.uniform(0, 2 * np.pi)
    while tb < minutes * 60:
        p = 1 / (1 + np.exp(-(tb - onset) / 15.0))
        hr = base_bpm - bpm_drop * p
        ibi = 60000 / hr
        ibi += rsa_amp_at(rsa_amp, tb) * np.sin(2 * np.pi * resp_hz_at(resp_hz, tb) * tb + phase_resp)
        p_irr = 1 / (1 + np.exp(-(tb - onset) / 15.0))
        ibi += rng.normal(0, 12 + 10 * p_irr)                         # N1 breathing/HR irregularity
        beats_t.append(tb); ibis.append(ibi)
        tb += ibi / 1000
    ppg = np.zeros(n)
    amp = 180 + 70 * prog                                              # vasodilation: strongest early channel
    for k, (bt, ibi) in enumerate(zip(beats_t, ibis)):
        i0 = int(bt * FS); ln = max(int(ibi / 1000 * FS), 10)
        seg = _pulse_shape(ln) * amp[min(i0, n - 1)]
        i1 = min(i0 + ln, n)
        ppg[i0:i1] += seg[: i1 - i0]
    ppg += 470 + 15 * np.sin(2 * np.pi * 0.25 * t) + rng.normal(0, 3, n)
    ppg = np.clip(ppg, 0, 1023)

    # ---------------- firmware emulation ----------------
    lines: list[str] = []
    lines.append('I,synthetic session')
    lines.append('N,label:awake')
    beat_i = 0
    rate = []
    for s in range(n):
        ms = (s + 1) * 10
        lines.append(f'S,{ms},{int(fsr[s])},{int(ppg[s])},{int(470 + amp[s] / 2)}')
        while beat_i < len(beats_t) and beats_t[beat_i] * FS <= s:
            ibi = int(ibis[beat_i])
            rate.append(ibi); rate = rate[-10:]
            bpm = int(60000 / (sum(rate) / len(rate)))
            if 40 <= bpm <= 180 and len(rate) >= 2:
                lines.append(f'B,{ms},{bpm},{ibi}')
            beat_i += 1
        if np.isfinite(onset) and s == int(onset * FS):
            lines.append('N,onset')
    hidden = dict(onset=onset if np.isfinite(onset) else None, grip0=grip0, relaxed=relaxed, tau=tau,
                  base_bpm=base_bpm, bpm_drop=bpm_drop)
    return lines, hidden


def rsa_amp_at(arr: np.ndarray, tb: float) -> float:
    return float(arr[min(int(tb * FS), len(arr) - 1)])


def resp_hz_at(arr: np.ndarray, tb: float) -> float:
    return float(arr[min(int(tb * FS), len(arr) - 1)])


def write_rssi(path: Path, minutes: float, onset: float | None, rng: np.random.Generator,
               t0: float = 1_726_000_000.0) -> None:
    """Companion rssi-<stamp>.csv in rssi_capture.py's format: ~66 Hz, 15 ms +- jitter.
    Caricature: -50 dBm awake with 1.2 dB noise; after onset the hand opens and the level shifts by a
    few dB (direction unknown until measured; +4 dB used here) over the same time course as grip."""
    with open(path, 'w') as fh:
        fh.write('host_time,type,a,b,c,d\n')
        t = 0.0; dev = 0; seq = 0; base = rng.uniform(-56, -46); shift = rng.uniform(2, 6); nxt_status = 0.0
        while t < minutes * 60:
            dt = 15 + int(rng.choice([0, 0, 0, 1, -1]))
            t += dt / 1000; dev += dt
            prog = 1 / (1 + np.exp(-(t - onset) / 15.0)) if onset is not None else 0.0
            v = int(round(base + shift * prog + rng.normal(0, 1.2)))
            fh.write(f'{t0 + t:.4f},S,{dev},{v},{seq},\n')
            if dev // 40 != (dev - dt) // 40:
                seq = (seq + 1) % 256
            if t >= nxt_status:
                fh.write(f'{t0 + t:.4f},T,1.0,12,66,0\n'); nxt_status += 1.0


def write(lines: list[str], path: Path, t0: float = 1_726_000_000.0) -> None:
    with open(path, 'w') as fh:
        fh.write('host_time,type,fields...\n')
        seq = 0
        for ln in lines:
            if ln.startswith('S,'):
                seq = int(ln.split(',')[1]) // 10
            fh.write(f'{t0 + seq / FS:.3f},{ln}\n')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='data/synthetic')
    ap.add_argument('--n', type=int, default=12, help='sessions with an onset')
    ap.add_argument('--awake', type=int, default=4, help='awake-only control sessions')
    ap.add_argument('--seed', type=int, default=7)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    for k in range(a.n):
        mins = rng.uniform(7, 12)
        onset = rng.uniform(150, mins * 60 - 120)
        lines, h = make_session(rng, mins, onset, base_bpm=rng.uniform(58, 78))
        write(lines, out / f'synth-onset-{k:02d}.csv')
        write_rssi(out / f'rssi-onset-{k:02d}.csv', mins, onset, rng)
        print(f'synth-onset-{k:02d}: {mins:.1f} min, onset {h["onset"]:.0f} s, bpm {h["base_bpm"]:.0f}-{h["bpm_drop"]:.0f}')
    for k in range(a.awake):
        mins = rng.uniform(6, 10)
        lines, _ = make_session(rng, mins, None, base_bpm=rng.uniform(58, 78))
        write(lines, out / f'synth-awake-{k:02d}.csv')
        write_rssi(out / f'rssi-awake-{k:02d}.csv', mins, None, rng)
        print(f'synth-awake-{k:02d}: {mins:.1f} min, no onset')


if __name__ == '__main__':
    main()
