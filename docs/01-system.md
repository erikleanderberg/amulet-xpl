# 01 · System

<!-- ![The harness](images/harness.jpg) -->

## What each part does

| Part | Where | Job |
|:--|:--|:--|
| **Flex strip** | Index finger | Grip tone. Muscle tone collapse is the earliest reliable sign of sleep onset without EEG. The primary detection channel. |
| **PPG** | Middle finger | Heart rate and pulse amplitude. Rate falls a few bpm over 1–3 min at onset; distal vasodilation raises amplitude. |
| **Link RSSI** | The radio itself | The capture channel. 50 Hz, board-measured, into the same file on the same clock as everything else. |
| **LRA** | Wrist | The cue. A 160 Hz resonant actuator driven by an I²S class-D amp. Small taps to lift the wearer back off the threshold; a 2 s tone to end the session. |
| **Button** | Wrist | The only control. Start, record, finish, power. |
| **PDM microphone** | On the board | The spoken report, recorded after the session ends. |

## The detection principle

The device does not know what sleep looks like in general. It knows what **this wearer** looks
like awake, and notices when they stop looking like that.

1. **Calibrate** — the first 120 s are assumed awake. Each feature's mean and standard deviation
   over that window become the personal baseline.
2. **Score** — once a second, causal features over trailing windows, expressed as deviations from
   that baseline.
3. **Decide** — the default rule fires when heart rate has moved more than 5 bpm **or** grip more
   than 8 kΩ from baseline.
4. **Hold** — the condition must persist for 8 s before a cue fires, and a 60 s refractory window
   follows, so the wearer is nudged rather than harassed.

The cue is deliberately small. The aim is not to wake them; it is to lift them back to the
threshold so they cross it again, over and over, for an hour.

## Why RSSI is the point

Physiological sensing is solved and inconvenient — it needs a glove. The interesting question is
whether the same state is legible in the radio channel with nothing worn at all.

Answering that needs RF data labelled with verified state, and the labels are the hard part. This
device produces them: it holds a wearer in a known state, verifies it from two independent
physiological channels, and records the RF signature throughout, all on one clock in one file.

Every session yields roughly 60 minutes of continuous 50 Hz RSSI with second-by-second labels for
calibration, watching, threshold crossings, cue events and the wake. That is the training corpus.

> The radio reports **RSSI**: one scalar per connection event. **CSI** — per-subcarrier amplitude
> and phase — carries far more, and is where this is headed, but it is not exposed by this radio.
> The capture format treats RSSI as one channel among possible others so a CSI source can be added
> without reworking the pipeline.

## Session flow

| Phase | Duration | What happens | Wearer does |
|:--|:--|:--|:--|
| **Idle** | — | Board advertising, streaming | Press the button |
| **Calibration** | 2 min | Personal baseline captured | Lie still, stay awake |
| **Run** | 60 min | Detection and cueing | Nothing. Drift |
| **Wake** | 2 s | Unmistakable tone | Wake up |
| **Report** | open | Each press records an 8 s take | Press, speak, repeat |
| **Done** | — | Takes transcribed locally | Hold 1.2 s to finish |

Duration is configurable: `45x` over the link runs 45 minutes instead of 60.

**The session clock lives in the firmware.** If the link drops mid-session, the wearer is still
woken on time and can still record; the host reconnects and picks the stream back up.

## Power

There is no power switch on this build. The button is the power control:

| Gesture | Effect |
|:--|:--|
| Press (idle) | Start a session |
| Press (report phase) | Record one 8 s take |
| Hold 1.2 s (report phase) | Finish the report |
| Hold 1.2 s (mid-session) | Abort the session |
| **Hold 3 s (idle or done)** | **Power off** — System OFF, ~2 µA, button wakes it |

Battery is a 120 mAh LiPo wired directly to the switched rail. Measured runtime streaming 100 Hz
over Bluetooth is about **7 hours**, so a 60-minute session has ample margin.

> The amplifier draws from the battery rail directly, not the XIAO's 3V3. This is deliberate: with
> the amp on 3V3, a long cue dropped the rail ~0.3 V, shifted the flex reading 10 %, and produced
> phantom heartbeats. The cue was corrupting the signals used to decide whether to fire it.

## Physical characteristics worth knowing

- The flex strip reads **240–260 counts at rest with nothing on it** (~30 kΩ), not zero. Bending
  *lowers* the reading. A value near 250 means nothing is touching it.
- PPG idles around 470 with no finger. Beat detection is gated to 40–180 bpm so room-light flicker
  cannot produce phantom beats.
- Beat detection fires on the PPG upstroke, ~250–300 ms after the cardiac contraction. A constant
  offset — irrelevant for intervals, relevant if you ever want beat-locked cues.
