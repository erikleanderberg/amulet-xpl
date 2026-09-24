"""
Session filing for self-training: start/end a nap session, file every raw stream into
data/sessions/, and summarise it against the Dormio deltas.

A filed session is three files with one stamp:
    data/sessions/bench-<stamp>.csv     every sensor line + every label (from the bridge)
    data/sessions/rssi-<stamp>.csv      every RSSI sample in the session window (cut from the
                                        continuous rssi capture by host time)
    data/sessions/session-<stamp>.json  metadata, detector config at the time, and the summary
plus one row in data/sessions/ledger.csv. The repo is the store; commit data/sessions/.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
SESSIONS = HERE / 'data' / 'sessions'
CAPTURES = HERE / 'data' / 'captures'
LEDGER = SESSIONS / 'ledger.csv'
LEDGER_COLS = ['stamp', 'name', 'date', 'duration_min', 'model', 'onset_s', 'first_detect_s', 'first_cue_s',
               'cue_latency_s', 'cues', 'reports', 'first_report', 'd_hr_bpm', 'd_flex_kohm', 'rssi_shift_db', 'notes']


def new_stamp() -> str:
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')


def cut_rssi(start_host: float, end_host: float, out: Path, pad: float = 5.0) -> int:
    """Rows from every rssi-*.csv in data/captures whose host_time is inside the session window."""
    n = 0
    with open(out, 'w') as fo:
        fo.write('host_time,type,a,b,c,d\n')
        for src in sorted(CAPTURES.glob('rssi-*.csv')):
            with open(src, newline='') as fh:
                for row in csv.reader(fh):
                    if len(row) < 2 or row[0] == 'host_time':
                        continue
                    try:
                        ht = float(row[0])
                    except ValueError:
                        continue
                    if start_host - pad <= ht <= end_host + pad:
                        fo.write(','.join(row) + '\n'); n += int(row[1] == 'S')
    return n


def append_notes(src: Path, dst: Path, notes: list[tuple[float, str]], host_t0: float) -> None:
    """Replay dry-run: copy the source capture and append the labels made during the replay."""
    shutil.copy(src, dst)
    with open(dst, 'a') as fh:
        for t, txt in notes:
            fh.write(f'{host_t0 + t:.3f},N,{txt}\n')


def summarise(bench_path: Path, cfg: dict) -> dict:
    """Everything a person wants to know right after the nap, computed from the filed files."""
    import train
    import thresholds
    from onset.parse import load, load_dir
    from rssi_report import win_stats
    s = load(bench_path)
    notes = sorted(s.notes)
    first = lambda pre: next((t for t, x in notes if x == pre or x.startswith(pre + ':')), None)
    onset = s.onset
    det = first('detected'); cue = first('haptic'); rep = next((x for t, x in notes if x.startswith('report:')), None)
    reports = [x[7:] for t, x in notes if x.startswith('report:')]
    cues = sum(1 for t, x in notes if x.startswith('haptic:'))
    mine = thresholds.compute([s], use_probe=True)
    ch = mine['channels']
    rssi_shift = None
    if s.rssi is not None and onset is not None:
        a = win_stats(s, onset, -90, -30); b = win_stats(s, onset, 30, 90)
        if a and b:
            rssi_shift = round(b[0] - a[0], 2)
    all_sessions = [x for x in load_dir(SESSIONS)]
    cumulative = thresholds.compute(all_sessions, use_probe=True) if all_sessions else None
    return dict(
        duration_min=round(s.duration / 60, 1), onset_s=onset, first_detect_s=det, first_cue_s=cue,
        cue_latency_s=(None if onset is None or cue is None else round(cue - onset, 1)),
        cues=cues, reports=reports, first_report=rep[7:] if rep else None,
        d_hr_bpm=ch['hr']['sleep_mean'], d_flex_kohm=ch['flex']['sleep_mean'], rssi_shift_db=rssi_shift,
        dormio=dict(calib_s=train.CALIB_S, d_hr=train.DORMIO_DELTA_HR_BPM, d_flex=train.DORMIO_DELTA_FLEX_KOHM,
                    model=train.MODEL, signed=train.DORMIO_SIGNED),
        cumulative=cumulative, n_sessions_total=len(all_sessions),
        probe_misses=sum(1 for t, x in notes if x == 'probe:miss'), probe_hits=sum(1 for t, x in notes if x == 'probe:hit'),
    )


def file_session(stamp: str, name: str, user_notes: str, bench_src: Path, start_host: float, end_host: float,
                 cfg: dict, replay_notes: list | None = None, host_t0: float | None = None) -> dict:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    dst = SESSIONS / f'bench-{stamp}.csv'
    if replay_notes is not None:
        append_notes(bench_src, dst, replay_notes, host_t0 or 0.0)
    else:
        shutil.copy(bench_src, dst)
    n_rssi = cut_rssi(start_host, end_host, SESSIONS / f'rssi-{stamp}.csv')
    if n_rssi == 0:
        (SESSIONS / f'rssi-{stamp}.csv').unlink(missing_ok=True)
        with open(dst) as fh:                              # link RSSI written by the bridge into the capture itself
            n_rssi = sum(1 for ln in fh if ',R,' in ln)
    summary = summarise(dst, cfg)
    meta = dict(stamp=stamp, name=name, notes=user_notes, start_host=start_host, end_host=end_host,
                date=datetime.datetime.fromtimestamp(start_host).isoformat(timespec='seconds'),
                rssi_samples=n_rssi, cfg=cfg, summary=summary, files=[dst.name] + ([f'rssi-{stamp}.csv'] if n_rssi else []))
    (SESSIONS / f'session-{stamp}.json').write_text(json.dumps(meta, indent=1, default=str))
    new = not LEDGER.exists()
    with open(LEDGER, 'a', newline='') as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(LEDGER_COLS)
        w.writerow([stamp, name, meta['date'], summary['duration_min'], summary['dormio']['model'], summary['onset_s'],
                    summary['first_detect_s'], summary['first_cue_s'], summary['cue_latency_s'], summary['cues'],
                    len(summary['reports']), summary['first_report'], summary['d_hr_bpm'], summary['d_flex_kohm'],
                    summary['rssi_shift_db'], user_notes.replace(',', ';')])
    return meta


def regenerate_review() -> Path:
    out = HERE / 'data' / 'exports' / 'session-review.html'
    subprocess.run([sys.executable, str(HERE / 'session_view.py'), '--data', str(SESSIONS), '--out', str(out)],
                   cwd=HERE, capture_output=True, timeout=600)
    return out


def ledger_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    with open(LEDGER, newline='') as fh:
        return list(csv.DictReader(fh))
