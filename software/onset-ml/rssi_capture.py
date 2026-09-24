#!/usr/bin/env python3
"""
RSSI capture for the palm XIAO running the Amulet RSSI firmware (amulet-firmware repo,
BLE contract v1.1). Records every sample for the whole session to disk, measures the real
rate and latency continuously, and forwards live to the onset GUI (live.py) if it is running.

How the signal is produced (from amulet-firmware/README.md): the XIAO is the BLE peripheral and
the RSSI *sensor*. This laptop is the central; it sends a link-layer packet every connection
interval whether or not it has data, the XIAO radio measures RSSI on each, batches
{int8 rssi_dbm, uint8 dt_ms} records and notifies them every 40 ms. ~66 Hz at a 15 ms interval.
macOS negotiates its own interval; the STATUS characteristic reports what was granted.

Sources:
  (default)      BLE: scan for service A3110000-6F1A-4B7C-9E2D-1C0A5E3B8D42, connect,
                 subscribe -> RESET_SEQ -> START, reconnect forever on loss
  --serial PORT  the firmware's 10 Hz "rssi:<dBm>" USB echo (bringup fallback, coarse)
  --demo         synthetic 66 Hz stream, no hardware (pipeline test)

Output: ~/Documents/Arduino/captures/rssi-<stamp>.csv, one row per event, written as it happens:
  host_time,type,a,b,c,d
    S  dev_ms, rssi_dbm, batch_seq, flag     flag: '' | 'gap' (dt saturated or seq gap) | 'first'
    T  fw, interval_units, achieved_hz, dropped     firmware STATUS, ~1 Hz
    E  text                                       connect / disconnect / errors / stats
host_time is time.time() at notification arrival, minus the dt of later samples in the same
batch, so it is the best estimate of when the radio measured that sample. The bench capture
(bench-<stamp>.csv) uses the same host clock, which is how the two files align.

Latency: BLE path = connection interval + up to 40 ms flush + macOS delivery. The E stats row
every 10 s reports observed rate, seq gaps, saturated dts, and mean batch age.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import glob
import json
import os
import random
import signal
import statistics
import sys
import threading
import time
import urllib.request

CAPTURES = os.environ.get('ONSET_CAPTURES') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'captures')
BASE = '-6f1a-4b7c-9e2d-1c0a5e3b8d42'
SVC, STREAM, CONTROL, STATUS = ('a3110000' + BASE, 'a3110001' + BASE, 'a3110002' + BASE, 'a3110003' + BASE)
CMD_START, CMD_STOP, CMD_RESET_SEQ = b'\x01', b'\x02', b'\x04'
LIVE_URL = "http://localhost:" + str(__import__("os").environ.get("ONSET_PORT", 8790)) + "/rssi"


class Log:
    def __init__(self, path: str | None):
        self.path = path
        self.f = open(path, 'a', buffering=1) if path else None
        if self.f and self.f.tell() == 0:
            self.f.write('host_time,type,a,b,c,d\n')
        self.pending: list[list] = []          # for live forward
        self.status: dict = {}
        self.n = 0; self.gaps = 0; self.sat = 0; self.t_first = None; self.ages: list[float] = []
        self.hz_win: list[float] = []

    def sample(self, host_t: float, dev_ms: int, rssi: int, seq: int, flag: str = '') -> None:
        if self.f:
            self.f.write(f'{host_t:.4f},S,{dev_ms},{rssi},{seq},{flag}\n')
        self.pending.append([round(host_t, 4), rssi])
        self.n += 1; self.t_first = self.t_first or host_t
        self.hz_win.append(host_t)
        if flag == 'gap':
            self.gaps += 1

    def status_row(self, host_t: float, fw: str, interval_units: int, hz: int, dropped: int) -> None:
        self.status = dict(fw=fw, interval_ms=round(interval_units * 1.25, 2), fw_hz=hz, dropped=dropped, t=host_t)
        if self.f:
            self.f.write(f'{host_t:.4f},T,{fw},{interval_units},{hz},{dropped}\n')

    def event(self, text: str) -> None:
        t = time.time()
        print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] {text}', flush=True)
        if self.f:
            self.f.write(f'{t:.4f},E,{text.replace(",", ";")}\n')

    def observed_hz(self) -> float:
        now = time.time()
        self.hz_win = [t for t in self.hz_win if now - t <= 2.0]
        return len(self.hz_win) / 2.0

    def stats_line(self) -> str:
        age = statistics.mean(self.ages) * 1000 if self.ages else 0
        self.ages = []
        return (f'stats: {self.n} samples, {self.observed_hz():.1f} Hz observed, '
                f'{self.gaps} gap flags, {self.sat} saturated dt, mean batch age {age:.0f} ms, '
                f'fw {self.status.get("fw_hz", "?")} Hz @ {self.status.get("interval_ms", "?")} ms, dropped {self.status.get("dropped", "?")}')


def forwarder(log: Log, stop: threading.Event) -> None:
    """POST pending samples + status to live.py every 100 ms. Never blocks logging."""
    while not stop.is_set():
        time.sleep(0.1)
        if not log.pending and not log.status:
            continue
        body = dict(samples=log.pending, status=dict(log.status, obs_hz=round(log.observed_hz(), 1), gaps=log.gaps))
        log.pending = []
        try:
            req = urllib.request.Request(LIVE_URL, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=0.3).read()
        except Exception:
            pass


# ---------------------------------------------------------------- BLE source
async def ble_source(log: Log, stop: threading.Event) -> None:
    from bleak import BleakClient, BleakScanner
    while not stop.is_set():
        log.event(f'scanning for Amulet service {SVC[:8]}...')
        dev = await BleakScanner.find_device_by_filter(
            lambda d, ad: SVC in [u.lower() for u in (ad.service_uuids or [])], timeout=10.0)
        if dev is None:
            continue
        disconnected = asyncio.Event()
        state = dict(dev_ms=0, last_seq=None, expect_first=True)

        def on_disc(_):
            disconnected.set()

        def on_stream(_, data: bytearray):
            arrival = time.time()
            seq, n = data[0], data[1]
            if len(data) != 2 + 2 * n:
                log.event(f'malformed notification len {len(data)} count {n}'); return
            recs = [(int.from_bytes(data[2 + 2 * i:3 + 2 * i], 'little', signed=True), data[3 + 2 * i]) for i in range(n)]
            seq_gap = state['last_seq'] is not None and (state['last_seq'] + 1) % 256 != seq
            state['last_seq'] = seq
            # host time of sample i = arrival - sum(dt of samples after i)
            tail = [0.0] * n
            acc = 0.0
            for i in range(n - 1, -1, -1):
                tail[i] = acc
                acc += recs[i][1] / 1000.0
            log.ages.append(acc)
            for i, (rssi, dt) in enumerate(recs):
                flag = ''
                if state['expect_first']:
                    flag = 'first'; state['expect_first'] = False
                elif dt == 255 or (i == 0 and seq_gap):
                    flag = 'gap'
                    if dt == 255: log.sat += 1
                state['dev_ms'] += dt
                log.sample(arrival - tail[i], state['dev_ms'], rssi, seq, flag)

        def on_status(_, b: bytearray):
            if len(b) >= 6:
                log.status_row(time.time(), f'{b[0]}.{b[1]}', int.from_bytes(b[2:4], 'little'), b[4], b[5])

        try:
            async with BleakClient(dev, disconnected_callback=on_disc) as client:
                log.event(f'connected to {dev.name or "Amulet"} [{dev.address}] mtu={client.mtu_size}')
                st = await client.read_gatt_char(STATUS)
                on_status(None, st)
                await client.start_notify(STREAM, on_stream)
                try:
                    await client.start_notify(STATUS, on_status)
                except Exception:
                    pass
                await client.write_gatt_char(CONTROL, CMD_RESET_SEQ, response=False)
                await client.write_gatt_char(CONTROL, CMD_START, response=False)
                log.event('streaming (subscribe -> RESET_SEQ -> START)')
                last_stats = time.time()
                while not stop.is_set() and not disconnected.is_set():
                    await asyncio.sleep(0.5)
                    if time.time() - last_stats >= 10:
                        last_stats = time.time(); log.event(log.stats_line())
                if not disconnected.is_set():
                    try:
                        await client.write_gatt_char(CONTROL, CMD_STOP, response=False)
                    except Exception:
                        pass
        except Exception as e:
            log.event(f'BLE error: {e.__class__.__name__}: {e}')
        log.event('disconnected' if not stop.is_set() else 'stopped')
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------- serial source
def serial_source(log: Log, port: str, stop: threading.Event) -> None:
    import serial
    while not stop.is_set():
        try:
            with serial.Serial(port, 115200, timeout=0.2) as s:
                log.event(f'serial {port} open (10 Hz rssi: echo)')
                last_stats = time.time()
                while not stop.is_set():
                    ln = s.readline().decode(errors='ignore').strip()
                    if ln.startswith('rssi:'):
                        try:
                            log.sample(time.time(), 0, int(ln[5:]), 0, '')
                        except ValueError:
                            pass
                    elif ln.startswith('rssi ') and '|' in ln:      # DBG status line
                        log.event(ln)
                    if time.time() - last_stats >= 10:
                        last_stats = time.time(); log.event(log.stats_line())
        except Exception as e:
            log.event(f'serial error: {e}'); time.sleep(1)


# ---------------------------------------------------------------- demo source
def demo_source(log: Log, stop: threading.Event) -> None:
    log.event('DEMO: synthetic 66 Hz RSSI, no hardware')
    dev_ms = 0; seq = 0; base = -50.0; t = time.time(); last_stats = t; last_status = t
    log.status_row(t, '1.0', 12, 66, 0)
    while not stop.is_set():
        dt = 15 + random.choice([0, 0, 0, 1, -1])
        t += dt / 1000.0
        d = t - time.time()
        if d > 0:
            time.sleep(d)
        dev_ms += dt
        base += random.gauss(0, 0.05); base = max(-70, min(-40, base))
        rssi = int(round(base + random.gauss(0, 1.2)))
        flag = 'gap' if random.random() < 0.002 else ''
        log.sample(t, dev_ms, rssi, seq, flag)
        if dev_ms // 40 != (dev_ms - dt) // 40:
            seq = (seq + 1) % 256
        if t - last_status >= 1:
            last_status = t; log.status_row(t, '1.0', 12, 66, 0)
        if t - last_stats >= 10:
            last_stats = t; log.event(log.stats_line())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--serial', help='read the firmware USB echo instead of BLE')
    ap.add_argument('--demo', action='store_true')
    ap.add_argument('--no-file', action='store_true', help='forward only, do not write a capture')
    ap.add_argument('--out', help='capture path (default captures/rssi-<stamp>.csv)')
    ap.add_argument('--captures', help='captures directory (default data/captures in the repo)')
    a = ap.parse_args()
    cap = a.captures or CAPTURES
    os.makedirs(cap, exist_ok=True)
    path = None if a.no_file else (a.out or os.path.join(cap, f'rssi-{datetime.datetime.now().strftime("%Y%m%d-%H%M%S")}.csv'))
    log = Log(path)
    if path:
        log.event(f'recording to {path}')
    stop = threading.Event()
    threading.Thread(target=forwarder, args=(log, stop), daemon=True).start()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    if a.demo:
        demo_source(log, stop)
    elif a.serial:
        serial_source(log, a.serial, stop)
    else:
        asyncio.run(ble_source(log, stop))
    log.event(log.stats_line())
    if log.f:
        log.f.close()


if __name__ == '__main__':
    main()
