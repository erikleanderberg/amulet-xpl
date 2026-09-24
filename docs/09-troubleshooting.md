# 09 · Troubleshooting

Only failures that have actually happened, with what the symptom looked like before it was
understood.

## Flashing

**"Target is not in DFU mode", every uploader, forever.**
The 1200-baud touch is handled by the *running sketch*, which sets a retained register and resets;
the bootloader reads that register. A crashed sketch never sets it. No uploader can recover this.
**Physically double-tap RESET.** Do not spend an hour retrying — the bootloader detects the
double-tap itself via a RAM flag, with the application uninvolved.

**`arduino-cli compile --upload` fails most of the time.**
A race, not a fault: the reset drops the board off USB for ~2 s and it re-enumerates on the same
port name after nrfutil has given up. Flash in two steps — see [03 · Firmware](03-firmware.md).

**The UF2 copy raises `PermissionError: [Errno 13]`.**
macOS removable-volume permissions, triggered when the copy comes from a background process. It
looks exactly like "the double-tap never worked". Copy from a foreground shell.

**Is it in the bootloader, or did the drive just not mount?**
`ioreg -p IOUSB` — `idProduct` is **69 (0x0045)** in the bootloader, **32837 (0x8045)** in the
application. More reliable than watching `/Volumes`.

## Link

**The board appears connected but is silent.**
A `bench_bridge.py` left running from an earlier session keeps reporting
`#status,connected,<port>` for a port that has since re-enumerated. Check the **process start
time**, not just whether something is listening:

```bash
ps -eo pid,lstart,command | grep bench_bridge
```

**"USB port flapping, nothing is advertising."**
A direct write to `NRF_CLOCK` / `NRF_POWER` / `NRF_RADIO` / `NRF_TIMER0` / `NRF_RTC0` after
`Bluefruit.begin()`. Those are SoftDevice-owned; the write hard-faults into a reset loop. Use
`sd_clock_hfclk_request()`.

**Nothing connects over Bluetooth.**
Only one central at a time, and the bridge holds it. Close any other BLE client. `bleak` needs
Bluetooth permission for the terminal in System Settings → Privacy & Security.

**Half a line arrives.**
Pack whole lines into ≤180-byte BLE writes. Per-line `bleuart.write` calls split lines when the
radio queue is full.

## Sensors

**Flex reads ~250 and nothing changes it.**
That is the unloaded rest value. The strip is not being held — or, if it is, it has slipped out of
the hand. Bending *lowers* the reading.

**Phantom heartbeats at 138–178 bpm during a cue.**
The amp is on the XIAO's 3V3 rail. Move it to the switched battery positive. A 5 s hold drops the
rail ~0.3 V and shifts the flex reading 10 % as well.

**The firmware says it is driving but nothing vibrates.**
`peak` on the `H` line proves I²S output, not vibration. The amp's Vin is on the switched
battery — pull the LiPo and the actuator is dead while the firmware still reports a run.

**PPG beats appear with no finger on the sensor.**
Room-light flicker at ~235 bpm. The 40–180 bpm gate handles it; if you see it, check the gate.

## Detector

**It fires every 60 seconds, forever.**
The signature failure. The baseline is computed once over the first 120 s and never revisited, so
a *physical* change to the sensor becomes a permanent deviation and the condition never clears.
The cue then fires at the refractory floor indefinitely.

> Observed 21 Sep 2026: flex jumped 102 counts in one minute at 03:07 when the hand released the
> strip. From 03:08 the cue fired **92 times at 60.3 s intervals** until the battery died at
> 04:41. The final Δflex was −33 kΩ against an 8 kΩ threshold. Heart rate never crossed its own
> threshold at all — every one of those 98 cues came from the flex channel alone.

Mitigations, none yet implemented: gate detection on the channel being loaded at all; cap
consecutive unanswered cues and auto-disarm; fix `fsr_dropout30`, which anchors its floor on the
row's own start and therefore never trips.

**A session is 8 hours long but contains 2½ hours of data.**
The board died and the session was not ended until morning. `duration_min` is data time;
`end_host − start_host` is wall-clock. Nothing detects a dead stream and ends the session.

**A report is recorded hours after its cue.**
The report box stays open until answered. In the 21 Sep session the single report was answered
5 h 22 m after the cue, on waking, and still landed in the ledger as that session's `first_report`.
Treat reports with a large cue-to-answer gap as invalid.

## Audio

**No transcript, and "no speech backend installed".**
```bash
cd software/onset-ml && make -C tools/apple_stt
.venv/bin/python transcribe_notes.py --backends
```

**The transcript stops mid-sentence.**
`REC_SECONDS` is 3. That is the whole recording. Lower the sample rate or enlarge the buffer.

**The transcript says something that was never said.**
It should not — the VAD gate exists precisely to prevent this, and a silent clip returns
`no_speech`. If it happens, the clip got past the gate; check `snr_db` and `speech_ratio` in the
session JSON, and keep the WAV, which is always the ground truth.

**The probe tone never sounds.**
The browser blocks audio until the page is clicked. The header shows *click to enable audio* until
it has been.
