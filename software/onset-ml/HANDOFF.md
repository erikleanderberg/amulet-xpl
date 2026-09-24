# Onset Lab handoff

Written 14 Sep 2026 for the engineer taking over `~/Documents/Amulet XPL/onset-ml/`.

## The 30-second version

We are building a Dormio-style glove. It does not classify sleep. It notices when the wearer
stops looking like their own awake self, then fires a haptic cue on the wrist so they surface
into hypnagogia and report what they saw.

Three layers:

1. **Firmware** (XIAO nRF52840, `bench3_live`) streams raw samples over USB serial: grip
   force (FSR) and PPG waveform at 100 Hz, detected heartbeats, EDA. No intelligence on the board.
2. **Laptop detector** (`live.py` + `train.py`) computes features once a second, z-scores them
   against the wearer's first two minutes awake, and combines them with a small logistic
   regression. When the score stays over threshold for 8 s, it fires.
3. **Cue** is a "decaying beep": a burst train on the Drake LRA that starts fast and strong
   and slows and fades. Fully parameterised, with an energy guard, in `onset/haptic.py`.

The threshold is not a number we pick. It falls out of an experiment loop: a fixed scoring
harness (`evaluate.py`), one editable detector (`train.py` and `onset/features.py`), a log
(`results.tsv`), keep or revert. An agent or a person runs it; `program.md` is the rulebook.

## What exists, and what it has been tested on

| piece | file | state |
|---|---|---|
| capture parser | `onset/parse.py` | done; labels are `N,` note lines inside the capture |
| synthetic naps | `onset/simulate.py` | done; a caricature, for plumbing only |
| features, 20 of them, causal, 1 Hz | `onset/features.py` | done; one known bug (below) |
| detector | `train.py` | logistic regression on z-scored features; tuned on synthetic only |
| scoring harness | `evaluate.py` | done; leave-one-session-out, checks causality |
| experiment loop | `program.md`, `results.tsv` | ran 8 experiments |
| live server + GUI | `live.py`, `gui.html`, `Onset Lab.command` | done; verified on replay |
| haptic pattern | `onset/haptic.py` | schema, presets, guard, preview; firmware side not built |

Numbers, all on 12 synthetic naps + 4 synthetic awake controls:

| | baseline | after 8 experiments |
|---|---|---|
| score (lower is better) | 0.095 | 0.010 |
| median latency after onset | 30 s | 8.7 s |
| false alarms per awake hour | 1.1 | 0 |
| missed onsets | 0 | 0 |

**Real recorded sessions: zero.** Treat every number above as a plumbing test, not a result.

## Demo script (5 minutes)

```
cd ~/Documents/Amulet\ XPL/onset-ml
.venv/bin/python live.py --replay data/synthetic/synth-onset-03.csv --speed 4
open http://localhost:8790
```
Point at: signals, the score climbing at the onset mark, the z table (which feature is
moving), the pattern editor and Preview (audio), the probe. Then
`.venv/bin/python evaluate.py` to show the harness output and `cat results.tsv`.

## Gaps to fill

### P0, before any threshold means anything

1. **Record real naps.** The self-training protocol is in `README.md`: bridge running, Record,
   two minutes awake, probe on (faint tone every 20 s, SPACE to answer, two misses = onset
   mark). Captures land in `~/Documents/Arduino/captures/`; copy to `data/sessions/`. Aim for
   15 to 20 sessions before trusting anything.
2. **Firmware pattern playback.** Bench3 only understands single-character commands. Needed: a
   `bench4` that parses the `DB k=v ...` line (`onset/haptic.py::serial_line`), renders the burst
   train into the I2S buffer, enforces the same energy guard on-device, and a `/line` endpoint in
   `bench_bridge.py`. Until then, "fire" can only send the old 150 ms tap.
3. **Session protocol.** Dormio runs cycles: calibrate, wait for onset, cue, prompt, record the
   verbal report, recover, repeat N times. `live.py` only fires once per 60 s refractory window.
   No cycle count, no prompt audio, no report recording, no timer fallback.

