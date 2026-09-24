"""
Haptic cue patterns for the TITAN Drake LRA (HF variant, f0 = 160 Hz) on the MAX98357A.

The pattern is a "decaying beep": a train of short bursts that starts fast and strong and
becomes slower and weaker. Everything is a parameter so the GUI can expose sliders.

Schema (keys match the serial command the firmware will parse, see serial_line()):
  f0, f1   carrier Hz for the first and last burst (linear sweep between)   40-300
  bd       burst duration ms                                                 6-60
  g0, g1   gap after the first / last burst, ms                              30-3000
  gc       gap curve: 'L' linear or 'E' exponential
  a0, a1   amplitude of first / last burst, 0-1
  ac       amplitude curve 'L' / 'E'
  n        burst count                                                       1-40
  at, rt   attack / release ms of each burst envelope                        0-20
  br       brake half-cycles after each burst (phase-inverted), 0-2
  sh       carrier shape 'Q' square (stronger) / 'S' sine (softer)

Guard (from the Drake datasheet limits and the amp's ~220 mA rms full scale into 8 ohm,
above the HF part's 145 mA rms rating): energy duty e = sum(bd * a^2) / total_ms <= 0.10,
no burst > 60 ms, gap >= 30 ms, total <= 8000 ms.
"""
from __future__ import annotations

import numpy as np

PRESETS = {
    'gentle':    dict(f0=130, f1=100, bd=25, g0=250, g1=1200, gc='E', a0=0.45, a1=0.10, ac='E', n=6,  at=6, rt=8, br=0, sh='S'),
    'standard':  dict(f0=160, f1=120, bd=18, g0=120, g1=900,  gc='E', a0=0.80, a1=0.12, ac='E', n=10, at=2, rt=3, br=1, sh='Q'),
    'insistent': dict(f0=200, f1=160, bd=15, g0=60,  g1=600,  gc='E', a0=1.00, a1=0.20, ac='E', n=16, at=1, rt=2, br=1, sh='Q'),
}
LIMITS = dict(f0=(40, 300), f1=(40, 300), bd=(6, 60), g0=(30, 3000), g1=(30, 3000), a0=(0, 1), a1=(0, 1),
              n=(1, 40), at=(0, 20), rt=(0, 20), br=(0, 2))


def _curve(v0: float, v1: float, k: int, n: int, kind: str) -> float:
    if n <= 1:
        return v0
    u = k / (n - 1)
    if kind == 'E' and v0 > 0 and v1 > 0:
        return float(v0 * (v1 / v0) ** u)
    return float(v0 + (v1 - v0) * u)


def normalise(p: dict) -> dict:
    q = dict(PRESETS['standard'])
    q.update({k: v for k, v in p.items() if k in q})
    for k, (lo, hi) in LIMITS.items():
        q[k] = float(min(max(float(q[k]), lo), hi))
    q['n'] = int(q['n']); q['br'] = int(q['br'])
    q['gc'] = 'E' if str(q['gc']).upper().startswith('E') else 'L'
    q['ac'] = 'E' if str(q['ac']).upper().startswith('E') else 'L'
    q['sh'] = 'S' if str(q['sh']).upper().startswith('S') else 'Q'
    return q


def schedule(p: dict) -> list[dict]:
    """Per-burst list: t_ms start, hz, amp, dur_ms, gap_ms."""
    p = normalise(p)
    out, t = [], 0.0
    for k in range(p['n']):
        hz = _curve(p['f0'], p['f1'], k, p['n'], 'L')
        amp = _curve(p['a0'], p['a1'], k, p['n'], p['ac'])
        gap = _curve(p['g0'], p['g1'], k, p['n'], p['gc'])
        out.append(dict(t_ms=t, hz=hz, amp=amp, dur_ms=p['bd'], gap_ms=gap))
        t += p['bd'] + gap
    return out


def guard(p: dict) -> tuple[bool, str, dict]:
    p = normalise(p)
    sch = schedule(p)
    total = sch[-1]['t_ms'] + p['bd'] if sch else 0
    energy = sum(b['dur_ms'] * b['amp'] ** 2 for b in sch)
    e = energy / total if total else 0
    info = dict(total_ms=total, e=e, n=p['n'])
    if total > 8000:
        return False, 'total > 8000 ms', info
    if e > 0.10:
        return False, f'energy duty {e:.3f} > 0.10', info
    return True, 'ok', info


def render(p: dict, sr: int = 16000) -> np.ndarray:
    """Mono float32 waveform in [-1, 1] for preview / WAV / future I2S streaming."""
    p = normalise(p)
    sch = schedule(p)
    total = (sch[-1]['t_ms'] + p['bd'] + 50) / 1000 if sch else 0.1
    y = np.zeros(int(total * sr), dtype=np.float32)
    for b in sch:
        n = int(b['dur_ms'] / 1000 * sr)
        i0 = int(b['t_ms'] / 1000 * sr)
        t = np.arange(n) / sr
        car = np.sin(2 * np.pi * b['hz'] * t)
        if p['sh'] == 'Q':
            car = np.sign(car)
        env = np.ones(n)
        a = int(p['at'] / 1000 * sr); r = int(p['rt'] / 1000 * sr)
        if a > 0: env[:a] = np.linspace(0, 1, a)
        if r > 0: env[n - r:] = np.linspace(1, 0, r)
        y[i0:i0 + n] += (b['amp'] * env * car).astype(np.float32)
        if p['br'] > 0:                                   # phase-inverted brake half-cycles
            hb = int(p['br'] * 0.5 / b['hz'] * sr)
            tb = np.arange(hb) / sr
            brake = -np.sin(2 * np.pi * b['hz'] * (tb + b['dur_ms'] / 1000)) * b['amp'] * 1.1
            y[i0 + n:i0 + n + hb] += brake.astype(np.float32)[: max(0, len(y) - i0 - n)]
    return np.clip(y, -1, 1)


def serial_line(p: dict) -> str:
    """One ASCII line for the firmware (to be implemented in bench4): 'DB k=v k=v ...'."""
    p = normalise(p)
    parts = [f'{k}={p[k]:g}' if isinstance(p[k], float) else f'{k}={p[k]}' for k in
             ('f0', 'f1', 'bd', 'g0', 'g1', 'gc', 'a0', 'a1', 'ac', 'n', 'at', 'rt', 'br', 'sh')]
    return 'DB ' + ' '.join(parts)


def write_wav(p: dict, path: str, sr: int = 16000) -> None:
    import wave
    y = (render(p, sr) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.tobytes())


if __name__ == '__main__':
    for name, p in PRESETS.items():
        ok, why, info = guard(p)
        print(f'{name:10s} {serial_line(p)}\n           total {info["total_ms"]:.0f} ms  e={info["e"]:.3f}  guard={why}')
