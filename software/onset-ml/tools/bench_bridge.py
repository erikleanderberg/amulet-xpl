#!/usr/bin/env python3
"""
Amulet bench bridge -- XIAO (USB serial OR Bluetooth) -> live dashboard at http://localhost:8787

Transports, in order of preference:
  1. USB serial  /dev/cu.usbmodem*  (115200)          -- owns the port; close other monitors first
  2. BLE Nordic UART, device name "Amulet-XPL"        -- used whenever no USB board is present
Both carry the same protocol lines. Commands from the dashboard (/cmd) go back over whichever
link is up. Reconnects on its own. Recordings go to ~/Documents/Arduino/captures/.

Standard library + pyserial + bleak. Stop with Ctrl+C.
"""
import asyncio
import datetime
import glob
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # BLE optional: USB still works without bleak
    BleakClient = BleakScanner = None

HERE = os.path.dirname(os.path.abspath(__file__))
# Vendored into onset-ml (16 Sep 2026, from ~/Documents/Arduino/bench3_live/, BLE-capable version).
# Captures go into the repo so a session is self-contained; override with ONSET_CAPTURES=...
CAPTURES = os.environ.get('ONSET_CAPTURES') or os.path.join(os.path.dirname(HERE), 'data', 'captures')
HTTP_PORT = 8787
BLE_NAME = 'Amulet-XPL'
# Link RSSI, measured by this Mac's radio on the connected peripheral (CoreBluetooth readRSSI, polled).
# Emitted as an "R,<dbm>" line into the same stream and capture as the sensor lines, so RSSI is stored
# and time-aligned with everything else. macOS rate-limits readRSSI; the achieved rate is printed.
# The per-connection-event (~66 Hz) path needs the AmuletRSSI firmware on the board; this is the
# host-side path that works with bench3_live today. ONSET_RSSI_HZ=0 disables.
RSSI_HZ = float(os.environ.get('ONSET_RSSI_HZ', '10'))
# ONSET_PREFER_BLE=1: ignore USB serial and always use the Bluetooth link (e.g. board on USB for
# charging but the study path is BLE). Default: USB when present, BLE otherwise.
PREFER_BLE = os.environ.get('ONSET_PREFER_BLE', '0') == '1'
NUS_SVC = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
NUS_RX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'   # central writes commands here
NUS_TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'   # board notifies lines here

clients = set()
clients_lock = threading.Lock()
ser = None
ser_lock = threading.Lock()
ble = {'client': None, 'loop': None, 'name': ''}
rec = {'file': None, 'path': ''}
status = {'line': '#status,waiting,'}


def broadcast(batch):
    msg = ('data: ' + json.dumps(batch) + '\n\n').encode()
    with clients_lock:
        for q in list(clients):
            try:
                q.put_nowait(msg)
            except queue.Full:
                clients.discard(q)


def set_status(state, port=''):
    line = f'#status,{state},{port}'
    if line == status['line']:
        return
    status['line'] = line
    broadcast([[round(time.time(), 3), line]])


def record(now, line):
    f = rec['file']
    if f:
        f.write(f'{now:.3f},{line}\n')


def serial_up():
    with ser_lock:
        return ser is not None


def ble_up():
    c = ble['client']
    return c is not None and c.is_connected


# ---------------------------------------------------------------- USB serial
def serial_reader():
    global ser
    while True:
        ports = [] if PREFER_BLE else sorted(glob.glob('/dev/cu.usbmodem*'))
        if not ports:
            if not ble_up():
                set_status('waiting')
            time.sleep(1)
            continue
        try:
            s = serial.Serial(ports[0], 115200, timeout=0.05)
        except (serial.SerialException, OSError):
            set_status('busy', ports[0])
            time.sleep(1)
            continue
        with ser_lock:
            ser = s
        set_status('connected', ports[0])
        print(f'USB: connected to {ports[0]}')
        batch, last = [], time.time()
        try:
            while True:
                raw = s.readline()
                now = time.time()
                if raw:
                    line = raw.decode(errors='ignore').strip()
                    if line:
                        batch.append([round(now, 3), line])
                        record(now, line)
                if batch and now - last >= 0.02:
                    broadcast(batch)
                    batch, last = [], now
        except (serial.SerialException, OSError):
            print('USB: board disconnected -- waiting for it to come back')
        finally:
            with ser_lock:
                ser = None
            try:
                s.close()
            except Exception:
                pass
            if not ble_up():
                set_status('waiting')
            time.sleep(1)