### P1, once real data exists

4. **Re-tune hold, smoothing, threshold.** They were shortened on synthetic data where there
   was no false-alarm pressure. Expect them to lengthen.
5. **Pulse amplitude dominates the combiner.** On a real finger it is contact- and
   motion-sensitive. Add a grip-must-agree second stage and gate on `beat_ok30`.
6. **Simulator fidelity or retirement.** Add motion artefacts, contact loss, PPG drift, or
   drop synthetic sessions from the harness once real ones outnumber them.
7. **Bug: `fsr_dropout30`** anchors its floor on the row's own start, so it never trips.
   Anchor it to the calibration grip.
8. **Calibration length.** Harness and detector use 120 s; Dormio used 300 s. Pick one; it
   must match in `evaluate.py` and `train.py`.

### P2, for the Warsaw study

9. **BLE transport.** The bench is USB serial. The glove will advertise Nordic UART Service.
   Only `bench_bridge.py` should change; `live.py` reads the bridge's event stream.
10. **More than one wearer.** The recipe is normalise per person, share the combiner, set
    threshold per person. Test on at least two people before the study.
11. **Ground truth beyond the probe.** Optional Muse S on a subset of sessions.
12. **Engineering hygiene.** No unit tests. Feature cache (`data/.feat_cache`) is keyed on file
    mtimes. Wire format has a sequence counter only on F lines; dropped packets hold last value.

## Words he will hear

- **onset**: the labelled instant sleep began (probe rule or a manual mark). Ground truth.
- **calibration**: the first N seconds of a session, assumed awake; the personal baseline.
- **z-score**: how many baseline standard deviations a feature has moved.
- **hold**: seconds the score must stay above threshold before firing.
- **refractory**: seconds after a fire during which it cannot fire again.
- **probe**: Ogilvie's method; a faint tone you answer with a key. Missing two = asleep.
- **LOSO**: leave-one-session-out cross validation; fit on all but one, score the one.
- **capture**: one CSV from the bridge; signals and labels in the same file.

## Reuse from the earlier Drake work (`~/conductor/workspaces/amulet-haptics`, Aug 2026)

Take the methods and the safety rules, not the measured numbers: that rig's amp was
mis-clocked or shut down for most of the campaign, and its late "160 Hz resonance" was
measured by gating the amp's idle switching, not audio. The bench3 board today is felt with
the clock configuration that repo claims silenced the coil.

Worth lifting directly:
- **On-board vibration meter.** `drake_sense` / `drake_validate`: onboard LSM6DS3 at 1.66 kHz,
  AC-coupled, Goertzel lock-in at the drive frequency, interleaved ON/OFF windows scored as an
  AUC. Use it to (a) find the mounted resonance on the actual glove, (b) log a measured g per
  cue at night so "cue delivered" is a fact, not an assumption.
- **Actuator model.** `tools/anchor_design.py` and the response table in `tools/wav_to_haptic.py`:
  Drake HF Q ~6.3, mechanical tau 12.6 ms, 10-90 % envelope rise 28 ms. Bursts shorter than
  ~25 ms never reach full amplitude; a downward carrier sweep (160 -> 120 Hz) loses roughly
  half the acceleration by the curve alone. Both should feed `onset/haptic.py`.
- **Drive rules.** Supply sets the ceiling (3 V bench safest; LiPo needs VOLUME <= 0.8; never 5 V
  on the amp without a cap on volume); tie SD to Vin; stop the I2S clock when idle rather than
  writing zeros; service the I2S buffer every ~16 ms; never park a tone at resonance.
- **Sensor corruption by the cue** (bench log, 14 Sep): with the amp on the XIAO 3V3 pin, a 5 s
  hold dropped the rail ~0.3 V, shifted FSR 10 %, and produced 7 phantom beats at 138-178 bpm.
  Fix is a separate supply (done on the bench, owed on the glove). The detector must also mask
  features for ~10 s after every `haptic:` note; it does not yet.
- **Open question:** which Drake variant is on the bench. The notes say MF (130 Hz) in one place
  and HF (160 Hz) in others. The IMU sweep above settles it in five minutes.

