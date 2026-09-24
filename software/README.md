# Software

[`onset-ml/`](onset-ml/) — the bridge, the detector, the cockpit, session filing and on-device
transcription. Python 3.12.

```bash
cd onset-ml
uv venv --python 3.12 && uv pip install numpy scipy scikit-learn pandas pyserial bleak
make -C tools/apple_stt                     # on-device speech-to-text (macOS 26+)

.venv/bin/python tools/bench_bridge.py      # owns the board — http://localhost:8787
.venv/bin/python live.py                    # the cockpit  — http://localhost:8790
```

No hardware:

```bash
.venv/bin/python tools/fake_board.py --port 8799
ONSET_BRIDGE=http://localhost:8799 .venv/bin/python live.py
```

Architecture: [`docs/05-software.md`](../docs/05-software.md) ·
Runbook: [`docs/06-running-a-session.md`](../docs/06-running-a-session.md) ·
Data formats: [`docs/07-data.md`](../docs/07-data.md)

> `data/` holds recorded sessions and dream audio. It is gitignored and must stay that way.
