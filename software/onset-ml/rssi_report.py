#!/usr/bin/env python3
"""
RSSI around the sleep-onset labels, and the aligned export the study needs to keep.

For every session (bench capture + its companion rssi-*.csv, aligned on the laptop clock):
  1. prints RSSI mean / SD / slope in windows around each label event
     (onset, detected, haptic:*, report:*, label:*), so the fluctuation at hypnagogia is read
     directly against the ground truth the GUI produced;
  2. writes data/exports/<session>-aligned.csv at 1 Hz with every channel and the labels, plus
     data/exports/<session>-rssi-raw.csv with every RSSI sample (host_time, t, dBm) untouched.

Nothing here decides anything. It is the record.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from onset.features import FEATURES, build_features
from onset.parse import FS, Session, load_dir

WINDOWS = [(-60, -30), (-30, 0), (0, 30), (30, 60), (60, 120)]
EVENT_PREFIXES = ('onset', 'detected', 'haptic:', 'report:', 'label:')


def win_stats(s: Session, t0: float, a: float, b: float):
    i0, i1 = int(max(0, (t0 + a) * FS)), int(min(len(s.t), (t0 + b) * FS))
    if s.rssi is None or i1 <= i0:
        return None
    x = s.rssi[i0:i1]
    m = np.isfinite(x)
    if m.sum() < FS:
        return None
    xs, ts = x[m], s.t[i0:i1][m]
    slope = np.polyfit(ts, xs, 1)[0] if m.sum() > 3 else np.nan
    return float(xs.mean()), float(xs.std()), float(slope)


def export(s: Session, out: Path) -> None:
    times, X = build_features(s)
    state = s.state_at(times)
    cols = ['t_s', 'host_time', 'state', 'fsr_adc', 'flex_kohm', 'hr_bpm', 'ppg_amp', 'rssi_dbm', 'rssi_sd', 'notes']
    fi = {n: FEATURES.index(n) for n in ('fsr_mean10', 'flex_kohm10', 'hr_mean15', 'ppg_amp10', 'rssi_mean10', 'rssi_std10')}
    notes_by_sec: dict[int, list[str]] = {}
    for t, txt in s.notes:
        notes_by_sec.setdefault(int(t), []).append(txt)
    with open(out / f'{s.name}-aligned.csv', 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(cols)
        for k, tn in enumerate(times):
            r = X[k]
            w.writerow([f'{tn:.0f}', f'{s.host_t0 + tn:.3f}', int(state[k]),
                        *[('' if not np.isfinite(r[fi[n]]) else f'{r[fi[n]]:.2f}') for n in ('fsr_mean10', 'flex_kohm10', 'hr_mean15', 'ppg_amp10', 'rssi_mean10', 'rssi_std10')],
                        ' | '.join(notes_by_sec.get(int(tn), []))])
    if s.rssi is not None:
        with open(out / f'{s.name}-rssi-raw.csv', 'w', newline='') as fh:
            w = csv.writer(fh); w.writerow(['t_s', 'host_time', 'rssi_dbm'])
            prev = None
            for i, v in enumerate(s.rssi):
                if np.isfinite(v) and v != prev:            # forward-filled grid: write changes only = original samples
                    w.writerow([f'{i / FS:.2f}', f'{s.host_t0 + i / FS:.3f}', int(v)]); prev = v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', nargs='*', default=['data/sessions'])
    ap.add_argument('--out', default='data/exports')
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    sessions = [s for d in a.data if Path(d).is_dir() for s in load_dir(d)]
    if not sessions:
        print('no sessions'); return
    for s in sessions:
        has = s.rssi is not None and np.isfinite(s.rssi).any()
        print(f'\n== {s.name}  {s.duration/60:.1f} min  rssi: {"yes" if has else "NO COMPANION"}')
        if has:
            x = s.rssi[np.isfinite(s.rssi)]
            print(f'   whole session: mean {x.mean():.1f} dBm  sd {x.std():.2f} dB  min {x.min():.0f}  max {x.max():.0f}')
            for t, txt in s.notes:
                if not txt.startswith(EVENT_PREFIXES):
                    continue
                parts = []
                for a_, b_ in WINDOWS:
                    st = win_stats(s, t, a_, b_)
                    parts.append(f'[{a_:+d},{b_:+d}] ' + ('-' if st is None else f'{st[0]:.1f}±{st[1]:.1f} ({st[2]*60:+.1f} dB/min)'))
                print(f'   {t:7.1f}s {txt:24s} ' + '  '.join(parts))
        export(s, out)
    print(f'\nexports in {out}/')


if __name__ == '__main__':
    main()
