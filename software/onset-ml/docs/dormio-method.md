# The Dormio sleep-onset method, and what we took from it

Compiled 15 Sep 2026 from a full read of Horowitz, Cunningham, Maes & Stickgold 2020, "Dormio:
A targeted dream incubation device", *Consciousness and Cognition* 83:102938 (NIH manuscript
nihms-1635492, 28 pp.), the public openSleep code, and the sources the paper cites for its
physiology. Every claim below is tagged with where it comes from. Where a source could not be
read, it says so. Nothing here is inferred from memory.

## 1. What the paper says, exactly

**Section 2.2, Dormio Sleep-Onset Detection Algorithm (p. 6).**

- Goal: "detect the sleep onset period (SOP), without making claims of identifying specific Hori
  stages or the exact moment of sleep onset."
- Three sensors, "each ... sampled at 100hz": heart rate via "Adafruit's Pulse Sensor Amped on
  their middle finger"; muscle tone via "a voltage divider composed of a resistor and a 4.5"
  Sparkfun flex sensor wrapped around the middle finger"; EDA between two wrist electrodes.
- Calibration: "These three measures are averaged over the first 120 seconds when the subject
  initially lies down, and predefined deviations from these mean values are interpreted as
  Dormio-defined sleep onset."
- Thresholds: "Based on this pilot data [5 participants], Heart Rate (BPM) deltas of > 5 BPM,
  Electrodermal Activity sensor deltas > 4 µSemen or flexor muscle sensor deltas > 8 KΩ were
  taken as indications of sleep onset. Awakenings were performed whenever one of these thresholds
  was passed." That is OR logic across channels.
- Not stated: the window over which the live value is taken before subtracting the mean, whether
  the delta is signed or absolute, any hold or debounce, and how BPM is derived from the PPG.

**Section 2.4, protocol (p. 7).** After Dormio-detected onset "a variable timer was triggered.
This timer instigated wakeups from 1:00 to 5:00 minutes after Dormio-detected sleep onset".
Prompt: "You're falling asleep", then "Please tell me, what's going through your mind", then
"And were you asleep?" with answers 'Awake', 'Halfway' or 'Asleep', then "Remember to think of a
tree" and "You can fall back asleep now". Loop repeated for 45 minutes.

**Section 3.3, Sleep Physiology (pp. 10-11) and Figure 4 (p. 22).** Measures: change in HR, EDA
and flexion from the start of the session (lying down) to the first subjective report of
'Sleep' or 'Halfway', versus the change to the first report of 'Wake'. Sleep onset "averaged
10.3 ± 6.4 (S.D.) minutes". Threshold candidates: "the 80th percentile of 'Sleep' values, above
which subjects would be likely to report being awake, and the 20th percentile for 'Awake' values,
below which subjects would likely report being asleep."

| measure | Sleep mean ± SD | Wake mean ± SD | 80th pct Sleep | 20th pct Wake | provisional |
|---|---|---|---|---|---|
| HR (bpm) | −2.9 ± 22.9 | +10.0 ± 18.7 | 6 | −2 | > 5 |
| Flex (kΩ) | −6.7 ± 9.0 | +3.4 ± 6.3 (text) / −3.42 ± 6.32 (Fig. 4 caption) | −4 | −6 | > 8 |
| EDA (µS) | −4.0 ± 13.4 | +3.4 ± 8.6 | 5 | −1 | > 5 (table) / > 4 (§2.2) |

"Future studies will look at the predictive power of system thresholds combining these
presumptive values."

**Internal inconsistencies in the paper.** Calibration is 120 s in §2.2 but Figure 1 is drawn with
a "calibration period (3:00 min)". The EDA threshold is 4 µS in §2.2 and 5 µS in the table. The
flex Wake mean is +3.4 in the text and −3.42 in the Figure 4 caption. The HR Sleep mean is printed
"−+2.9" in the text; the caption gives −2.89.

**Limitations the authors state (p. 12).** No PSG: "This leaves us with little information as to
where participants were awoken within the range of the sleep-onset process, and means
experimenters must trust verbal reports with regards to sleep onset, which can be unreliable."

