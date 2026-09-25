#!/usr/bin/env python3
"""A bench4_live stand-in: speaks the bridge's HTTP + SSE contract with no hardware attached.

For working on the GUI and the voice-note flow while the real board is being flashed, or on a
train. It emits S/B/V/M/K lines, and on `r` then `d` it plays back a synthesised "recording" as
A,<seq>,<b64> chunks terminated by AEND, exactly as the board does.

    ONSET_BRIDGE=http://localhost:8799 .venv/bin/python live.py --port 8798
    .venv/bin/python tools/fake_board.py --port 8799
    # then POST {"action":"start"} to /voice, or press the glove button:
    curl -s localhost:8799/button -d '{"clicks":2}'
"""
from __future__ import annotations
import argparse, base64, json, math, os, queue, random, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FS, MIC_HZ, REC_S = 100, 8000, 8.0
# Compressed so a full session runs in seconds; --real uses the firmware's own timings.
CALIB_S, RUN_S = 6.0, 20.0
clients: set[queue.Queue] = set()
state = dict(recording=False, rec_at=0.0, dumping=False, presses=0, ms=0,
             sess='idle', sess_t0=0.0, takes=0)


def push(lines: list[str]) -> None:
    now = time.time()
    msg = ('data: ' + json.dumps([[round(now, 3), ln] for ln in lines]) + '\n\n').encode()
    for q in list(clients):
        try: q.put_nowait(msg)
        except queue.Full: clients.discard(q)


SAMPLE_LINE = os.environ.get(
    'FAKE_SPEECH', 'I was in a house made of glass and the rooms kept changing shape')


