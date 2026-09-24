#!/usr/bin/env python3
"""
bench_scope.py -- live scope for the Amulet bench.

Subscribes to bench_bridge on :8787, keeps a rolling window in memory, and
serves a single page on :8792 that polls it. Same origin, so no CORS.
Shows PPG with its live threshold and beat marks, the button (K lines, which
the old dashboard predates), FSR, haptics and mic level.

  python tools/bench_scope.py      then open http://localhost:8792/
"""
import json, os, re, struct, subprocess, threading, time, urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

BRIDGE = 'http://localhost:8787'
PORT = 8792
WINDOW = 3000

state = {
    'ppg': deque(maxlen=WINDOW), 'fsr': deque(maxlen=WINDOW), 'thr': deque(maxlen=WINDOW),
    'ms': deque(maxlen=WINDOW), 'beats': deque(maxlen=60), 'btn': deque(maxlen=60),
    'mic': deque(maxlen=WINDOW), 'filt': deque(maxlen=WINDOW), 'fthr': deque(maxlen=WINDOW),
    'amp': 0.0, 'usable': False,
    'bv': deque(maxlen=1800), 'bt': deque(maxlen=1800), 'bjit': deque(maxlen=20),
    'mv': 0, 'pct': 0, 'chg': False, 'fast': True, 'sag': 0,
    'achunks': {}, 'adone': False, 'arate': 16000,
    'rec': 'idle', 'transcript': '', 'clip': {},
    'hap': 'idle', 'vol': 0.0, 'peak': 0, 'btnDown': False, 'btnCount': 0,
    'connected': False, 'seq': 0, 'lastLine': 0.0,
}
lock = threading.Lock()
LINE = re.compile(r'"((?:S|B|H|M|I|R|K|F|V|A|#)[^"]*)"')


def pump():
    while True:
        try:
            with urllib.request.urlopen(BRIDGE + '/events', timeout=10) as r:
                for raw in r:
                    txt = raw.decode('utf-8', 'replace')
                    for ln in LINE.findall(txt):
                        f = ln.split(',')
                        with lock:
                            state['lastLine'] = time.time()
                            state['seq'] += 1
                            k = f[0]
                            try:
                                if k == 'S' and len(f) >= 5:
                                    state['ms'].append(int(f[1])); state['fsr'].append(int(f[2]))
                                    state['ppg'].append(int(f[3])); state['thr'].append(int(f[4]))
                                    state['connected'] = True
                                elif k == 'B' and len(f) >= 4:
                                    state['beats'].append({'ms': int(f[1]), 'bpm': int(f[2]), 'ibi': int(f[3])})
                                elif k == 'K' and len(f) >= 4:
                                    state['btnDown'] = (f[2] == '1'); state['btnCount'] = int(f[3])
                                    state['btn'].append({'ms': int(f[1]), 'down': f[2] == '1', 'n': int(f[3])})
                                elif k == 'F' and len(f) >= 6:
                                    state['filt'].append(int(f[2])/10.0)
                                    state['fthr'].append(int(f[3])/10.0)
                                    state['amp'] = int(f[4])/10.0
                                    state['usable'] = (f[5] == '1')
                                elif k == 'A' and len(f) >= 3:
                                    state['achunks'][int(f[1])] = ln.split(',', 2)[2]
                                elif k == 'AEND' and len(f) >= 3:
                                    state['arate'] = int(f[2]); state['adone'] = True
                                elif k == 'V' and len(f) >= 8:
                                    state['mv']=int(f[2]); state['pct']=int(f[3])
                                    state['chg']=(f[4]=='1'); state['fast']=(f[5]=='1')
                                    state['sag']=int(f[7])
                                    if state['bv']: state['bjit'].append(abs(int(f[2]) - state['bv'][-1]))
                                    state['bv'].append(int(f[2])); state['bt'].append(time.time())
                                elif k == 'M' and len(f) >= 4:
                                    state['mic'].append(int(f[2]))
                                elif k == 'H' and len(f) >= 5:
                                    state['hap'] = f[1]; state['vol'] = float(f[3]); state['peak'] = int(f[4])
                            except ValueError:
                                pass
        except Exception:
            with lock:
                state['connected'] = False
            time.sleep(1.5)



WHISPER = '/opt/homebrew/bin/whisper-cli'
MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'models', 'ggml-base.en.bin')
CAPTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'captures')


def _send(c):
    try:
        urllib.request.urlopen(urllib.request.Request(
            BRIDGE + '/cmd', data=json.dumps({'c': c}).encode(),
            headers={'Content-Type': 'application/json'}), timeout=3).read()
    except Exception:
        pass