**Data analysis (§2.5).** Kruskal-Wallis H across the four conditions, post-hoc Mann-Whitney U
without Bonferroni. Incubation rate = percent of reports with a direct or indirect 'Tree'
reference per subject.

## 2. Paper versus the public code (openSleep, tomasero/openSleep, last commit March 2022)

Read by the research agent from the raw files. The code is not the 100 Hz, kΩ, 120 s system the
paper describes.

| parameter | paper | code |
|---|---|---|
| sampling | 100 Hz | ~10 Hz (RFduino sketch) / ~20 Hz (nRF52 sketch); HR maths assumes 10 Hz |
| units | bpm, kΩ, µS | raw 10-bit ADC counts everywhere, no conversion |
| calibration | 120 s (text), 3:00 (Fig. 1) | 300 s (`calibrationTime`), recalibrated after every prompt |
| HR threshold | > 5 bpm | `abs(lastHR − meanHR) ≥ 15` |
| flex threshold | > 8 kΩ | `abs(lastFlex − meanFlex) ≥ 50` counts |
| EDA threshold | > 4 / 5 µS | `abs(...) ≥ 10` counts |
| combination | any of three (OR) | newer FlowViewController: one detector chosen by a segmented control, default HBOS; older ViewController: OR |
| live value | not stated | mean of the last 30-sample upload (~1.5-3 s), checked every 3 s |
| direction | not stated | two-sided absolute value |
| post-onset timer | random 1-5 min | fixed 30 / 60 / 90 s by "dream stage" |
| which onsets sensed | all | first only; later ones on a 240 s timer |
| HR from PPG | not stated | 60 s ring buffer, peak when successive samples differ by > 50 counts, skip 4 |
| false-positive veto | not described | two fist squeezes within 5 s cancel the onset |

The HBOS prediction server (not in the paper) z-scores six features against session rows 60-120,
concatenates a population "awake" set, fits HBOS with contamination 0.05, and the app fires when
the rounded max score is ≥ 7. Its LF/HF uses a periodogram with fs = 1, so its "bands" are in
cycles per sample, not Hz.

## 3. What the cited evidence actually supports

Read by the research agent; access level noted.

- **Penzel 2003** (Eur Respir J, full text). An editorial on diagnosing sleep apnoea from HRV. Its
  only stage-related sentence: "Heart rate does change with sleep stages". Nothing on sleep onset.
- **Penzel & Kesper 2006** (Karger chapter). Could not be accessed.
- **Ogilvie 2001** (Sleep Med Rev, abstract): sleep entry is "a continuous, interwoven series of
  changes which begin in relaxed drowsiness and continue through stage 1, often into the first
  minutes of stage 2". Ogilvie, Wilkinson & Allison 1989 (abstract): behavioural response rates fall
  from W through stage 1 to 2, but "responding continued in both light 'sleep' stages".
- **Prerau et al. 2014** (PLoS Comput Biol, full text). Squeeze-a-ball-on-each-breath task with an
  FSR glove and EMG of flexor digitorum profundus. "EMG squeeze amplitudes decay until the correct
  responses stop entirely." Behavioural wakefulness continued "for another 5 minutes past the first
  epoch of Stage N1" in the worked case. No heart rate in the study.
- **Casagrande et al. 1997** (abstract). Finger-tapping task validated against PSG latency; no
  lag numbers.
- **Hori 1994 / Tanaka 1996** (abstracts). Hypnagogic core stages 4-8 last under 30 s each.
- **Carskadon & Dement** (chapter, full text): "The EMG may show a gradual diminution of muscle
  tonus as sleep approaches, but rarely does a discrete EMG change pinpoint sleep onset." Nothing
  on HR at NREM onset.
- **Kelly, Strecker & Bianchi 2012** (full text). Device review; contains no hand or flex method.
  The paper's citation of it for flex-based SOP detection is not supported by its content.
