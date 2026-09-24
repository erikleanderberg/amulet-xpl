# Documentation

Read in order if you are new. Each page stands alone if you are not.

| | Page | For |
|:--|:--|:--|
| 01 | [Overview](01-overview.md) | What the device does, and the science it rests on |
| 02 | [Hardware](02-hardware.md) | Boards, pin map, power, physical quirks |
| 03 | [Firmware](03-firmware.md) | `bench4_live`: structure, build, flash |
| 04 | [Protocol](04-protocol.md) | Every line and command, both directions |
| 05 | [Software](05-software.md) | Bridge, detector, GUI, transcription |
| 06 | [Running a session](06-running-a-session.md) | The operator runbook |
| 07 | [Data](07-data.md) | File formats and how to load them |
| 08 | [Research notes](08-research.md) | Dormio, physiology, what the evidence supports |
| 09 | [Troubleshooting](09-troubleshooting.md) | Failure modes we have actually hit |

## Conventions used throughout

- **Board time** is the `ms` field on every line: a monotonic millisecond counter since power-on.
  It is exact in its spacing and immune to link jitter. Use it for intervals.
- **Host time** is the Unix timestamp the bridge stamps on arrival. Use it to align separate
  streams. Over USB it lags the board by ≤ 5 ms; over Bluetooth by up to ~90 ms.
- **Session time** `t` is seconds since the session started, on the 100 Hz sample grid.
- A **capture** is one CSV holding every line the board sent, signals and labels together.
