# 03 · Firmware

`firmware/bench4_live/bench4_live.ino` — one sketch, both boards.

## What it does, and what it deliberately does not

It streams samples and plays what it is told. There is no detection, no filtering beyond beat
gating, and no state that the host cannot see. Every decision lives on the laptop.

| Loop | Rate | Work |
|:--|:--|:--|
| Sample tick | 500 Hz | Read PPG, run beat detection |
| Emit tick | 100 Hz | Read flex, emit `S`; every 2nd tick emit `R` |
| Mic block | 50 Hz | PDM level meter → `M`; fill the record buffer if recording |
| Battery | 2 Hz | VBAT divider → `V` |
| Button | on edge | Debounced 25 ms → `K` |
| Haptics | per I²S buffer | Render the burst train; stop the clock 40 ms after idle |

## Subsystems worth knowing about

**Haptics.** MAX98357A I²S amp driving a Drake HF LRA at its 160 Hz resonance. The I²S clock is
stopped 40 ms after the last run rather than left writing zeros, so an idle board does not burn
power in the amp. Bursts shorter than ~25 ms never reach full amplitude (mechanical τ ≈ 12.6 ms).

**Microphone.** On-board PDM at 16 kHz. `REC_SECONDS` is 3, giving 48000 samples = 96 KB of the
213 KB free RAM. Recording is store-and-forward: `r` records into RAM, `d` streams it back as
base64. There is no streaming audio path.

> **3 seconds is the binding constraint on dream reports.** Test reports are already being cut off
> mid-sentence. Dropping to 8 kHz would give ~13 s; speech is perfectly intelligible at 8 kHz and
> every ASR model resamples to 16 k anyway.

**Battery.** `VBAT_ENABLE` (P0.14) low, read `PIN_VBAT` (P0.32) against the internal 1.8 V
reference, scale by the Seeed divider `(1 M + 510 k)/510 k = 2.960784`. Percentage is hysteretic —
it only moves on a 2-point change, because a gauge that twitches is worse than one that lags.

**Bluetooth.** Nordic UART Service, advertised as `Amulet-XPL`, connection interval 7.5–15 ms,
MTU 247 negotiated with macOS. Whole lines are packed into ≤180-byte writes and sent as a unit, so
a full radio queue drops a whole packet rather than half a line.

## Two gotchas that cost real time

> **Never touch `NRF_CLOCK`, `NRF_POWER`, `NRF_RADIO`, `NRF_TIMER0` or `NRF_RTC0` directly after
> `Bluefruit.begin()`.** They are SoftDevice-owned; a direct register write hard-faults into a
> reset loop that presents as "USB port flapping, nothing advertising". Use
> `sd_clock_hfclk_request()` for the crystal.

> **A crashed application cannot be flashed by software.** The 1200-baud touch is handled by the
> *running* sketch, which sets a retained register and resets; the bootloader reads that register.
> A crashed sketch never sets it, so every uploader fails with "Target is not in DFU mode" forever.
> Only a **physical double-tap of RESET** recovers it. Go straight to the button; do not retry.

## Build and flash

`arduino-cli compile --upload` fails most of the time: the 1200-baud reset makes the board drop
off USB for ~2 s and re-enumerate on the same port name, and the uploader has already given up.
It is not a bad board and not bad solder — it is a race.

Flash in two steps instead:

```bash
arduino-cli compile --fqbn Seeeduino:nrf52:xiaonRF52840Sense firmware/bench4_live

# 1. touch the port at 1200 baud to enter DFU, then wait for it to come back
python3 -c "import serial,time; s=serial.Serial('/dev/cu.usbmodem2101',1200); s.dtr=False; s.close(); time.sleep(3)"

# 2. upload
~/Library/Python/3.9/bin/adafruit-nrfutil dfu serial \
  --package build/bench4_live.ino.zip -p /dev/cu.usbmodem2101 -b 115200 --singlebank
```

Fallback: double-tap RESET and copy the UF2 onto the `XIAO-SENSE` volume (family `0xADA52840`).

**Stop the bridge first** — it holds the serial port. And copying a UF2 from a background process
raises `PermissionError: [Errno 13]` under macOS removable-volume permissions; copy it from a
foreground shell.

Useful diagnostic: USB `idProduct` is **69 (0x0045)** in the bootloader and **32837 (0x8045)** in
the application. Poll `ioreg -p IOUSB` for it to tell "bootloader engaged" from "drive not
mounted", rather than watching `/Volumes`.
