"""Speech-to-text for session voice notes.

Design notes, from the 2026 literature and from measuring this machine:

* **Local only.** Nothing here calls a web service. A dream report is about as private as data
  gets, and a study cannot promise participants confidentiality while shipping their audio to a
  third party. Every backend below runs on the machine in front of you.
* **Cross-platform.** `faster-whisper` is the portable default and runs on Windows, macOS and
  Linux from a single `pip install`, CPU-only, no GPU required. On macOS 26+ Apple's
  SpeechAnalyzer is preferred when present because it is faster and needs no model download, but
  it is an optimisation, never a requirement.
* **Whispered speech is the real risk, not latency.** Whispering removes the fundamental frequency
  and harmonic structure that ASR models lean on; Whisper-v3 goes from 3.95 % CER on normal speech
  to 18.93 % on whispered speech. Transcription here happens after the session, so a second of
  latency costs nothing and accuracy is worth everything.
* **A hallucinated transcript is worse than no transcript.** This feeds a research dataset, and
  Whisper-family models are known to emit training-set subtitle text ("Thank you for watching!")
  over silence. So every clip passes an energy VAD first, and a clip with no speech in it returns
  `no_speech` instead of being handed to a decoder that will invent something.
* **The WAV is the ground truth.** The transcript is derived data; it is never the only record.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
APPLE_CLI = os.path.join(HERE, '..', 'tools', 'apple_stt', 'apple-stt')
LOCALE = os.environ.get('ONSET_STT_LOCALE', 'en-US')
IS_MAC = platform.system() == 'Darwin'
# Apple's engine is tried first on macOS only. faster-whisper is the portable fallback and the
# default everywhere else, so a Windows machine needs no special-casing.
DEFAULT_ORDER = ('apple,parakeet,mlx_whisper,faster_whisper,whisper' if IS_MAC
                 else 'faster_whisper,whisper')
ORDER = [b.strip() for b in os.environ.get('ONSET_STT', DEFAULT_ORDER).split(',') if b.strip()]

MODEL_MLX = os.environ.get('ONSET_WHISPER_MODEL', 'mlx-community/whisper-large-v3-turbo')
MODEL_FW = os.environ.get('ONSET_FW_MODEL', 'large-v3')

LABELS = dict(apple='Apple SpeechAnalyzer (local, macOS)', parakeet='parakeet-mlx (local, Apple silicon)',
              mlx_whisper='mlx-whisper (local, Apple silicon)',
              faster_whisper='faster-whisper (local, any platform)',
              whisper='openai-whisper (local, any platform)')
PIP = dict(parakeet='parakeet-mlx', mlx_whisper='mlx-whisper', faster_whisper='faster-whisper',
           whisper='openai-whisper')


# ---------------------------------------------------------------- audio
def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, 'rb') as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32)
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
    return a, sr


def measure(path: str) -> dict:
    """Energy VAD plus the numbers worth keeping in the record.

    The floor is taken from the clip's own quietest frames rather than a fixed constant, so a
    genuine whisper recorded at high mic gain is not thrown away with the silence.
    """
    try:
        a, sr = read_wav(path)
    except Exception as e:
        return dict(ok=False, error=f'unreadable wav: {e}')
    if a.size == 0:
        return dict(ok=False, error='empty wav', seconds=0.0)
    n = max(1, int(0.02 * sr))                                   # 20 ms frames
    frames = a[: (a.size // n) * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    floor = float(np.percentile(rms, 10))
    thresh = max(floor * 3.0, 25.0)                              # 25 counts ~ the PDM noise floor
    speech = rms > thresh
    ratio = float(speech.mean())
    snr = float(20 * np.log10(np.percentile(rms, 90) / floor)) if floor > 0 else 0.0
    return dict(ok=True, seconds=round(a.size / sr, 2), rate=sr, peak=int(np.abs(a).max()),
                rms=int(np.sqrt((a ** 2).mean())), floor=round(floor, 1),
                speech_ratio=round(ratio, 3), snr_db=round(snr, 1),
                has_speech=bool(ratio >= 0.04 and np.abs(a).max() > 150))


# ---------------------------------------------------------------- backends
def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def installed(name: str) -> bool:
    if name == 'apple':
        return IS_MAC and os.path.exists(APPLE_CLI) and os.access(APPLE_CLI, os.X_OK)
    if name in ('parakeet', 'mlx_whisper') and not IS_MAC:
        return False                                   # MLX is Apple silicon only
    return _has({'parakeet': 'parakeet_mlx'}.get(name, name))


def available() -> tuple[str | None, str]:
    for b in ORDER:
        if installed(b):
            return b, LABELS.get(b, b)
    return None, ('no speech backend installed — run `pip install faster-whisper` '
                  '(works on Windows, macOS and Linux)'
                  + (', or build tools/apple_stt with `make -C tools/apple_stt`' if IS_MAC else ''))


def _apple(path: str) -> str:
    r = subprocess.run([APPLE_CLI, path, '--locale', LOCALE], capture_output=True, text=True, timeout=180)
    d = json.loads(r.stdout.strip() or '{}')
    if not d.get('ok'):
        raise RuntimeError(d.get('error') or f'apple-stt exit {r.returncode}')
    return d.get('text', '')


def _parakeet(path: str) -> str:
    from parakeet_mlx import from_pretrained
    m = from_pretrained(os.environ.get('ONSET_PARAKEET', 'mlx-community/parakeet-tdt-0.6b-v3'))
    return (m.transcribe(path).text or '').strip()


def _mlx_whisper(path: str) -> str:
    import mlx_whisper
    # condition_on_previous_text=False stops one bad guess from dragging the rest of the clip with it
    r = mlx_whisper.transcribe(path, path_or_hf_repo=MODEL_MLX, language=LOCALE.split('-')[0],
                               condition_on_previous_text=False, temperature=0.0)
    return (r.get('text') or '').strip()


def _faster_whisper(path: str) -> str:
    from faster_whisper import WhisperModel
    segs, _ = WhisperModel(MODEL_FW, device='cpu', compute_type='int8').transcribe(
        path, language=LOCALE.split('-')[0], vad_filter=True, condition_on_previous_text=False)
    return ' '.join(s.text for s in segs).strip()


def _whisper(path: str) -> str:
    import whisper
    return (whisper.load_model(MODEL_FW).transcribe(path, condition_on_previous_text=False)
            .get('text') or '').strip()


RUNNERS = dict(apple=_apple, parakeet=_parakeet, mlx_whisper=_mlx_whisper,
               faster_whisper=_faster_whisper, whisper=_whisper)


# ---------------------------------------------------------------- entry point
def transcribe(path: str, backend: str | None = None, second_opinion: bool = False) -> dict:
    """Never raises. Returns ok / text / backend / error plus the audio measurements."""
    m = measure(path)
    out = dict(ok=False, text='', backend=None, error='', audio=m, alternates={})
    if not m.get('ok'):
        out['error'] = m.get('error', 'unreadable audio'); return out
    if not m['has_speech']:
        # Hand nothing to the decoder: this is where hallucinated dream reports come from.
        out.update(ok=True, error='', text='', no_speech=True,
                   note=f'no speech detected (peak {m["peak"]}, speech {m["speech_ratio"]*100:.0f}% of frames)')
        return out

    order = [backend] if backend else [b for b in ORDER if installed(b)]
    if not order:
        out['error'] = available()[1]; return out

    for b in order:
        if not installed(b):
            out['error'] = f'{b} not installed'; continue
        try:
            text = (RUNNERS[b](path) or '').strip()
        except Exception as e:
            out['error'] = f'{b}: {e.__class__.__name__}: {e}'
            continue
        out.update(ok=True, text=text, backend=LABELS.get(b, b), backend_id=b, error='')
        break
    else:
        return out

    if second_opinion:
        for b in order:
            if b == out.get('backend_id') or not installed(b):
                continue
            try:
                out['alternates'][b] = (RUNNERS[b](path) or '').strip()
            except Exception:
                pass
            break
    return out
