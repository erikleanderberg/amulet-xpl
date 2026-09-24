# 02 · Hardware

<!-- ![The harness](images/harness.jpg) -->

## Boards

Two **Seeed XIAO nRF52840 Sense** modules, both running the same sketch.

- **Board A** — bench board on a breadboard, USB powered. For development.
- **Board B** — the wearable: LiPo, slide switch, worn on the wrist.

The Sense variant matters: the PDM microphone used for dream reports is on that board.

## Pin map

| Pin | Net | Notes |
|:--|:--|:--|
| `A0` / D0 | Flex strip signal | Through a 10 kΩ divider to GND |
| `A1` / D1 | PPG signal | Pulse Sensor Amped `S`. Keep away from the amp wiring |
| `D2` | Button | 6 mm tactile to GND, `INPUT_PULLUP`, pressed = LOW |
| `D8` / `D9` / `D10` | I²S `DIN` / `BCLK` / `LRC` | To the MAX98357A |
| `D22` | Charge current (`HICHG`) | LOW = 100 mA fast, HIGH = 50 mA |
| `D23` | `~CHG` | LOW while charging, HIGH when done |
| `PIN_VBAT` (32) | Battery sense | Enabled by driving `VBAT_ENABLE` (14) LOW |
| `3V3` / `GND` | Sensor bus | One wire each to a stripboard rail |
| `BAT+` / `BAT−` | LiPo via slide switch | **The amp's Vin is on the switched BAT+, not the XIAO** |
| `D3`–`D7` | Free | `D4`/`D5` reserved as SDA/SCL |

> **The button has four legs and it matters which two you use.** They are two internally-shorted
> pairs. Take one leg from each pair — diagonally opposite legs are always safe. Wiring both legs
> of one pair is a permanent short, and since the firmware reads `D2` as active-low it looks like
> the button is held down forever. See `hardware/wiring/amulet_wiring_button-legs.html`.

## Bill of materials

See [`hardware/bom.md`](../hardware/bom.md) for the full table with footprints and part numbers.
The short version:

XIAO nRF52840 Sense · Adafruit MAX98357A I²S amp · TITAN Drake HF LRA (f₀ 160 Hz) ·
2.2″ flex strip · Pulse Sensor Amped · 10 kΩ resistor · 6 mm tactile switch · SPDT slide switch ·
120 mAh protected LiPo (401230) · protoboard scrap · 24 AWG 2-core cable

## Power

The amp draws its supply from the **switched battery positive**, not from the XIAO's 3V3 rail.
This is deliberate and it is the fix for a real failure:

> **14 Sep 2026** — with the amp on the XIAO's 3V3 pin, a 5 s haptic hold dropped the rail ~0.3 V,
> shifted the flex reading 10 %, and produced **7 phantom heartbeats at 138–178 bpm**. The cue was
> corrupting the very signals used to decide whether to fire it.

Consequence worth remembering: **pull the LiPo and the actuator is dead even though the firmware
still reports that it is driving.** `peak` on the `H` line proves I²S output, not vibration.

`SUPPLY_VOLTS` is `4.2f` for the battery build, which caps `VOLUME` at 0.83. On a 3.3 V bench
supply the cap is 1.0. Never run the amp at 5 V without capping volume.

### Measured runtime

**7 h 19 m 39 s** on a 120 mAh cell, streaming 100 Hz over BLE at a 7.5–15 ms connection interval,
including 98 haptic taps. The taps are negligible — about 15 seconds of drive in total, well under
1 mAh. The draw is the MCU, the ADC and the radio.

Two easy savings, neither yet applied: `Bluefruit.setTxPower(4)` is +4 dBm where 0 would do (the
link sits at −57 dBm), and the green LED flashes 60 ms on every detected heartbeat, all night.

## Physical quirks

- **The "FSR" is a flex sensor.** It reads **240–260 counts at rest with nothing on it** (~30 kΩ),
  not zero. Bending *lowers* the reading. Treat it as a channel with a non-zero baseline; "grip"
  is a deviation below rest, and a value near 250 means nothing is touching it.
- **PPG idles at ~470** with no finger, ±25 counts of room-light flicker. The firmware gates beat
  detection to 40–180 bpm so flicker no longer produces phantom beats.
- **Beat detection fires on the PPG upstroke**, ~250–300 ms after the actual cardiac contraction.
  A constant offset: irrelevant for HRV, relevant for beat-locked haptics.

## Build guides

Open these in a browser — they are self-contained pages with diagrams:

| File | Covers |
|:--|:--|
| `amulet_build-spec.html` | Full BOM, netlist, assembly order |
| `amulet_wiring_glove-harness.html` | The worn harness, cable runs, module split |
| `amulet_wiring_soldered-harness.html` | Protoboard version, joint by joint |
| `amulet_wiring_FSR-strip.html` | Flex strip and divider |
| `amulet_wiring_Adafruit-1093-PulseAmp.html` | PPG wiring |
| `amulet_wiring_MAX98357A-Drake-LRA.html` | Amp and actuator |
| `amulet_wiring_button-legs.html` | Which two legs of the tactile switch |
