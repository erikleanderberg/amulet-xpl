# 06 · Running a session

The operator runbook. Read it once before the first session; after that the checklist is enough.

## Before you lie down

**1 · Start the stack.**

```bash
cd software/onset-ml
.venv/bin/python tools/bench_bridge.py    # terminal 1
.venv/bin/python live.py                  # terminal 2
open http://localhost:8790
```

**2 · Click anywhere on the page.** The header says *click to enable audio* until you do. Until
then the browser blocks the probe tone and you have no ground truth. This is the easiest way to
waste a session.

**3 · Fit the sensors, then look at the traces.** Flex strip under the relaxed index finger, PPG
on the middle finger. Watch until the PPG trace is a clean repeating wave and the BPM is
believable. No finger on the sensor idles flat at ~470.

**4 · Fire one test cue.** Press `F` while sitting up. You should feel a 150 ms tap. If you cannot
feel it now you will not feel it asleep. Dismiss the report box with `1`.

**5 · Choose your audio route.** The probe is a faint 1 kHz blip. Headphones you might roll onto
will fail silently; laptop speakers at low volume are more robust.

**6 · Check the battery pill.** It reads percent and volts in the header. A full 120 mAh cell gives
about 7 hours of streaming — enough for a nap, marginal for a whole night.

## The session

| Step | Action |
|:--|:--|
| 1 | Type a name, press **Start session**. Calibration restarts from zero |
| 2 | Lie still and **stay awake for 2 minutes**. Hand relaxed and still — a fidget here poisons the baseline every later reading is scored against |
| 3 | Press `A` once to label yourself awake |
| 4 | Turn **Probe** on. Press `SPACE` at every faint tone. Two consecutive misses auto-marks `onset` |
| 5 | Press `X` to arm the cue. It cannot fire during calibration |
| 6 | When it fires, answer **"were you asleep?"** with `1` awake / `2` halfway / `3` asleep |
| 7 | **Double-click the glove button** to end the session and record your spoken report |

### Keys

`SPACE` probe · `A` `D` `H` `S` label awake/drowsy/halfway/asleep · `X` arm · `F` fire now ·
`R` reset calibration · `1` `2` `3` answer the report box

## After

Files land in `data/sessions/` under one stamp: the capture CSV, an RSSI companion if there is one,
a session JSON, a voice WAV and its transcript `.txt`. A row is appended to `ledger.csv` and the
dream journal gets a dated entry.

```bash
.venv/bin/python session_view.py --data data/sessions   # the review page, also at /review
.venv/bin/python evaluate.py                            # score the detector
.venv/bin/python thresholds.py --probe                  # the paper's table, on your data
```

## What to expect the first few times

The detector has never seen you. It runs the paper's rule — fire when heart rate moves more than
5 bpm **or** grip moves more than 8 kΩ from your own baseline. On synthetic data that scored well.
On a real hand it will probably fire early, and PPG amplitude is contact-sensitive, so a finger
shifting on the sensor looks a great deal like sleep onset.

That is fine. Early sessions are for **ground truth**, not for the detector being right. Your probe
misses and your 1/2/3 answers are the data. Whether the detector agreed is something you find out
afterwards.

## Rules that are not optional

> **Run the probe.** A session without it has no labelled onset and cannot train anything. Five
> sessions have been recorded this way already; none of them can be used to validate the detector.

> **Go past 3 minutes.** `evaluate.py` skips any session shorter than calibration + 60 s.

> **Do not run a nap protocol overnight.** Calibration, probe, refractory and cue budget are all
> designed for a 20–40 minute nap. An 8-hour run needs its own mode: no probe, a drift-tolerant
> baseline, a cue budget, and morning recall as the only report. Running the nap protocol all
> night is what produced 98 cues and no usable data on 21 Sep.