## Dormio method, integrated (15 Sep)

Read `docs/dormio-method.md` first. Short version: `train.py` now defaults to `MODEL = 'dormio'`,
the paper's rule (deviation from the first-120 s mean; |ΔHR| > 5 bpm or |Δflex| > 8 kΩ, OR logic,
EDA dropped). `thresholds.py` reproduces the paper's own way of setting thresholds from the
subjective reports the GUI now collects after every cue ("And were you asleep?", 1/2/3). The
paper does not use HRV; the only evidence-backed HRV term is a fall in LF/HF over ≥ 120 s, wired
as an option and off by default. Its citations for the heart-rate claim do not contain that claim;
the flex channel is the well-grounded one.

## RSSI channel (15 Sep)

`rssi_capture.py` is the study recorder for the palm XIAO's RSSI (amulet-firmware, BLE contract
v1.1; see README "RSSI"). It logs every sample with the laptop clock, the firmware's own rate and
interval status, and observed rate / gaps / batch age, and forwards live to the GUI. The parser
attaches the companion file to a session, three RSSI features exist, `thresholds.py` and
`rssi_report.py` report RSSI against the labels, and `rssi_report.py` writes the aligned export.

Open on hardware: the palm board must be advertising the Amulet service (flash `AmuletRSSI.uf2`).
The August notes give an example status of 64 Hz at a 15 ms interval but no recorded measurement
from a connected run; the first BLE session will establish what macOS actually grants. If the
same XIAO must carry both the sensor stream and the RSSI service, that is a firmware merge
(bench3 + AmuletRSSI) and the cleanest outcome is an `R,<ms>,<rssi>` line in the serial stream so
one capture holds everything.

## Self-training sessions (15 Sep)

`Nap Session.command` starts the vendored bridge (`tools/bench_bridge.py`, captures into
`data/captures/`), `rssi_capture.py`, and the GUI. The GUI's Session panel starts/ends a session;
`onset/session.py` files bench + RSSI + a JSON summary into `data/sessions/` under one stamp,
appends `ledger.csv`, and regenerates the review page (`/review`). Detector default stays the
Dormio baseline. `data/sessions/` is meant to be committed; `data/captures/` is scratch.

## Live hardware state (16 Sep, from HARDWARE_HANDOFF.md)

Board B is the wearable: battery, BLE name `Amulet-XPL`, Nordic UART, same line protocol as USB.
Channels are flex (A0) + PPG (A1) only; EDA is gone (a transitional 5th field may still appear and
is ignored). The bridge (`tools/bench_bridge.py`, now the BLE-capable version) is the single BLE
owner; everything reads its SSE on :8787. Haptics over BLE are the single-character commands;
`live.py` haptic mode `tap` sends `b`. Pattern playback needs the firmware `P,<hz>,<ms>,<vol>`
command the hardware side offered. The Amulet RSSI service is NOT on Board B, so `rssi_capture.py`
finds nothing until a board runs AmuletRSSI (or bench3 grows an `R,` line).
First real session over BLE recorded 16 Sep (see data/sessions/ledger.csv).

## RSSI over Bluetooth (16 Sep, later)

The wearable now measures link RSSI itself: `bench3_live.ino` calls `monitorRssi(0)` on connect and
emits `R,<ms>,<rssi_dbm>` on every 2nd S tick (50 Hz) while a central is connected, into the same
stream and capture as the sensor lines. `SUPPLY_VOLTS` is 4.2 for the battery build. The bridge
also has a host-side fallback (`R,<rssi>` via CoreBluetooth readRSSI) which measured only ~0.5 Hz on
this Mac, and an `ONSET_PREFER_BLE=1` switch to stay on Bluetooth while USB is attached. A copy of
the flashed firmware is in `tools/bench3_live.ino`; the source of truth stays in
`~/Documents/Arduino/bench3_live/` (archive copy synced). Note for the hardware session: its watcher
restarts a bridge on :8787 whenever it sees the board; only one bridge can own the link.
