# Firmware

| Sketch | State | Notes |
|:--|:--|:--|
| `bench4_live/` | **current** | Flex + PPG + beats + link RSSI + PDM mic + button + battery |
| `bench3_live.ino` | superseded | No microphone, no button, no battery telemetry. Kept for reference |

Board: **Seeed XIAO nRF52840 Sense**, FQBN `Seeeduino:nrf52:xiaonRF52840Sense`.
The Sense variant is required — the PDM microphone is only on that board.

Build, flash and the two gotchas that will cost you an hour each:
[`docs/03-firmware.md`](../docs/03-firmware.md).
The wire format both sketches speak: [`docs/04-protocol.md`](../docs/04-protocol.md).

> Stop `bench_bridge.py` before flashing — it holds the serial port.
