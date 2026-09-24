#!/usr/bin/env python3
"""
FIXED evaluation harness for the sleep-onset detector.  DO NOT MODIFY (see program.md).

Runs leave-one-session-out cross-validation over every session in the data dirs, using the
detector defined in train.py (build_features / fit / predict), and prints a summary block.

Ground truth per session: the 'onset' note (see onset/parse.py). Sessions with no onset are
awake-only controls and contribute false-alarm time only.

Scoring rules
  CALIB_S      first 120 s of every session are awake calibration; triggers there are ignored
  REFRACTORY_S triggers within 60 s of a previous trigger are the same event
  EARLY_TOL_S  a trigger up to 20 s before the labelled onset still counts as a detection
  LATE_MAX_S   a detection more than 180 s after onset counts as missed
  false alarm  any trigger event outside [onset - EARLY_TOL, onset + LATE_MAX], or any trigger
               in an awake-only session (after calibration)
  awake hours  time before onset (or whole session if none) minus calibration
  latency      first valid trigger time - onset (may be slightly negative)

composite score (lower is better):
  score = 0.45 * missed_frac
        + 0.35 * min(false_alarms_per_awake_hour, 6) / 6
        + 0.20 * clip(median_latency_s, 0, 180) / 180

Causality check: predict() is run on the full session and on the session truncated to 65 %;
the trigger series must be identical on the common prefix, otherwise the run fails.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

import train
from onset.parse import Session, load_dir

CALIB_S = 120.0
REFRACTORY_S = 60.0
EARLY_TOL_S = 20.0
LATE_MAX_S = 180.0
DATA_DIRS = ['data/sessions', 'data/synthetic']


def trigger_events(times: np.ndarray, trig: np.ndarray) -> list[float]:
    ev: list[float] = []
    for tt in times[trig.astype(bool)]:
        if tt < CALIB_S:
            continue
        if not ev or tt - ev[-1] >= REFRACTORY_S:
            ev.append(float(tt))
    return ev


def truncate(s: Session, frac: float) -> Session:
    n = int(len(s.t) * frac)
    tmax = s.t[n - 1]
    m = s.beat_t <= tmax
    return replace(s, t=s.t[:n], fsr=s.fsr[:n], ppg=s.ppg[:n], rssi=None if s.rssi is None else s.rssi[:n],
                   beat_t=s.beat_t[m], beat_ibi=s.beat_ibi[m], beat_bpm=s.beat_bpm[m],
                   notes=[(t, x) for t, x in s.notes if t <= tmax])


def score_session(s: Session, events: list[float]) -> dict:
    onset = s.onset
    out = dict(name=s.name, onset=onset, events=events, detected=None, latency=np.nan, false_alarms=0)
    if onset is None:
        out['awake_h'] = max(s.duration - CALIB_S, 0) / 3600
        out['false_alarms'] = len(events)
        return out
    out['awake_h'] = max(onset - CALIB_S, 0) / 3600
    for e in events:
        if e < onset - EARLY_TOL_S:
            out['false_alarms'] += 1
        elif out['detected'] is None and e <= onset + LATE_MAX_S:
            out['detected'] = e
            out['latency'] = e - onset
    return out


def composite_score(missed_frac: float, fa_ph: float, med_latency: float) -> float:
    lat = 0.0 if np.isnan(med_latency) else float(np.clip(med_latency, 0, 180))
    return 0.45 * missed_frac + 0.35 * min(fa_ph, 6) / 6 + 0.20 * lat / 180


def run(sessions: list[Session], verbose: bool = True) -> dict:
    t0 = time.time()
    short = [s.name for s in sessions if s.duration < CALIB_S + 60]
    if short:
        print(f'skipping {len(short)} session(s) shorter than {CALIB_S + 60:.0f} s: {", ".join(short)}')
        sessions = [s for s in sessions if s.duration >= CALIB_S + 60]
    feats = [train.build_features(s) for s in sessions]
    results = []
    fit_secs = 0.0
    for i, s in enumerate(sessions):
        tr = [(sessions[j], feats[j][0], feats[j][1]) for j in range(len(sessions)) if j != i]
        ft = time.time()
        model = train.fit(tr)
        fit_secs += time.time() - ft
        times, X = feats[i]
        trig = np.asarray(train.predict(model, times, X, s)).astype(bool)
        # causality check
        s2 = truncate(s, 0.65)
        t2, X2 = train.build_features(s2)
        trig2 = np.asarray(train.predict(model, t2, X2, s2)).astype(bool)
        k = min(len(trig2), len(trig))
        if not np.array_equal(trig[:k], trig2[:k]):
            print(f'CAUSALITY VIOLATION in session {s.name}: predictions change when the future is removed')
            sys.exit(2)
        r = score_session(s, trigger_events(times, trig))
        results.append(r)
        if verbose:
            det = f'{r["latency"]:+.0f} s' if r['detected'] is not None else ('miss' if r['onset'] is not None else '-')
            print(f'  {s.name:28s} onset={str(None if r["onset"] is None else round(r["onset"])):>5s}  '
                  f'detect={det:>7s}  false_alarms={r["false_alarms"]}')
    with_onset = [r for r in results if r['onset'] is not None]
    missed = sum(1 for r in with_onset if r['detected'] is None)
    missed_frac = missed / len(with_onset) if with_onset else 0.0
    awake_h = sum(r['awake_h'] for r in results)
    fa = sum(r['false_alarms'] for r in results)
    fa_ph = fa / awake_h if awake_h > 0 else 0.0
    lats = [r['latency'] for r in with_onset if r['detected'] is not None]
    med_lat = float(np.median(lats)) if lats else np.nan
    score = composite_score(missed_frac, fa_ph, med_lat)
    summary = dict(score=score, latency_s=med_lat, false_alarms_ph=fa_ph, missed_frac=missed_frac,
                   n_missed=missed, n_false=fa, fit_seconds=fit_secs, total_seconds=time.time() - t0,
                   n_sessions=len(sessions))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--data', nargs='*', default=DATA_DIRS)
    a = ap.parse_args()
    sessions = []
    for d in a.data:
        if Path(d).is_dir():
            sessions += load_dir(d)
    if not sessions:
        print('no sessions found'); sys.exit(1)
    if a.list:
        for s in sessions:
            print(f'{s.name:28s} {s.duration/60:5.1f} min  onset={s.onset}  beats={len(s.beat_t)}')
        return
    summ = run(sessions)
    print('---')
    for k in ('score', 'latency_s', 'false_alarms_ph', 'missed_frac', 'n_missed', 'n_false', 'fit_seconds', 'total_seconds', 'n_sessions'):
        v = summ[k]
        print(f'{k + ":":18s}{v:.4f}' if isinstance(v, float) else f'{k + ":":18s}{v}')


if __name__ == '__main__':
    main()
