#!/usr/bin/env python3
"""
battery_log.py -- measure real system current draw by watching the cell discharge.

Reads V lines from bench_bridge (so it shares the one BLE connection with the
scope rather than fighting it for the radio). Logs to captures/battery-<stamp>.csv
and prints a running estimate.

    CAPACITY_MAH below must match the actual cell.
    python tools/battery_log.py            log until ctrl-C
"""
import csv, os, re, sys, time, urllib.request

CAPACITY_MAH = 250.0
BRIDGE = 'http://localhost:8787'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'captures')
LINE = re.compile(r'"(V,[^"]*)"')


def main():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, 'battery-%s.csv' % time.strftime('%Y%m%d-%H%M%S'))
    f = open(path, 'w', newline=''); w = csv.writer(f)
    w.writerow(['unix_t', 'elapsed_s', 'mV', 'pct', 'charging', 'fast'])
    print(f"logging to {path}\ncapacity assumed {CAPACITY_MAH:.0f} mAh\n")
    print(f"{'elapsed':>9} {'mV':>6} {'pct':>4} {'chg':>4}   {'mV/min':>8} {'draw mA':>9}")

    t0 = None; first = None; rows = []
    while True:
        try:
            with urllib.request.urlopen(BRIDGE + '/events', timeout=30) as r:
                for chunk in r:
                    for ln in LINE.findall(chunk.decode('utf-8', 'replace')):
                        p = ln.split(',')
                        if len(p) < 6: continue
                        now = time.time()
                        mv, pct, chg, fast = int(p[2]), int(p[3]), int(p[4]), int(p[5])
                        if t0 is None: t0 = now
                        el = now - t0
                        w.writerow([round(now, 2), round(el, 1), mv, pct, chg, fast]); f.flush()
                        rows.append((now, mv, pct))
                        rows[:] = [x for x in rows if now - x[0] <= 900]   # 15 min window

                        if first is None and not chg: first = (now, mv, pct)
                        if len(rows) > 30 and now - rows[0][0] > 120:
                            dt_min = (rows[-1][0] - rows[0][0]) / 60.0
                            dmv = rows[-1][1] - rows[0][1]
                            dpct = rows[-1][2] - rows[0][2]
                            rate = dmv / dt_min
                            draw = abs(dpct) / 100.0 * CAPACITY_MAH / (dt_min / 60.0) if dpct else 0.0
                            tag = 'CHG' if chg else '---'
                            est = f"{draw:9.1f}" if (not chg and dpct < 0) else f"{'--':>9}"
                            print(f"\r{el/60:7.1f}m {mv:6d} {pct:4d} {tag:>4}   {rate:8.2f} {est}",
                                  end='', flush=True)
        except KeyboardInterrupt:
            print("\nstopped."); break
        except Exception:
            time.sleep(2)


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print()
