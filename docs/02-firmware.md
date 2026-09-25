# 02 · Firmware

`firmware/bench4_live/bench4_live.ino` — one sketch, board **Seeed XIAO nRF52840 Sense**
(FQBN `Seeeduino:nrf52:xiaonRF52840Sense`). The Sense variant is required: the PDM microphone is
only on that board.

## What it owns

It streams samples, keeps the session clock, and plays what it is told. Detection and cue policy
live on the host. The board owns exactly three pieces of state, because each must survive a
dropped link:

- **The session clock.** Calibration, run duration, the wake cue.
- **Button semantics.** Which gesture means what, given the phase.
- **The audio buffer.** Recording is store-and-forward; the host cannot be in the loop.

| Loop | Rate | Work |
|:--|:--|:--|
| Sample tick | 500 Hz | PPG read, filter, beat detection |
| Emit tick | 100 Hz | Flex read, emit `S`; every 2nd tick emit `R` |
| Mic block | 50 Hz | Level meter → `M`; fill the record buffer |
| Session | 1 Hz | Phase timing → `G` |
| Battery | 2 Hz | VBAT divider → `V` |
| Button | on edge | Debounced 25 ms → `K` |
| Haptics | per I²S buffer | Render the burst; stop the clock 40 ms after idle |

## Protocol

Text lines, `\n`-terminated, comma-separated, ASCII. **Identical over USB serial at 115200 and
over Bluetooth.** This is the entire contract between the board and everything else.

### Board → host

| Line | Rate | Fields |
|:--|:--|:--|
| `S,<ms>,<fsr>,<pulse>,<thresh>` | 100 Hz | `fsr` 0–1023 at A0 · `pulse` raw PPG at A1 · `thresh` the beat detector's current threshold |
| `B,<ms>,<bpm>,<ibi>` | per beat | `bpm` is a median-filtered rate; **`ibi` is the single latest interval in ms — use `ibi` for variability** |
| `R,<ms>,<rssi>` | 50 Hz | Link RSSI in dBm, board-measured per connection event. **The capture channel** |
| `M,<ms>,<rms>,<peak>` | 50 Hz | Microphone level, while the mic is on |
| `V,<ms>,<mV>,<pct>,<chg>,<fast>,<raw>,<sag>` | 2 Hz | Battery |
| `K,<ms>,<down>,<count>` | on edge | Button. `down` 1 = pressed |
| `G,<ms>,<state>,<elapsed>,<left>,<takes>` | 1 Hz | Session phase: `idle`/`calib`/`run`/`report`/`done`, seconds elapsed and remaining, takes recorded |
| `H,<mode>,<hz>,<vol>,<peak>,<sync>,<gap_us>,<dropped>` | 1 Hz idle, 10 Hz driving | Haptic and loop health |
| `A,<seq>,<base64>` | during download | A chunk of raw little-endian int16 PCM |
| `AEND,<total_bytes>,<rate>` | once | Closes a download |
| `I,<text>` | on events | Acknowledgements and errors |

### Host → board

Single ASCII characters, no newline.

| Char | Action |
|:--|:--|
| `x` | Start a session. Digits first set the duration: `45x` = 45 minutes |
| `y` | Abort the session |
| `b` `t` `o` `z` `w` `n` `s` | Tap · tone · hold · buzz · wide sweep · narrow sweep · stop |
| `+` `-` | Volume ± 0.10 |
| `k` | Toggle tap-on-every-heartbeat |
| `m` `r` `d` `g` `l` | Mic on/off · record a take · download · cycle gain · latency probe |
| `c` | Toggle fast charge |
| `h` | Emit status now |

A command sent while the board is busy or cooling is refused with `I,busy`. **Check for the reply.**

### Session state machine

```
IDLE ──press──▶ CALIB (120 s) ──▶ RUN (60 min, cues fire) ──▶ wake tone
                                                                  │
   DONE ◀──hold 1.2 s── REPORT ◀───────────────────────────────────┘
     │                    │
     └──hold 3 s──▶ OFF   └──press──▶ record one 8 s take, auto-download
```

Mid-session button presses are logged as `K` lines but do nothing — the protocol is hands-off by
design. A 1.2 s hold aborts.

### Audio

Store-and-forward, not streaming. **8 kHz, 16-bit, 8 s per take** = 128 KB of the board's RAM,
leaving ~84 KB headroom. Speech is fully intelligible at 8 kHz and every speech recogniser
resamples internally anyway, so the lower rate buys seconds for free.

Multiple takes are the answer to the buffer limit: each press records one take and downloads it,
so a report can be as long as the wearer likes in 8-second pieces. The host joins them.

### Link

Nordic UART Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`, RX `…0002`, TX `…0003`, advertised as
`Amulet-XPL`. Connection interval 7.5–15 ms, MTU 247 on macOS. Whole lines are packed into
≤180-byte writes, so a full radio queue drops a whole packet rather than half a line.

**One central at a time.** The bridge holds it; do not open a second client.

Measured: connect 1.6 s · 99.5 `S` lines/s · 0 dropped over 20 s · arrival jitter p50 0 ms,
max 89 ms.

## Build and flash

```bash
arduino-cli compile --fqbn Seeeduino:nrf52:xiaonRF52840Sense firmware/bench4_live
```

`--upload` fails most of the time, and it is a race rather than a fault: the 1200-baud reset drops
the board off USB for ~2 s and it re-enumerates on the same port name after the uploader has given
up. Flash in two steps:

```bash
# 1 · touch the port at 1200 baud to enter DFU, then wait for it to come back
python3 -c "import serial,time; s=serial.Serial('/dev/cu.usbmodem2101',1200); s.dtr=False; s.close(); time.sleep(3)"

# 2 · upload
adafruit-nrfutil dfu serial --package build/bench4_live.ino.zip \
  -p /dev/cu.usbmodem2101 -b 115200 --singlebank
```

Fallback: double-tap RESET and copy the UF2 onto the `XIAO-SENSE` volume (family `0xADA52840`).

**Stop the bridge first** — it holds the serial port.

### Two traps

> **A crashed sketch cannot be flashed by software.** The 1200-baud touch is handled by the
> *running* application, which sets a retained register and resets; the bootloader reads that
> register. A crashed sketch never sets it, so every uploader reports "Target is not in DFU mode"
> forever. **Physically double-tap RESET.** Do not keep retrying.
>
> `ioreg -p IOUSB` tells you which mode it is in: `idProduct` is **69 (0x0045)** in the bootloader,
> **32837 (0x8045)** in the application.

> **Never write `NRF_CLOCK`, `NRF_POWER`, `NRF_RADIO`, `NRF_TIMER0` or `NRF_RTC0` directly after
> `Bluefruit.begin()`.** They are SoftDevice-owned; the write hard-faults into a reset loop that
> presents as "USB port flapping, nothing advertising". Use `sd_clock_hfclk_request()`.

## Footprint

```
Program storage   159,920 bytes   19% of 811,008
Global variables  153,404 bytes   64% of 237,568   (~84 KB free)
```

The record buffer is 128 KB of that. If you enlarge it, watch the headroom — above roughly 78 %
the toolchain warns and BLE stability suffers.
