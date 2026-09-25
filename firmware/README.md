# Firmware

| Sketch | State |
|:--|:--|
| `bench4_live/` | **current** — flex, PPG, RSSI capture, mic, button, battery, session clock |
| `bench3_live.ino` | superseded — no mic, button, battery or session machine |

Board: **Seeed XIAO nRF52840 Sense**, FQBN `Seeeduino:nrf52:xiaonRF52840Sense`.
The Sense variant is required for the PDM microphone.

Protocol, session state machine, build and flash: [`docs/02-firmware.md`](../docs/02-firmware.md).

> Stop `bench_bridge.py` before flashing — it holds the serial port.