def say_pcm(seconds: float, rate: int) -> bytes | None:
    """Real speech via macOS `say`, quietened to roughly what a half-asleep murmur gives the mic.

    Without this the end-to-end test proves the plumbing but not the transcription.
    """
    import shutil, subprocess, tempfile, wave
    if not (shutil.which('say') and shutil.which('afconvert')):
        return None
    try:
        with tempfile.TemporaryDirectory() as d:
            aiff, wav = os.path.join(d, 'a.aiff'), os.path.join(d, 'a.wav')
            subprocess.run(['say', '-o', aiff, SAMPLE_LINE], check=True, capture_output=True, timeout=30)
            subprocess.run(['afconvert', '-f', 'WAVE', '-d', f'LEI16@{rate}', '-c', '1', aiff, wav],
                           check=True, capture_output=True, timeout=30)
            with wave.open(wav, 'rb') as w:
                raw = w.readframes(w.getnframes())
    except Exception:
        return None
    import array
    a = array.array('h'); a.frombytes(raw[:(len(raw) // 2) * 2])
    want = int(seconds * rate)
    out = array.array('h', [0] * want)
    for i in range(min(want, len(a))):
        out[i] = int(a[i] * 0.22)                       # quiet, like someone mumbling at 4 a.m.
    return out.tobytes()


def speech_pcm(seconds: float, rate: int) -> bytes:
    """Real speech if `say` is available, else noise shaped like speech."""
    real = say_pcm(seconds, rate)
    if real is not None:
        return real
    n = int(seconds * rate); out = bytearray()
    f0, env, voiced, left = 130.0, 0.0, False, 0
    for i in range(n):
        if left <= 0:
            voiced = random.random() < 0.65
            left = int(rate * random.uniform(0.08, 0.30))
            f0 = random.uniform(95, 165)
        left -= 1
        target = 0.32 if voiced else 0.02
        env += (target - env) * 0.0015
        t = i / rate
        v = sum(math.sin(2 * math.pi * f0 * k * t) / k for k in (1, 2, 3)) / 1.9
        v = v * env + random.gauss(0, 0.012)
        out += int(max(-1.0, min(1.0, v)) * 12000).to_bytes(2, 'little', signed=True)
    return bytes(out)


def dump(pcm: bytes) -> None:
    state['dumping'] = True
    CH = 120
    for seq, off in enumerate(range(0, len(pcm), CH)):
        push([f'A,{seq},' + base64.b64encode(pcm[off:off + CH]).decode()])
        time.sleep(0.004)                       # ~30 KB/s, in the ballpark of the real USB dump
    push([f'AEND,{len(pcm)},{MIC_HZ}'])
    state['dumping'] = False


def sess_go(st: str) -> None:
    state['sess'] = st; state['sess_t0'] = time.time()
    push([sess_line()])


def sess_line() -> str:
    el = int(time.time() - state['sess_t0']) if state['sess'] != 'idle' else 0
    left = 0
    if state['sess'] == 'calib': left = max(0, int(CALIB_S) - el)
    elif state['sess'] == 'run': left = max(0, int(RUN_S) - el)
    return f"G,{state['ms']},{state['sess']},{el},{left},{state['takes']}"


def sess_service() -> None:
    if state['sess'] == 'idle': return
    el = time.time() - state['sess_t0']
    if state['sess'] == 'calib' and el >= CALIB_S:
        push(['I,calibration done -- watching']); sess_go('run')
    elif state['sess'] == 'run' and el >= RUN_S:
        push(['I,session over -- wake cue', 'I,press the button to record a take, hold it to finish'])
        sess_go('report')


def streamer() -> None:
    seq = 0
    last_g = 0.0
    while True:
        time.sleep(0.01)
        state['ms'] += 10; ms = state['ms']; seq += 1
        sess_service()
        if time.time() - last_g >= 1.0 and state['sess'] != 'idle':
            last_g = time.time(); push([sess_line()])
        t = ms / 1000.0
        fsr = 250 + int(6 * math.sin(t / 7))
        ppg = 470 + int(120 * math.sin(2 * math.pi * 1.05 * t) * (0.6 + 0.4 * math.sin(t / 3)))
        lines = [f'S,{ms},{fsr},{ppg},460']
        if seq % 2 == 0:
            lines.append(f'R,{ms},{-55 + int(4 * math.sin(t / 11))}')
        if seq % 2 == 0:                                        # M at 50 Hz, like the board
            lvl = random.randint(1800, 5200) if state['recording'] else random.randint(20, 90)
            lines.append(f'M,{ms},{lvl},{int(lvl * 3.4)}')
        if seq % 50 == 0:                                       # V at 2 Hz
            lines.append(f'V,{ms},{3990 - int(t / 40)},{max(5, 78 - int(t / 400))},0,0,512,35')
        if seq % 100 == 0:
            lines.append(f'B,{ms},{58 + random.randint(-2, 2)},{1030 + random.randint(-40, 40)}')
        if state['recording'] and time.time() - state['rec_at'] > REC_S:
            state['recording'] = False
            lines.append(f'I,rec done: {int(MIC_HZ * REC_S)} samples @{MIC_HZ} Hz -- send d to download')
        push(lines)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path != '/events':
            self._json(dict(ok=True, fake=True)); return
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache'); self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=4000); clients.add(q)
        try:
            self.wfile.write(('data: ' + json.dumps(
                [[time.time(), '#status,connected,FAKE bench4 (no hardware)'], [time.time(), '#rec,']]) + '\n\n').encode())
            self.wfile.flush()
            while True:
                try: m = q.get(timeout=15)
                except queue.Empty: m = b': ping\n\n'
                self.wfile.write(m); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            clients.discard(q)

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        d = json.loads(self.rfile.read(n) or b'{}')
        if self.path == '/cmd':
            c = d.get('c')
            if c == 'r':
                state.update(recording=True, rec_at=time.time())
                push([f'I,recording {int(REC_S)} s ...'])
            elif c == 'x':
                state['takes'] = 0; sess_go('calib')
                push(['I,session start'])
            elif c == 'y':
                sess_go('idle'); push(['I,session aborted'])
            elif c == 'd':
                if state['dumping']:
                    push(['I,already dumping'])
                else:
                    threading.Thread(target=dump, args=(speech_pcm(REC_S, MIC_HZ),), daemon=True).start()
            elif c == 'b':
                push(['I,tap'])
            self._json(dict(sent=True, via='fake'))
        elif self.path == '/button':                     # stand in for a finger on the glove
            clicks = int(d.get('clicks', 1)); gap = float(d.get('gap_ms', 200))
            hold = float(d.get('hold_ms', 0))
            def _press():
                for _ in range(clicks):
                    state['presses'] += 1
                    push([f'K,{state["ms"]},1,{state["presses"]}'])
                    time.sleep(max(0.05, hold / 1000.0))
                    push([f'K,{state["ms"]},0,{state["presses"]}'])
                    # the firmware acts on the gesture itself; mirror that here
                    if hold >= 1200:
                        if state['sess'] == 'report':
                            push([f'I,report finished: {state["takes"]} take(s)']); sess_go('done')
                        elif state['sess'] in ('calib', 'run'):
                            sess_go('idle')
                    elif state['sess'] in ('idle', 'done'):
                        state['takes'] = 0; sess_go('calib'); push(['I,session start'])
                    elif state['sess'] == 'report' and not state['recording']:
                        state['takes'] += 1
                        state.update(recording=True, rec_at=time.time())
                        push([f'I,take {state["takes"]} recording {int(REC_S)} s ...'])
                    time.sleep(gap / 1000.0)
            threading.Thread(target=_press, daemon=True).start()
            self._json(dict(ok=True, clicks=clicks))
        elif self.path == '/record':
            push([f'#rec,{"/tmp/fake-capture.csv" if d.get("on") else ""}'])
            self._json(dict(recording='/tmp/fake-capture.csv' if d.get('on') else ''))
        elif self.path == '/note':
            push([f'#note,{d.get("text", "")}']); self._json(dict(ok=True))
        else:
            self._json(dict(ok=True))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--port', type=int, default=8799)
    a = ap.parse_args()
    threading.Thread(target=streamer, daemon=True).start()
    print(f'fake board on http://localhost:{a.port}  (SSE /events, POST /cmd /button /record /note)')
    ThreadingHTTPServer(('127.0.0.1', a.port), H).serve_forever()


if __name__ == '__main__':
    main()
