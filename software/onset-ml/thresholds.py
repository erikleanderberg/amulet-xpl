#!/usr/bin/env python3
"""
Derive sleep-onset thresholds the way Horowitz et al. 2020 (sec. 3.3, Figure 4) did.

The paper: for every participant, the change in each measure between the start of the session
(the moment they lay down; the first 120 s mean) and the FIRST automated awakening whose
subjective report was 'Asleep' or 'Halfway' ("Sleep"), versus the change to the FIRST awakening
reported as 'Awake' ("Wake"). Threshold candidates: the 80th percentile of the Sleep deltas
(above which a report is likely 'Awake') and the 20th percentile of the Wake deltas (below which
a report is likely 'Asleep'). Their table, 49 participants:

    measure     provisional   80th pct Sleep   20th pct Wake
    HR          > 5 BPM       6 BPM            -2 BPM
    Flex        > 8 kOhm      -4 kOhm          -6 kOhm

This script reproduces that table from our captures. It needs sessions that contain cue notes
('haptic:...') followed by report notes ('report:asleep' | 'report:halfway' | 'report:awake'),
which the GUI writes after every fire. With --probe it will ALSO treat a probe-derived 'onset'
note as a Sleep event; that is our deviation from the paper and is labelled as such.

Channels: heart rate (bpm) and flex (kOhm) as in the paper, plus RMSSD (ms) which the paper does
not use, shown separately.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import train
from onset.features import FEATURES, build_features
from onset.parse import Session, load_dir

CHANNELS = [('hr', train.DORMIO_HR_FEATURE, 'bpm'), ('flex', train.DORMIO_FLEX_FEATURE, 'kOhm'),
            ('rmssd (not in paper)', 'rmssd60', 'ms'), ('rssi (not in paper)', 'rssi_mean10', 'dBm')]
REPORT_WINDOW_S = 300.0


def first_events(s: Session, use_probe: bool) -> dict:
    """{'sleep': t_cue | None, 'wake': t_cue | None} of the first cue with each report class."""
    notes = sorted(s.notes)
    out = {'sleep': None, 'wake': None}
    for i, (t, txt) in enumerate(notes):
        if not txt.startswith('haptic:'):
            continue
        rep = next((x for tt, x in notes[i + 1:] if x.startswith('report:') and tt - t <= REPORT_WINDOW_S), None)
        if rep is None:
            continue
        cls = 'wake' if rep == 'report:awake' else 'sleep'
        if out[cls] is None:
            out[cls] = t
    if use_probe and out['sleep'] is None and s.onset is not None:
        out['sleep'] = s.onset
        out['sleep_from_probe'] = True
    return out


def delta_at(times, X, col, t_event):
    calib = times <= train.CALIB_S
    base = np.nanmean(X[calib, col]) if calib.sum() else np.nan
    i = np.searchsorted(times, t_event, side='right') - 1
    return float(X[i, col] - base) if i >= 0 else np.nan


def compute(sessions, use_probe: bool = False) -> dict:
    """The paper's table as data: {channel: {n_sleep, n_wake, sleep_mean, sleep_sd, wake_mean, wake_sd, p80_sleep, p20_wake, unit}}."""
    rows = {c[0]: {'sleep': [], 'wake': []} for c in CHANNELS}
    n_probe = 0
    for s in sessions:
        ev = first_events(s, use_probe)
        if ev['sleep'] is None and ev['wake'] is None:
            continue
        n_probe += int(ev.get('sleep_from_probe', False))
        times, X = build_features(s)
        for name, feat, _ in CHANNELS:
            col = FEATURES.index(feat)
            for cls in ('sleep', 'wake'):
                if ev[cls] is not None:
                    d = delta_at(times, X, col, ev[cls])
                    if np.isfinite(d):
                        rows[name][cls].append(d)
    out = {'n_sessions': len(sessions), 'sleep_from_probe': n_probe, 'channels': {}}
    for name, _, unit in CHANNELS:
        S = np.array(rows[name]['sleep']); W = np.array(rows[name]['wake'])
        out['channels'][name] = dict(
            unit=unit, n_sleep=int(len(S)), n_wake=int(len(W)),
            sleep_mean=float(S.mean()) if len(S) else None, sleep_sd=float(S.std()) if len(S) else None,
            wake_mean=float(W.mean()) if len(W) else None, wake_sd=float(W.std()) if len(W) else None,
            p80_sleep=float(np.percentile(S, 80)) if len(S) else None,
            p20_wake=float(np.percentile(W, 20)) if len(W) else None)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', nargs='*', default=['data/sessions'])
    ap.add_argument('--probe', action='store_true', help='also use probe-derived onset notes as Sleep events (deviation from paper)')
    a = ap.parse_args()
    sessions = [s for d in a.data if Path(d).is_dir() for s in load_dir(d)]
    if not sessions:
        print('no sessions'); return
    rows = {c[0]: {'sleep': [], 'wake': []} for c in CHANNELS}
    n_probe = 0
    for s in sessions:
        ev = first_events(s, a.probe)
        if ev['sleep'] is None and ev['wake'] is None:
            continue
        n_probe += int(ev.get('sleep_from_probe', False))
        times, X = build_features(s)
        for name, feat, _ in CHANNELS:
            col = FEATURES.index(feat)
            for cls in ('sleep', 'wake'):
                if ev[cls] is not None:
                    d = delta_at(times, X, col, ev[cls])
                    if np.isfinite(d):
                        rows[name][cls].append(d)
    print(f'{len(sessions)} sessions; Sleep events from probe onset: {n_probe}' + (' (deviation from paper)' if n_probe else ''))
    print(f'{"measure":22s} {"n S/W":>7s} {"Sleep mean+-SD":>18s} {"Wake mean+-SD":>18s} {"80th pct Sleep":>15s} {"20th pct Wake":>14s}   provisional')
    prov = {'hr': f'> {train.DORMIO_DELTA_HR_BPM:g} bpm', 'flex': f'> {train.DORMIO_DELTA_FLEX_KOHM:g} kOhm'}
    for name, _, unit in CHANNELS:
        S = np.array(rows[name]['sleep']); W = np.array(rows[name]['wake'])
        fmt = lambda v: f'{np.mean(v):+.1f} +- {np.std(v):.1f}' if len(v) else '-'
        p80 = f'{np.percentile(S, 80):+.1f}' if len(S) else '-'
        p20 = f'{np.percentile(W, 20):+.1f}' if len(W) else '-'
        print(f'{name:22s} {len(S):3d}/{len(W):<3d} {fmt(S):>18s} {fmt(W):>18s} {p80:>15s} {p20:>14s}   {prov.get(name, "-")} ({unit})')
    print('\nPaper (49 participants): HR Sleep -2.9 +- 22.9, Wake +10.0 +- 18.7, 80th Sleep 6, 20th Wake -2;'
          ' Flex Sleep -6.7 +- 9.0, Wake +3.4 +- 6.3 (caption: -3.4), 80th Sleep -4, 20th Wake -6.')


if __name__ == '__main__':
    main()
