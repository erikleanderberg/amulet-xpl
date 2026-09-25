# Software

[`onset-ml/`](onset-ml/) — bridge, detector, cockpit, session capture and local transcription.
Python 3.10+, runs on macOS, Windows and Linux.

```bash
cd onset-ml
python -m venv .venv
.venv/bin/pip install -e ".[transcribe]"     # Windows: .venv\Scripts\pip

.venv/bin/python tools/bench_bridge.py       # owns the board  — http://localhost:8787
.venv/bin/python live.py                     # the cockpit     — http://localhost:8790
```

No hardware:

```bash
.venv/bin/python tools/fake_board.py --port 8799
ONSET_BRIDGE=http://localhost:8799 .venv/bin/python live.py
```

Architecture and cross-platform notes: [`docs/03-software.md`](../docs/03-software.md) ·
Data formats: [`docs/04-data.md`](../docs/04-data.md)

> `data/` holds recorded sessions and audio. It is gitignored and must stay that way.
> Transcription is entirely local — there is no cloud backend in this codebase.
