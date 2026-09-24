#!/usr/bin/env python3
"""
mic_capture.py -- record from the XIAO Sense PDM mic via bench4_live, bring the
audio back over USB, write a WAV, plot it, and play it.

The LRA cannot reproduce speech (it is a 160 Hz resonant actuator), so "listen
back" happens here on the laptop, not on the board.

  python tools/mic_capture.py              record 3 s, save, plot, play
  python tools/mic_capture.py --no-play    skip playback
  python tools/mic_capture.py --latency    run the acoustic loopback probe
"""
import argparse, base64, glob, os, subprocess, sys, time, wave
import numpy as np
import serial

RATE_DEFAULT = 16000
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'captures')


def port():
    p = sorted(glob.glob('/dev/cu.usbmodem*'))
    if not p:
        sys.exit("no /dev/cu.usbmodem* -- plug the XIAO in (and stop bench_bridge.py, it holds the port)")
    return p[0]


def open_board():
    s = serial.Serial(port(), 115200, timeout=0.3)
    s.dtr = True
    time.sleep(1.2)
    s.reset_input_buffer()
    return s


def drain(s, seconds, show=('I,', 'AEND')):
    out, t = [], time.time()
    while time.time() - t < seconds:
        line = s.readline().decode('utf-8', 'replace').strip()
        if not line:
            continue
        out.append(line)
        if any(line.startswith(p) for p in show):
            print("   ", line)
    return out


def capture(s, rec_seconds=3.0):
    print(">>> r  (recording)")
    s.write(b'r'); s.flush()
    drain(s, rec_seconds + 1.2)

    print(">>> d  (downloading)")
    s.write(b'd'); s.flush()
    chunks, rate, total, t0 = {}, RATE_DEFAULT, None, time.time()
    while time.time() - t0 < 60:
        line = s.readline().decode('utf-8', 'replace').strip()
        if not line:
            continue
        if line.startswith('A,'):
            _, seq, payload = line.split(',', 2)
            chunks[int(seq)] = payload
        elif line.startswith('AEND'):
            parts = line.split(',')
            total, rate = int(parts[1]), int(parts[2])
            print("   ", line)
            break
        elif line.startswith('I,'):
            print("   ", line)
    if not chunks:
        sys.exit("no audio came back -- is bench4_live flashed? (bench3 has no mic)")

    raw = b''.join(base64.b64decode(chunks[i]) for i in sorted(chunks))
    if total and len(raw) != total:
        print(f"    WARNING: got {len(raw)} bytes, board said {total}")
    return np.frombuffer(raw[: (len(raw) // 2) * 2], dtype='<i2'), rate


def save_wav(pcm, rate, path):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def plot(pcm, rate, path):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("    (matplotlib not installed -- skipping plot; pip install matplotlib)")
        return None
    t = np.arange(len(pcm)) / rate
    fig, ax = plt.subplots(2, 1, figsize=(11, 6), constrained_layout=True)
    ax[0].plot(t, pcm, lw=0.4, color='#1F6F8B')
    ax[0].set_xlabel('seconds'); ax[0].set_ylabel('amplitude')
    ax[0].set_title(f'{len(pcm)} samples @ {rate} Hz   peak {int(np.abs(pcm).max())}   rms {int(np.sqrt((pcm.astype(float)**2).mean()))}')
    ax[0].set_xlim(0, t[-1] if len(t) else 1)
    ax[1].specgram(pcm.astype(float), Fs=rate, NFFT=512, noverlap=384, cmap='magma')
    ax[1].set_xlabel('seconds'); ax[1].set_ylabel('Hz')
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def latency(s, n=5):
    print(">>> l  x%d  (tap -> mic acoustic loopback)" % n)
    s.write(b'm'); s.flush(); drain(s, 1.5)
    vals = []
    for i in range(n):
        s.write(b'l'); s.flush()
        for line in drain(s, 2.5):
            if 'latency' in line:
                try: vals.append(int(line.split('latency')[1].split('us')[0].strip()))
                except ValueError: pass
        time.sleep(0.5)
    if vals:
        a = np.array(vals) / 1000.0
        print(f"\n    n={len(a)}  min {a.min():.1f} ms  median {np.median(a):.1f} ms  max {a.max():.1f} ms")
    else:
        print("    no latency lines -- try 'g' to raise mic gain, or move the LRA nearer the mic")
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-play', action='store_true')
    ap.add_argument('--latency', action='store_true')
    ap.add_argument('--seconds', type=float, default=3.0)
    a = ap.parse_args()

    s = open_board()
    print(f"connected: {s.port}")
    try:
        if a.latency:
            latency(s); return
        pcm, rate = capture(s, a.seconds)
    finally:
        try: s.close()
        except Exception: pass

    os.makedirs(OUTDIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    wav = os.path.abspath(os.path.join(OUTDIR, f'mic-{stamp}.wav'))
    png = os.path.abspath(os.path.join(OUTDIR, f'mic-{stamp}.png'))
    save_wav(pcm, rate, wav)
    dur = len(pcm) / rate
    print(f"\n    {len(pcm)} samples, {dur:.2f} s @ {rate} Hz")
    print(f"    peak {int(np.abs(pcm).max())} / 32767    rms {int(np.sqrt((pcm.astype(float)**2).mean()))}")
    print(f"    WAV  {wav}")
    if plot(pcm, rate, png):
        print(f"    PLOT {png}")
    if not a.no_play:
        print("    playing...")
        subprocess.run(['afplay', wav])


if __name__ == '__main__':
    main()