- **Nielsen 2017** (full text): 93.4% of signalled hypnagogic images arose from Hori sub-stages
  4/5; recall 90-98% for N1 and N2 onset awakenings.
- **Carrington et al. 2005** (J Appl Physiol, full text, not cited by Dormio): HR "declined by a
  total of 6.6 beats/min from presleep wakefulness to stable sleep", beginning before theta, minimum
  at stable stage 2; each arousal raised HR ~7.7 bpm (up to 21). Phase durations: lights-out to N1
  12.2 ± 12.5 min, N1 to N2 8.1 ± 6.9 min.
- **Shinar et al. 2006** (abstract, not cited): RR lengthens at onset, "Very-low-frequency power
  started to decrease significantly 2 min before sleep onset", LF fell, HF unchanged, LF/HF fell.
- **Trinder et al. 2001** (abstract): HF and LF "changed abruptly at sleep onset and was then
  constant" within each stage; no stage-1 data.
- No source reports RMSSD or SDNN at N1.

**Summary.** The flex channel is the best-grounded: muscle tone and behavioural responding decay
gradually across onset, and can trail the first N1 epoch by minutes. Heart rate falls about 6-7
bpm across the whole transition, spread over 20-30 minutes, and a single arousal reverses more
than that. A 5 bpm two-sided delta from a 120 s mean is therefore roughly the entire effect size
and will also fire on arousals. The paper's own Table shows HR Sleep and Wake distributions
overlapping (80th percentile of Sleep 6, 20th of Wake −2). The paper does not use HRV; the HRV
change with the strongest citation is a fall in LF/HF (and VLF) starting about two minutes before
EEG onset, which needs a window of at least 120 s to estimate.

## 4. What is now in the code

- `train.py`, `MODEL = 'dormio'`: score = max(|ΔHR| / 5 bpm, |Δflex| / 8 kΩ) where Δ is against
  the mean of the first 120 s; fires at 1.0 held for HOLD_S. HR over 15 s, flex over 10 s. Flex is
  converted to kΩ from the bench divider (10 kΩ to ground). `DORMIO_SIGNED` switches to
  decrease-only. `DORMIO_HRV = ('lfhf120', -1, 0.5)` adds the evidence-backed LF/HF fall as a third
  channel; off by default because the paper has none. EDA is not used, by decision.
- `train.py`, `MODEL = 'logreg'`: our learned combiner, kept for comparison in the harness.
- `thresholds.py`: reproduces §3.3 and Figure 4 from our captures. Per session, delta from the
  120 s mean to the first cue whose report was Asleep/Halfway versus the first reported Awake;
  prints Sleep/Wake mean ± SD, 80th percentile of Sleep, 20th percentile of Wake, next to the
  provisional thresholds. `--probe` also accepts probe-derived onsets as Sleep events and says so.
- `live.py` and `gui.html`: "And were you asleep?" (Awake / Halfway / Asleep, keys 1/2/3) after
  every cue, written as `report:*` notes; Halfway label (H); Dormio timing controls (random 1-5 min
  delay between detection and cue, default 0 for latency measurement); the header shows which
  detector is running.
- `onset/parse.py`: `label:halfway` and `report:*` notes.

Synthetic-data scores (12 onset + 4 awake, plumbing only): dormio 0.032, latency 28 s, no false
alarms, no misses; logreg 0.010, latency 8.7 s.

## 5. What this does not settle

1. The paper's thresholds were set on 5 pilot participants and characterised on 49 without PSG.
   No independent validation of the HR/flex/EDA detector against EEG was found. Bellaiche et al.
   2024 (Dormio Light) replaced the sensors with a timer and reports the same incubation rate.
2. The 2.2" Electrokit flex sensor on the bench is not the 4.5" Sparkfun part. Its kΩ swing over
   the same finger travel differs; the 8 kΩ value must be re-derived with `thresholds.py`.
3. The HR term needs real data to justify at all. Expect `thresholds.py` to show overlapping
   Sleep and Wake HR distributions, as the paper's own did.
4. Whether to add the LF/HF term is an empirical question the harness can answer once there are
   real sessions with reports.
