# 04 · Data

> **Nothing in `data/` is committed.** Recorded sessions are personal physiological data and the
> voice takes are spoken reports. It is gitignored and should stay that way.

<!-- ![Session review](images/session-review.png) -->

## What one session produces

```
data/sessions/
├── bench-20260925-021530.csv      every line the board sent
├── session-20260925-021530.json   metadata, config, summary
├── voice-20260925-021530-1.wav    take 1
├── voice-20260925-021530-2.wav    take 2
├── voice-20260925-021530.txt      joined transcript
└── ledger.csv                     one row per session
```

## Capture CSV

Header `host_time,type,fields...`, then one row per protocol line — the Unix timestamp the bridge
received it, followed by the raw line unmodified:

```csv
host_time,type,fields
1789514334.992,S,8570,238,468,490
1789514335.002,R,8580,-57
1789514335.004,B,8600,72,830
1789514335.010,V,8600,3985,74,0,0,512,40
1789514336.120,G,9700,run,1204,2396,0
```

Signals, RSSI, session phase and labels all live in the same file on the same clock, which is the
whole point — nothing can drift out of alignment because nothing is in a separate file.

## The RSSI channel

`R,<ms>,<rssi>` at 50 Hz, measured by the board on each connection event, for the entire session.
This is the capture channel — the reason the device exists.

Each sample is paired with a session phase from the `G` line, so RSSI is labelled continuously:

| Label source | Gives you |
|:--|:--|
| `G` phase | `calib` / `run` / `report` — coarse state |
| Detector output | second-by-second threshold crossings |
| Cue events | the moment of each intervention |
| Wake cue | the verified end of the hypnagogic window |

```bash
.venv/bin/python rssi_report.py --session data/sessions/bench-20260925-021530.csv
```

writes an aligned 1 Hz export and a raw RSSI export per session.

> RSSI is a single scalar per connection event. **CSI** — per-subcarrier amplitude and phase —
> carries far more and is the intended direction, but is not exposed by this radio. The capture
> format treats RSSI as one channel among possible others, so a CSI source can be added without
> reworking anything downstream.

## Session JSON

```jsonc
{
  "stamp": "20260925-021530",
  "date": "2026-09-25T02:15:30",
  "start_host": 1789952696.6, "end_host": 1789956296.6,
  "cfg":     { /* the detector and cue config exactly as it ran */ },
  "summary": {
    "duration_min": 60.0,
    "calib_s": 120,
    "cues": 14,
    "rssi_samples": 180000,
    "takes": 2
  },
  "voice": { "wavs": ["…-1.wav", "…-2.wav"], "txt": "…", "text": "…", "engine": "…" },
  "files": ["bench-….csv", "voice-….wav", "voice-….txt"]
}
```

> `duration_min` is **data time**, not wall-clock. If the board stops early, the two diverge. For
> wall-clock use `end_host − start_host`, and check the stream actually ran that long.

## Loading a session

```python
from onset.parse import load

s = load('data/sessions/bench-20260925-021530.csv')
s.t          # seconds, 100 Hz grid
s.fsr        # flex counts
s.ppg        # raw PPG
s.rssi       # dBm, forward-filled onto the same grid
s.beat_t     # beat times
s.beat_ibi   # inter-beat intervals, ms
s.notes      # [(t_seconds, text), …]
```

Everything is on one grid, so any two channels can be sliced against each other directly.

## Regenerating derived data

The capture CSV and the WAVs are ground truth. Everything else rebuilds:

```bash
.venv/bin/python transcribe_notes.py --all --force    # transcripts
.venv/bin/python session_view.py --data data/sessions # review pages
.venv/bin/python rssi_report.py --all                 # RSSI exports
```
