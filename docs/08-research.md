# 08 · Research notes

What the design rests on, and — just as important — what it does not.

## Dormio, and what it actually is

Horowitz et al. 2020, *Dormio: A targeted dream incubation device* (MIT Media Lab). The core
method is **deviation from the wearer's own awake baseline**, not sleep-stage classification.

What we took, §2.2: a 120 s calibration window assumed awake, then fire on
`|ΔHR| > 5 bpm` **or** `|Δflex| > 8 kΩ`. OR logic. `thresholds.py` reproduces §3.3 — deriving
those thresholds from the wearer's own subjective reports (80th percentile of Sleep, 20th
percentile of Wake).

Three things worth knowing about the source:

- **The openSleep repository contains no training set.** Three labelled recordings of 30–85 s
  (`maggieasleep`, `awake`, `awake2`, format `flex,hr,eda`, 10-bit pipe-separated), an opaque
  `awake.pkl`, and an unreferenced random forest. There is nothing there to train on.
- **The paper's own citations for the heart-rate claim do not contain that claim.** The flex
  channel is the well-grounded one; treat the 5 bpm figure as a working number, not a result.
- **The paper's Figure 1 draws a 3-minute calibration while the text says 2.** We follow the text.
  `CALIB_S` is 120 in both `evaluate.py` and `train.py` and they must not diverge.

## Physiology

From a September 2026 literature memo, in rough order of usefulness at sleep onset:

| Signal | Behaviour at onset | Verdict |
|:--|:--|:--|
| **Grip / muscle tone** | Collapses early and unmistakably | The primary channel |
| **PPG amplitude** | Rises — distal vasodilation | Early, but contact-sensitive |
| **Heart rate** | Falls a few bpm over 1–3 min | Slow but real |
| **Breathing regularity** | Becomes irregular in N1 | Derivable from PPG |
| **RMSSD / HF power** | Barely move at N1; they rise in N2 | Too late to be useful |
| **EDA** | Storms are far too slow | **Removed entirely, 15 Sep 2026** |

Only one HRV term has evidence behind it for this purpose: a fall in LF/HF over ≥ 120 s. It is
wired as an option and off by default.

## Ground truth

The **Ogilvie behavioural probe**: a faint tone every 16–30 s, acknowledged with a key. Two
consecutive misses marks onset. It is the only ground truth available without EEG, and optional
EEG (a Muse S on a subset of sessions) remains the obvious upgrade.

## Haptics

TITAN Drake HF linear resonant actuator, f₀ 160 Hz, 5–300 Hz usable band, 145 mA rms rated.

Carried over from an earlier campaign (`amulet-haptics`, Aug 2026) — **the methods, not that
campaign's numbers**, because its amplifier was mis-clocked or shut down for most of it and its
late "160 Hz resonance" was measured by gating the amp's idle switching rather than audio.

What is worth reusing:

- **On-board vibration measurement.** LSM6DS3 at 1.66 kHz, AC-coupled, Goertzel lock-in at the
  drive frequency, interleaved ON/OFF windows scored as an AUC. Use it to find the *mounted*
  resonance on the actual glove, and to log a measured g per cue so "cue delivered" is a fact.
- **Actuator model.** Q ≈ 6.3, mechanical τ ≈ 12.6 ms, 10–90 % envelope rise 28 ms. Bursts under
  ~25 ms never reach full amplitude. A downward carrier sweep 160 → 120 Hz loses roughly half the
  acceleration to the response curve alone.
- **Drive rules.** Supply sets the ceiling. Tie SD to Vin. Stop the I²S clock when idle rather
  than writing zeros. Service the buffer every ~16 ms. Never park a tone at resonance.
- **Open question.** Whether the bench actuator is the MF (130 Hz) or HF (160 Hz) variant. The
  notes disagree. An IMU sweep settles it in five minutes.

## Speech recognition

Transcription happens after the session, so latency is nearly irrelevant and accuracy on difficult
audio is everything. Two findings drove the design:

- **Whispered speech breaks ASR.** It has no fundamental frequency and no harmonic structure.
  Published 2026 figures put Whisper-v3 at **18.93 % CER on whispered speech against 3.95 % on
  normal speech**. The mitigation that matters is behavioural: murmur, do not whisper.
- **Whisper-family models hallucinate over silence**, emitting training-set subtitle text. For a
  research corpus this is the worst possible failure, because it is invisible. Every clip therefore
  passes an energy VAD first and a silent clip is recorded as `no_speech`.

Apple's on-device SpeechAnalyzer (macOS 26+) was measured at 2.12 % WER on LibriSpeech clean
against Whisper Small's 3.74 %, at roughly 3× the speed, with no model download and nothing
leaving the machine. It is the default backend.

## The experiment loop

The threshold is not chosen; it is derived, and the derivation is kept honest by keeping the
referee fixed.

```
evaluate.py         FIXED. Leave-one-session-out CV. Composite of misses,
                    false alarms per awake hour, and detection latency.
train.py            editable
onset/features.py   editable
results.tsv         the log: commit, score, latency, FA/h, misses, keep|discard, why
program.md          the rulebook an agent or a person follows
```

Eight experiments are logged so far. They took the composite from 0.095 to 0.010 and median
latency from 30 s to 8.7 s — **entirely on 12 synthetic naps and 4 synthetic awake controls**.
Real recorded sessions with ground truth: **zero**. Every one of those numbers is a plumbing
test, and the synthetic generator is a caricature with no motion artefacts, no contact loss and
no PPG drift.

## Open questions

1. Re-tune hold, smoothing and threshold on real data. They were shortened against synthetic data
   where there was no false-alarm pressure. Expect them to lengthen.
2. PPG amplitude dominates the combiner and is motion- and contact-sensitive. It needs a
   grip-must-agree second stage, gated on `beat_ok30`.
3. Mask features for ~10 s after every cue. The cue perturbs the signals and the detector does not
   currently know that.
4. Retire or improve the simulator once real sessions outnumber synthetic ones.
5. Normalise per person, share the combiner, set the threshold per person. Test on two people
   before the study.
