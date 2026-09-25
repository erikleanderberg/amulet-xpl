# Documentation

| | Page | Covers |
|:--|:--|:--|
| 01 | [System](01-system.md) | What each part does, and how a session runs |
| 02 | [Firmware](02-firmware.md) | `bench4_live`, the wire protocol, build and flash |
| 03 | [Software](03-software.md) | Host stack, cross-platform setup, transcription |
| 04 | [Data](04-data.md) | Capture formats, RSSI output, loading a session |

## Clocks

Three, and they are not interchangeable.

- **Board time** — the `ms` field on every line, a monotonic counter since power-on. Exact in its
  spacing and immune to link jitter. Use it for intervals. It drifts about −0.55 % against the
  host over hours, so do not use it for absolute alignment.
- **Host time** — the Unix timestamp the bridge stamps on arrival. Use it to align separate
  streams. USB lags the board by ≤ 5 ms, Bluetooth by up to ~90 ms.
- **Session time** — seconds since the session started, on the 100 Hz sample grid.
