#!/usr/bin/env python3
"""
mic_record.py -- record a clip from the XIAO Sense PDM mic, with a countdown so
you know exactly when to speak, then save a normalised WAV ready for ASR.

Works through bench_bridge (port 8787) if it is running, so the scope and
dashboard keep working. Falls back to the serial port directly.

  python tools/mic_record.py                 countdown, record, save
  python tools/mic_record.py --gain 80       set PDM gain first (0/20/40/60/80)
  python tools/mic_record.py --monitor       just show live levels, no recording
"""
import argparse, base64, glob, json, os, sys, time, urllib.request, wave
import numpy as np

BRIDGE = 'http://localhost:8787'
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'captures')


def bridge_up():
    try:
        urllib.request.urlopen(BRIDGE + '/cmd', data=b'{}',
                               timeout=2).read()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


class Link:
    """Talks to the board either through the bridge or straight down the port."""
    def __init__(self):
        self.mode = 'bridge' if bridge_up() else 'serial'
        if self.mode == 'serial':
            import serial
            p = sorted(glob.glob('/dev/cu.usbmodem*'))
            if not p:
                sys.exit("no board on USB, and no bridge on :8787")
            self.s = serial.Serial(p[0], 115200, timeout=0.3)
            self.s.dtr = True; time.sleep(1.2); self.s.reset_input_buffer()
        else:
            self.stream = urllib.request.urlopen(BRIDGE + '/events', timeout=60)
            self._buf = []
        print(f"link: {self.mode}")

    def send(self, c):
        if self.mode == 'serial':
            self.s.write(c.encode()); self.s.flush()
        else:
            urllib.request.urlopen(urllib.request.Request(
                BRIDGE + '/cmd', data=json.dumps({'c': c}).encode(),
                headers={'Content-Type': 'application/json'}), timeout=3).read()

    def lines(self, seconds):
        """Yield protocol lines for the next `seconds`."""
        t0 = time.time()
        if self.mode == 'serial':
            while time.time() - t0 < seconds:
                ln = self.s.readline().decode('utf-8', 'replace').strip()
                if ln:
                    yield ln
        else:
            import re
            pat = re.compile(r'"((?:[A-Z]),[^"]*)"')
            while time.time() - t0 < seconds:
                chunk = self.stream.readline().decode('utf-8', 'replace')
                for m in pat.findall(chunk):
                    yield m


def level_bar(rms, width=40):
    n = min(width, int(width * min(rms, 4000) / 4000))
    return '[' + '#' * n + '-' * (width - n) + f'] {rms:5d}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gain', type=int, choices=[0, 20, 40, 60, 80])
    ap.add_argument('--monitor', action='store_true')
    ap.add_argument('--no-normalise', action='store_true')
    a = ap.parse_args()

    link = Link()
    link.send('m'); time.sleep(1.0)          # make sure the mic is up

    if a.gain is not None:
        # 'g' steps 0->20->40->60->80->0; press until we land on the target
        for _ in range(6):
            got = None
            link.send('g')
            for ln in link.lines(1.2):
                if ln.startswith('I,mic on'):
                    got = int(ln.split('gain')[1].strip())
            if got == a.gain:
                print(f"gain set to {got}")
                break
        else:
            print(f"could not confirm gain {a.gain}")

    if a.monitor:
        print("\nlive level -- talk into the mic, ctrl-C to stop\n")
        try:
            while True:
                for ln in link.lines(0.5):
                    if ln.startswith('M,'):
                        f = ln.split(',')
                        print('\r' + level_bar(int(f[2])), end='', flush=True)
        except KeyboardInterrupt:
            print()
        return

    print("\n" + "=" * 46)
    for n in (3, 2, 1):
        print(f"   recording in {n}...", flush=True)
        time.sleep(1.0)
    print("   >>> SPEAK NOW <<<", flush=True)
    link.send('r')
    peak = 0
    for ln in link.lines(3.4):
        if ln.startswith('M,'):
            peak = max(peak, int(ln.split(',')[3]))
    print(f"   done. live peak during take: {peak}")
    print("=" * 46 + "\n")

    link.send('d')
    chunks, rate, total = {}, 16000, None
    for ln in link.lines(45):
        if ln.startswith('A,'):
            _, seq, payload = ln.split(',', 2)
            chunks[int(seq)] = payload
        elif ln.startswith('AEND'):
            p = ln.split(','); total, rate = int(p[1]), int(p[2]); break
    if not chunks:
        sys.exit("no audio returned -- is bench4_live flashed?")
    raw = b''.join(base64.b64decode(chunks[i]) for i in sorted(chunks))
    if total and len(raw) != total:
        print(f"WARNING got {len(raw)} bytes, board said {total}")
    pcm = np.frombuffer(raw[:(len(raw)//2)*2], dtype='<i2').astype(np.int16)

    pk = int(np.abs(pcm.astype(int)).max()); rms = int(np.sqrt((pcm.astype(float)**2).mean()))
    print(f"captured {len(pcm)} samples, {len(pcm)/rate:.2f}s @ {rate} Hz")
    print(f"  peak {pk}/32767 ({pk/327.67:.1f}% FS)   rms {rms}")

    out = pcm
    if not a.no_normalise and pk > 0:
        # DC-remove then normalise to -3 dBFS; ASR models want a healthy level
        f = pcm.astype(np.float32); f -= f.mean()
        g = (32767 * 0.707) / max(np.abs(f).max(), 1.0)
        out = np.clip(f * g, -32768, 32767).astype(np.int16)
        print(f"  normalised x{g:.1f} -> peak {int(np.abs(out.astype(int)).max())}")

    os.makedirs(OUTDIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    path = os.path.abspath(os.path.join(OUTDIR, f'voice-{stamp}.wav'))
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(out.tobytes())
    print(f"  WAV {path}")
    if pk < 500:
        print("\n  !! peak under 500 -- that is near silence. Raise gain (--gain 80),")
        print("     speak closer to the board, or check nothing covers the mic port.")


if __name__ == '__main__':
    main()
