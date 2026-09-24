"""Voice note: the spoken report you whisper to the glove after a session.

bench4_live records a few seconds of PDM audio into board RAM on `r`, then streams it back as
base64 on `d`:

    A,<seq>,<base64 of raw little-endian int16 PCM>      many lines
    AEND,<total_bytes>,<sample_rate>                     once, at the end

This class collects those lines, rebuilds the WAV, and tracks the state the GUI draws. It owns
no I/O to the board — live.py sends the commands and feeds the lines in — so it is testable
without hardware and cannot wedge the SSE reader.
"""
from __future__ import annotations

import base64
import threading
import time
import wave

# state: idle -> recording -> downloading -> transcribing -> done | failed
IDLE, RECORDING, DOWNLOADING, TRANSCRIBING, DONE, FAILED = (
    'idle', 'recording', 'downloading', 'transcribing', 'done', 'failed')

DOWNLOAD_TIMEOUT_S = 90.0


class VoiceNote:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self.state = IDLE
        self.chunks: dict[int, str] = {}
        self.rate = 16000
        self.expected = 0
        self.started = 0.0
        self.rec_s = 0.0
        self.wav_path = ''
        self.text = ''
        self.backend = ''
        self.error = ''
        self.reason = ''          # why it started: 'button' | 'manual' | 'session-end'
        self.no_speech = False
        self.note = ''
        self.txt_path = ''
        self.stamp = ''
        self.peak = 0
        self.rms = 0

    # ---------------------------------------------------------------- lifecycle
    def begin(self, reason: str, stamp: str, rec_s: float) -> None:
        with self.lock:
            self._reset_locked()
            self.state = RECORDING
            self.reason = reason
            self.stamp = stamp
            self.rec_s = rec_s
            self.started = time.time()

    def downloading(self) -> None:
        with self.lock:
            if self.state == RECORDING:
                self.state = DOWNLOADING

    def fail(self, why: str) -> None:
        with self.lock:
            self.state = FAILED
            self.error = why

    # ---------------------------------------------------------------- line feed
    def feed(self, typ: str, fields: list[str]) -> bool:
        """Accept one A/AEND line. Returns True when AEND closed the download."""
        with self.lock:
            if self.state not in (RECORDING, DOWNLOADING):
                return False
            if typ == 'A' and len(fields) >= 2:
                self.state = DOWNLOADING
                try:
                    self.chunks[int(fields[0])] = fields[1]
                except ValueError:
                    pass
                return False
            if typ == 'AEND' and len(fields) >= 2:
                try:
                    self.expected, self.rate = int(fields[0]), int(fields[1]) or 16000
                except ValueError:
                    pass
                return True
        return False

    def timed_out(self) -> bool:
        with self.lock:
            return (self.state in (RECORDING, DOWNLOADING)
                    and time.time() - self.started > DOWNLOAD_TIMEOUT_S)

    # ---------------------------------------------------------------- output
    def pcm(self) -> bytes:
        with self.lock:
            parts = [self.chunks[k] for k in sorted(self.chunks)]
        out = bytearray()
        for p in parts:
            try:
                out += base64.b64decode(p, validate=False)
            except Exception:
                continue
        return bytes(out[: (len(out) // 2) * 2])

    def save_wav(self, path: str) -> dict:
        raw = self.pcm()
        if not raw:
            self.fail('no audio came back from the board')
            return dict(ok=False, error='no audio came back from the board')
        with self.lock:
            rate, expected = self.rate, self.expected
        try:
            with wave.open(path, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes(raw)
        except Exception as e:
            self.fail(f'{e.__class__.__name__}: {e}')
            return dict(ok=False, error=str(e))
        peak = rms = 0
        try:
            import numpy as np
            a = np.frombuffer(raw, dtype='<i2').astype(float)
            if a.size:
                peak, rms = int(abs(a).max()), int((a ** 2).mean() ** 0.5)
        except Exception:
            pass
        with self.lock:
            self.wav_path, self.peak, self.rms = path, peak, rms
            self.state = TRANSCRIBING
        short = expected and len(raw) < expected
        return dict(ok=True, path=path, bytes=len(raw), expected=expected, rate=rate,
                    seconds=round(len(raw) / 2 / rate, 2), peak=peak, rms=rms, truncated=bool(short))

    def finish(self, result: dict, txt_path: str = '') -> None:
        with self.lock:
            self.text = result.get('text', '')
            self.backend = result.get('backend') or ''
            self.error = '' if result.get('ok') else (result.get('error') or '')
            self.no_speech = bool(result.get('no_speech'))
            self.note = result.get('note', '')
            self.txt_path = txt_path
            self.state = DONE

    # ---------------------------------------------------------------- view
    def snapshot(self) -> dict:
        with self.lock:
            left = 0.0
            if self.state == RECORDING and self.rec_s:
                left = max(0.0, self.rec_s - (time.time() - self.started))
            return dict(state=self.state, reason=self.reason, stamp=self.stamp,
                        rec_s=self.rec_s, remaining=round(left, 1), chunks=len(self.chunks),
                        bytes_expected=self.expected, rate=self.rate, wav=self.wav_path,
                        text=self.text, backend=self.backend, error=self.error,
                        no_speech=self.no_speech, note=self.note, txt=self.txt_path,
                        peak=self.peak, rms=self.rms)