def _record_and_transcribe():
    """r -> wait -> d -> collect -> WAV -> whisper. Runs in its own thread."""
    import base64, subprocess, wave
    try:
        with lock:
            state['achunks'] = {}; state['adone'] = False
            state['rec'] = 'recording'; state['transcript'] = ''; state['clip'] = {}
        _send('m'); time.sleep(0.3)          # make sure the mic is up
        with lock:
            state['achunks'] = {}; state['adone'] = False
        _send('r')
        time.sleep(3.6)                       # REC_SECONDS is 3 on the board

        with lock: state['rec'] = 'downloading'
        _send('d')
        t0 = time.time()
        while time.time() - t0 < 40:
            with lock:
                if state['adone']: break
            time.sleep(0.1)
        with lock:
            chunks = dict(state['achunks']); rate = state['arate']; done = state['adone']
        if not chunks:
            with lock: state['rec'] = 'error'; state['transcript'] = 'no audio came back from the board'
            return

        raw = b''.join(base64.b64decode(chunks[i]) for i in sorted(chunks))
        pcm = array_from(raw)
        pk = int(max(abs(int(x)) for x in pcm)) if len(pcm) else 0
        rms = int((sum(float(x) * x for x in pcm) / max(len(pcm), 1)) ** 0.5)

        # DC removal (the MSM261 datasheet specifies ~4% FS offset) then peak normalise
        mean = sum(pcm) / max(len(pcm), 1)
        g = (32767 * 0.707) / max(max((abs(x - mean) for x in pcm), default=1), 1)
        out = bytearray()
        import struct
        for x in pcm:
            v = int(max(-32768, min(32767, (x - mean) * g)))
            out += struct.pack('<h', v)

        os.makedirs(CAPTURES, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        path = os.path.join(CAPTURES, 'voice-%s.wav' % stamp)
        with wave.open(path, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(bytes(out))
        with lock:
            state['clip'] = {'path': path, 'peak': pk, 'rms': rms,
                             'secs': round(len(pcm) / rate, 2), 'complete': done}
            state['rec'] = 'transcribing'

        if not os.path.exists(MODEL):
            with lock: state['rec'] = 'error'; state['transcript'] = 'model missing: ' + MODEL
            return
        r = subprocess.run([WHISPER, '-m', MODEL, '-f', path, '--language', 'en',
                            '--no-timestamps', '--suppress-nst', '--temperature', '0', '-nt'],
                           capture_output=True, text=True, timeout=120)
        text = ' '.join(l.strip() for l in r.stdout.splitlines()
                        if l.strip() and not l.startswith('whisper_') and not l.startswith('ggml_')).strip()
        with lock:
            state['transcript'] = text or '(nothing recognised)'
            state['rec'] = 'done'
    except Exception as e:
        with lock:
            state['rec'] = 'error'; state['transcript'] = 'failed: %s' % e


def array_from(raw):
    import struct
    n = len(raw) // 2
    return struct.unpack('<%dh' % n, raw[:n * 2])


def _rate():
    """mV per minute over the last ~90 s of battery samples."""
    bv=list(state['bv']); bt=list(state['bt'])
    if len(bv)<10: return 0.0
    t0=bt[-1]-90
    pts=[(t,v) for t,v in zip(bt,bv) if t>=t0]
    if len(pts)<10 or pts[-1][0]-pts[0][0] < 20: return 0.0
    dt=(pts[-1][0]-pts[0][0])/60.0
    return round((pts[-1][1]-pts[0][1])/dt, 1)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith('/battery'):
            body = BATPAGE.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if self.path == '/voice.wav':
            with lock: pth = state['clip'].get('path')
            if pth and os.path.exists(pth):
                data = open(pth, 'rb').read()
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers(); self.wfile.write(data)
            else:
                self.send_response(404); self.end_headers()
            return
        if self.path.startswith('/data'):
            with lock:
                body = json.dumps({
                    'ppg': list(state['ppg'])[-900:], 'thr': list(state['thr'])[-900:],
                    'fsr': list(state['fsr'])[-900:], 'mic': list(state['mic'])[-900:],
                    'filt': list(state['filt'])[-900:], 'fthr': list(state['fthr'])[-900:],
                    'amp': state['amp'], 'usable': state['usable'],
                    'mv': state['mv'], 'pct': state['pct'], 'chg': state['chg'],
                    'fast': state['fast'], 'sag': state['sag'],
                    'bv': list(state['bv'])[-600:], 'rate': _rate(),
                    'jitter': round(sum(state['bjit'])/len(state['bjit']), 1) if state['bjit'] else 0.0,
                    'rec': state['rec'], 'transcript': state['transcript'],
                    'clip': state['clip'],
                    'beats': list(state['beats'])[-12:], 'btn': list(state['btn'])[-12:],
                    'hap': state['hap'], 'vol': state['vol'], 'peak': state['peak'],
                    'btnDown': state['btnDown'], 'btnCount': state['btnCount'],
                    'live': (time.time() - state['lastLine']) < 2.0, 'seq': state['seq'],
                }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path == '/record':
            with lock:
                busy = state['rec'] in ('recording', 'downloading', 'transcribing')
            if not busy:
                threading.Thread(target=_record_and_transcribe, daemon=True).start()
            body = json.dumps({'started': not busy}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if self.path == '/cmd':
            n = int(self.headers.get('Content-Length', 0))
            payload = self.rfile.read(n)
            try:
                req = urllib.request.Request(BRIDGE + '/cmd', data=payload,
                                             headers={'Content-Type': 'application/json'})
                out = urllib.request.urlopen(req, timeout=3).read()
            except Exception as e:
                out = json.dumps({'sent': False, 'err': str(e)}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers(); self.wfile.write(out)


BATPAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'bat_pad.html'), encoding='utf-8').read()
PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'bench_scope.html'), encoding='utf-8').read()

if __name__ == '__main__':
    threading.Thread(target=pump, daemon=True).start()
    print(f"bench scope on http://localhost:{PORT}/  (bridge {BRIDGE})")
    HTTPServer(('127.0.0.1', PORT), H).serve_forever()
