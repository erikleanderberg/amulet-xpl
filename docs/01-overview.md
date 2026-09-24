# 01 · Overview

## The idea

At sleep onset the mind produces hypnagogia: loose, vivid, associative imagery that is unusually
easy to steer and almost impossible to remember. The Dormio work at MIT Media Lab showed you can
interrupt that state deliberately — detect onset, wake the sleeper just enough to speak, capture
the report, let them drop back — and repeat it for as many cycles as you like.

Amulet XPL is a glove that does this without an EEG.

## What it measures, and why

| Channel | Sensor | Why it is here |
|:--|:--|:--|
| **Grip / flex** | 2.2″ flex strip, index finger | Muscle tone collapse is the earliest and most reliable onset sign available without EEG. This is the well-grounded channel. |
| **PPG** | Pulse Sensor Amped, middle finger | Heart rate falls a few bpm over 1–3 min at onset; distal vasodilation raises pulse amplitude. |
| **Link RSSI** | The BLE radio itself | Free. Body position and gross movement modulate it. Exploratory. |
| **Voice** | On-board PDM mic | The dream report. The actual output of the experiment. |
| **Battery** | VBAT divider | So an overnight run's end is a recorded fact, not an inference. |

EDA was removed on 15 Sep 2026: skin-conductance responses are far too slow for a detector that
must fire within tens of seconds.

## How detection works

The detector does not know what sleep looks like. It knows what **you** look like awake, and
notices when you stop looking like that.

1. **Calibrate.** The first 120 s of a session are assumed awake. Every feature's mean and standard
   deviation over that window become the personal baseline.
2. **Score.** Once a second, ~22 causal features are computed over trailing windows and expressed
   as deviations from that baseline.
3. **Decide.** The default model is the Dormio rule from Horowitz et al. 2020 §2.2: fire when
   `|ΔHR| > 5 bpm` **or** `|Δflex| > 8 kΩ`. A logistic-regression combiner also exists.
4. **Hold and fire.** The score must stay over threshold for 8 s. After firing, a 60 s refractory
   window blocks another cue.
5. **Report.** The cue is a 150 ms tap. The wearer answers "were you asleep?" (awake / halfway /
   asleep) and speaks a dream report, which is transcribed on-device.

The threshold is not a number anyone picked. It falls out of an experiment loop: a fixed scoring
harness (`evaluate.py`), one editable detector (`train.py`, `onset/features.py`), a log
(`results.tsv`), keep or revert. See [08 · Research notes](08-research.md).

## Ground truth without EEG

The **Ogilvie behavioural probe**: a faint tone every ~20 s that the wearer acknowledges with the
space bar. Two consecutive misses marks sleep onset. It is the only ground truth this device has.

> ⚠️ **The probe has never been switched on in a recorded session.** Every detector figure quoted
> anywhere in this repo comes from synthetic data. Until sessions are recorded with the probe
> running, the detector's real-world accuracy is unknown.

## Where it is going

The device is being trained on one person before a study in Warsaw. The path is: record 15–20
real sessions with probe ground truth → re-derive thresholds on real data → validate on a second
wearer → run the study.
