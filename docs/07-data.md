# 07 · Data

> **Nothing in this directory is committed.** Recorded sessions are personal physiological data,
> and the voice notes are dream reports. `data/` is gitignored and should stay that way.

<!-- ![Session review](images/session-review.png) -->

## What one session produces

```
data/sessions/
├── bench-20260921-020456.csv      every line the board sent
├── rssi-20260921-020456.csv       RSSI companion, if a separate board recorded it
├── session-20260921-020456.json   metadata, config, summary, voice note
├── voice-20260921-020456.wav      the spoken report — ground truth
├── voice-20260921-020456.txt      its transcript, with a self-describing header
├── ledger.csv                     one row per session, all sessions
└── dream-journal.md               every transcript in order, newest last
```

## Capture CSV

Header `host_time,type,fields...`, then one row per protocol line — the Unix timestamp the bridge
received it, followed by the raw line:

```csv
host_time,type,fields
1789514334.992,S,8570,238,468,490
1789514335.004,B,8600,72,830
1789514335.010,V,8600,3985,74,0,0,512,40
1789514336.120,N,onset
```

Labels are `N,<text>` rows written into the same file, so signals and marks can never drift apart.

Audio downloads land here too, as `A,` lines. That adds ~128 KB of base64 per voice note. It is
harmless and arguably good — the recording is archived inside the capture.

## Session JSON

```jsonc
{
  "stamp": "20260921-020456",
  "date": "2026-09-21T02:04:56",
  "start_host": 1789952696.6, "end_host": 1789981446.6,
  "cfg":     { /* the full detector and cue config as it ran */ },
  "summary": {
    "duration_min": 155.7,
    "onset_s": null,              // null = no ground truth in this session
    "first_detect_s": 232.76,
    "cues": 98,
    "reports": ["asleep"],
    "d_hr_bpm": -4.38,            // deviation at the first report
    "d_flex_kohm": -32.97,
    "dormio": { "calib_s": 120, "d_hr": 5.0, "d_flex": 8.0 },
    "cumulative": { /* the 80th/20th percentile table over all sessions */ }
  },
  "voice": { "wav": "...", "txt": "...", "text": "...", "engine": "...", "snr_db": 22.4 },
  "files": ["bench-….csv", "voice-….wav", "voice-….txt"]
}
```

> **`duration_min` is data time, not wall-clock time.** In the 21 Sep session the two differed by
> five and a half hours: the battery died at 04:41 and the session was not ended until 10:04. If
> you need wall-clock, use `end_host − start_host`, and check whether the stream actually ran that
> long.

## Loading a session

```python
from onset.parse import load            # → Session(t, fsr, ppg, beat_t, beat_ibi, beat_bpm, notes, rssi)
from onset.features import feature_row  # one causal row per second

s = load('data/sessions/bench-20260921-020456.csv')
print(s.t.shape, s.beat_t.size, s.notes[:5])
```

`Session` puts everything on a 100 Hz grid. `notes` is `[(t_seconds, text), …]`.

## Ledger

One row per session: stamp, name, date, duration, model, onset, first detection, first cue,
latency, cue count, report count, first report, ΔHR, Δflex, RSSI shift, notes. Enough to see the
shape of the whole corpus without opening a single capture.

## Regenerating derived data

The WAV and the capture are ground truth. Everything else can be rebuilt:

```bash
.venv/bin/python transcribe_notes.py --all --force   # transcripts and journal
.venv/bin/python session_view.py --data data/sessions # review pages
```

## Current corpus

Five real sessions, 21 Sep 2026, 1.7 to 155.7 minutes. **None has a labelled onset** — the probe
was never switched on. The shortest is below the harness's 3-minute floor. Treat the corpus as
plumbing validation, not evidence.