# ---------------------------------------------------------------- BLE Nordic UART
async def ble_main():
    if BleakScanner is None:
        print('BLE: bleak not installed (pip3 install --user bleak) -- USB only')
        return
    ble['loop'] = asyncio.get_running_loop()
    while True:
        if serial_up():
            await asyncio.sleep(1)
            continue
        def match(d, a):
            name = d.name or a.local_name or ''
            return name.startswith(BLE_NAME) or NUS_SVC in [u.lower() for u in (a.service_uuids or [])]
        try:
            dev = await BleakScanner.find_device_by_filter(match, timeout=6.0)
        except Exception as e:
            print(f'BLE: scan failed: {e}')
            await asyncio.sleep(2)
            continue
        if dev is None:
            continue
        name = dev.name or BLE_NAME
        buf = bytearray()
        batch = []
        blast = [time.time()]

        def on_rx(_, data):
            now = time.time()
            buf.extend(data)
            while True:
                i = buf.find(b'\n')
                if i < 0:
                    break
                line = bytes(buf[:i]).decode(errors='ignore').strip()
                del buf[:i + 1]
                if line:
                    batch.append([round(now, 3), line])
                    record(now, line)

        try:
            async with BleakClient(dev) as client:
                ble['client'] = client
                ble['name'] = name
                try:
                    mtu = client.mtu_size
                except Exception:
                    mtu = '?'
                await client.start_notify(NUS_TX, on_rx)
                set_status('connected', f'BLE {name} (mtu {mtu})')
                print(f'BLE: connected to {name} mtu={mtu}')
                rssi_next = time.time(); rssi_n = 0; rssi_t0 = time.time(); rssi_fail = 0
                get_rssi = getattr(getattr(client, '_backend', None), 'get_rssi', None)
                while client.is_connected and not serial_up():
                    await asyncio.sleep(0.02)
                    now = time.time()
                    if RSSI_HZ > 0 and get_rssi is not None and now >= rssi_next:
                        rssi_next = now + 1.0 / RSSI_HZ
                        try:
                            v = int(await asyncio.wait_for(get_rssi(), timeout=0.5))
                            tr = time.time()
                            batch.append([round(tr, 3), f'R,{v}'])
                            record(tr, f'R,{v}')
                            rssi_n += 1
                            if tr - rssi_t0 >= 10:
                                print(f'BLE: link RSSI {v} dBm, {rssi_n / (tr - rssi_t0):.1f} reads/s achieved (asked {RSSI_HZ:g})')
                                rssi_n = 0; rssi_t0 = tr
                        except Exception as e:
                            rssi_fail += 1
                            if rssi_fail in (1, 10, 100):
                                print(f'BLE: readRSSI failed x{rssi_fail}: {e.__class__.__name__}: {e}')
                    if batch and now - blast[0] >= 0.02:
                        broadcast(list(batch))
                        batch.clear()
                        blast[0] = now
                if serial_up():
                    print('BLE: USB board appeared -- releasing the BLE link')
        except Exception as e:
            print(f'BLE: link error: {e}')
        finally:
            ble['client'] = None
            if not serial_up():
                set_status('waiting')
            print('BLE: disconnected')
            await asyncio.sleep(1)


def ble_send(c):
    client, loop = ble['client'], ble['loop']
    if client is None or loop is None or not client.is_connected:
        return False
    fut = asyncio.run_coroutine_threadsafe(client.write_gatt_char(NUS_RX, c.encode(), response=False), loop)
    try:
        fut.result(timeout=2)
        return True
    except Exception as e:
        print(f'BLE: command failed: {e}')
        return False


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return {}

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            with open(os.path.join(HERE, 'dashboard.html'), 'rb') as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            q = queue.Queue(maxsize=400)
            with clients_lock:
                clients.add(q)
            try:
                now = round(time.time(), 3)
                first = [[now, status['line']], [now, f'#rec,{rec["path"]}']]
                self.wfile.write(('data: ' + json.dumps(first) + '\n\n').encode())
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                    except queue.Empty:
                        msg = b': ping\n\n'
                    self.wfile.write(msg)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with clients_lock:
                    clients.discard(q)
        else:
            self.send_error(404)

    def do_POST(self):
        data = self._body()
        if self.path == '/record':
            if data.get('on') and not rec['file']:
                os.makedirs(CAPTURES, exist_ok=True)
                stamp = str(data.get('stamp') or datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))   # live.py passes the session stamp
                rec['path'] = os.path.join(CAPTURES, f'bench-{stamp}.csv')
                rec['file'] = open(rec['path'], 'w', buffering=1)
                rec['file'].write('host_time,type,fields...\n')
            elif not data.get('on') and rec['file']:
                rec['file'].close()
                rec['file'] = None
                rec['path'] = ''
            broadcast([[round(time.time(), 3), f'#rec,{rec["path"]}']])
            self._json({'recording': rec['path']})
        elif self.path == '/note':
            text = str(data.get('text', ''))[:80].replace('\n', ' ').replace(',', ';')
            now = time.time()
            record(now, f'N,{text}')
            broadcast([[round(now, 3), f'#note,{text}']])
            self._json({'ok': True})
        elif self.path == '/line':                      # a whole line to the board (future P,<hz>,<ms>,<vol> / DB commands)
            line = str(data.get('line', ''))[:200].replace('\n', '')
            ok = False
            if line:
                with ser_lock:
                    if ser is not None:
                        ser.write((line + '\n').encode()); ok = True
                if not ok:
                    ok = ble_send(line + '\n')
            self._json({'sent': ok, 'via': 'usb' if serial_up() else 'ble' if ok else None})
        elif self.path == '/cmd':
            c = str(data.get('c', ''))[:1]
            ok = False
            if c:
                with ser_lock:
                    if ser is not None:
                        ser.write(c.encode())
                        ok = True
                if not ok:
                    ok = ble_send(c)
            self._json({'sent': ok, 'via': 'usb' if serial_up() else 'ble' if ok else None})
        else:
            self.send_error(404)


def main():
    threading.Thread(target=serial_reader, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', HTTP_PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'Dashboard: http://localhost:{HTTP_PORT}   (Ctrl+C to stop)')
    try:
        asyncio.run(ble_main())
    except KeyboardInterrupt:
        pass
    finally:
        if rec['file']:
            rec['file'].close()


if __name__ == '__main__':
    main()
