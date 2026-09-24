# autoresearch: Amulet sleep-onset detector

An agent runs its own experiment loop on a small-data sleep-onset detector. CPU only,
numpy / scipy / scikit-learn, one experiment finishes in about a minute. The spirit and
guardrails follow karpathy/autoresearch; the metric and the loop are adapted below.

## Setup

1. Agree on a run tag with the user, based on the date (e.g. `sep14`). The branch
   `autoresearch/<tag>` must not already exist.
2. `git checkout -b autoresearch/<tag>` from main.
3. Read the in-scope files: `README.md`, `evaluate.py` (fixed harness), `train.py` and
   `onset/features.py` (the two files you may edit), `onset/parse.py` (data format, read only).
4. `.venv/bin/python evaluate.py --list` must print every session. If it errors, tell the user.
5. Create `results.tsv` with only the header row if it does not exist.
6. Confirm with the user, then start the loop and do not check back.

## Experimentation

One experiment: `.venv/bin/python evaluate.py > run.log 2>&1`

The harness imports `build_features`, `fit`, `predict` from `train.py`, runs
leave-one-session-out cross-validation over every session in `data/sessions` (real) and
`data/synthetic`, and prints a block ending in:

```
---
score:            0.0947
latency_s:        29.7250
false_alarms_ph:  1.0571
missed_frac:      0.0000
...
```

`score` is the ground-truth metric, lower is better: a fixed composite of missed onsets,
false alarms per awake hour, and median detection latency, defined in
`evaluate.py::composite_score`. Never re-derive or re-weight it in train.py.

**You CAN edit:** `train.py` and `onset/features.py`. Features, windows, model family,
calibration, thresholds, smoothing, hysteresis, refractory logic: all fair game. Learned
models (logistic regression, HBOS-style histograms, isolation forest, gradient boosting) go
in `fit()` and must only see the training sessions they are given.

**You CANNOT:**
- edit `evaluate.py`, `onset/parse.py`, `onset/simulate.py`, or anything under `data/`
- install packages; only what `pyproject.toml` pins
- make `predict` non-causal (the harness truncates the session and compares; a mismatch
  aborts the run with exit code 2 and counts as a crash)
- tune anything against a session by name

**Runtime** is a soft constraint: aim for under 60 s. Kill a run over 3 minutes and log it
as a crash.

**Simplicity criterion:** all else equal, simpler is better. A 0.002 gain from 30 lines of
special cases is not worth it. The same gain from deleting a feature is. Equal score with
less code: keep.

**The first run** is always the baseline, `train.py` exactly as it is.

**Real data first.** Synthetic sessions exist so the pipeline can be tested; they are a
caricature. When `data/sessions` holds real captures, the score on real sessions is what
matters. If a change helps synthetic but hurts real, it is a discard.

## Logging

Append one row per run to `results.tsv` (tab separated, never commas):

```
commit	score	latency_s	fa_per_h	missed	status	description
```

`0.0000` for numbers on a crash. `status` is `keep`, `discard`, or `crash`. Extract with

```
grep "^score:\|^latency_s:\|^false_alarms_ph:\|^missed_frac:" run.log
```

Do not commit `results.tsv`; leave it untracked.

## The loop

LOOP FOREVER:

1. Check git state: branch and commit.
2. Change `train.py` and/or `onset/features.py` with ONE idea.
3. `git commit -am "<short description>"`
4. `.venv/bin/python evaluate.py > run.log 2>&1` (never tee, never print the whole log)
5. `grep` the summary lines as above.
6. Empty grep = crash: `tail -n 40 run.log`; fix if trivial (typo, shape, import) and
   re-run; otherwise log `crash` and move on. Give up on an idea after three attempts.
7. Append the row to `results.tsv`.
8. If `score` is strictly lower by at least 0.002, or equal with simpler code: keep, the
   branch advances.
9. Otherwise `git reset --hard HEAD~1`.

Treat gains under 0.002 as noise: leave-one-session-out on a dozen sessions is noisy.

**Ideas when stuck:** re-read `evaluate.py` for exactly how onsets are matched and what
counts as a false alarm; find which sessions drive misses and false alarms; trade latency
against false alarms with threshold, hold, and smoothing; a learned combiner instead of the
hand weights; per-feature robust scaling (MAD) instead of SD; a signal-quality gate; a
second-stage confirmation window; different HRV windows.

**NEVER STOP.** Do not ask "should I keep going?" The user may be asleep and expects the
loop to run until interrupted.
