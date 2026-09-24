#!/usr/bin/env python3
"""
bat_tap.py -- live BAT-pad readout for touch-testing the battery + wire.

Prints every V line with a marker when the reading moves. With USB connected the
BQ25101 drives the BAT node on its own, so a bare pad still shows ~4.2 V -- what
proves a real cell is touching is that the voltage SETTLES to the cell's own
level and stops drifting, and that it barely moves when loaded.
"""
import re, sys, time, urllib.request
LINE = re.compile(r'"(V,[^"]*)"')
BRIDGE = 'http://localhost:8787'
print("tap the battery + wire onto BAT+ and hold for ~3 s\n")
print(f"{'t':>6}  {'mV':>6}  {'pct':>4}  {'chg':>4}  {'d_mV':>6}   trace")
prev = None; t0 = time.time()
while True:
    try:
        with urllib.request.urlopen(BRIDGE + '/events', timeout=30) as r:
            for chunk in r:
                for ln in LINE.findall(chunk.decode('utf-8', 'replace')):
                    p = ln.split(',')
                    if len(p) < 6: continue
                    mv, pct, chg = int(p[2]), int(p[3]), int(p[4])
                    d = 0 if prev is None else mv - prev
                    prev = mv
                    bar = ''
                    if abs(d) >= 15: bar = '  <<<<<< CHANGE'
                    elif abs(d) >= 5: bar = '  <<< moved'
                    print(f"{time.time()-t0:6.1f}  {mv:6d}  {pct:4d}  {'CHG' if chg else '--':>4}  "
                          f"{d:+6d}{bar}", flush=True)
    except KeyboardInterrupt:
        break
    except Exception:
        time.sleep(1)
