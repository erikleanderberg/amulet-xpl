#!/usr/bin/env python3
"""
Live sleep-onset detector + haptic cue controller.   http://localhost:8790

Sources (pick one):
  --replay data/synthetic/synth-onset-03.csv [--speed 8]   replay a capture (default when given)
  (no flag)   subscribe to the bench bridge's SSE stream at http://localhost:8787/events
              (bench_bridge.py must be running; it owns the serial port and the recording)

The detector is whatever train.py currently defines (build_features / fit / predict), fed
causally one second at a time, so the GUI shows exactly what evaluate.py scores.

HTTP API (consumed by gui.html):
  GET  /               gui.html
  GET  /state          full config: thresholds, armed, pattern, presets, mode, calib
  GET  /events         SSE. Each message is one JSON object with a "type":
        raw    ~10/s   {t, fsr:[..], ppg:[..], rssi:[..], bpm, ibi}         new samples since last
        feat   1/s     {t, score, thresh, hold, hold_run, state, calib_left, features:{}, z:{},
                        trigger, armed, last_fire_t, probe_misses}
        event          {t, text}      notes, fires, probe results, onset marks, source status
  POST /set    {thresh?, hold?, smooth?, armed?, pattern?, preset?, haptic_mode?, probe_s?}
  POST /fire   {}                     fire the current pattern now (manual)
  POST /note   {text}                 label:awake|drowsy|asleep, probe:hit|miss, free text
  POST /record {on: bool}             passthrough to the bridge (live mode only)
  POST /reset  {}                     drop buffers, restart calibration
  GET  /pattern.wav                   current pattern rendered (for a quick listen)

Haptic modes:
  none     log only
  tap      send 'b' (150 ms tap) to the bridge -> current bench3 firmware. Placeholder.
  pattern  send the DB line (onset/haptic.serial_line). Needs the bench4 firmware parser and a
           bridge /line endpoint -- both are the next hardware step; until then it only logs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import queue
import threading
import time
import pathlib
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import train
from onset import haptic
from onset import session as filing
from onset.features import FEATURES, MIN_HISTORY, feature_row
from onset.parse import FS, Session
from onset.voicenote import VoiceNote
from onset import transcribe as stt
from onset import journal

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8790
BRIDGE = os.environ.get('ONSET_BRIDGE', 'http://localhost:8787')   # ONSET_BRIDGE points at a fake board for UI work
REFRACTORY_S = 60.0
RAW_WIN_S = 60.0          # seconds of 100 Hz trace the GUI draws (must match WIN_S in gui.html)
HIST_WIN_S = 660.0        # seconds of detector history the GUI draws (DET_WIN + margin)
BTN_DOUBLE_MS = 450.0     # two presses closer than this on the board clock = double-click
REC_SECONDS = 3.0         # bench4_live's REC_SECONDS; it records for this long on 'r'
SECOND_OPINION = os.environ.get('ONSET_STT_SECOND', '0') == '1'   # run a 2nd engine and keep both

lock = threading.RLock()
clients: set[queue.Queue] = set()
cfg = dict(thresh=getattr(train, 'THRESH', 1.0), hold=train.HOLD_S, smooth=train.SMOOTH_S, armed=False,
           preset='standard', pattern=dict(haptic.PRESETS['standard']), haptic_mode='tap',   # 'tap' is what bench3 firmware can do today
           probe_s=20, mode='idle', source='',
           # Horowitz 2020 sec. 2.4: after Dormio-detected onset "a variable timer ... instigated wakeups from
           # 1:00 to 5:00 minutes after" onset. 0/0 = fire immediately (our default for latency measurement).
           delay_min_s=0, delay_max_s=0, model=train.MODEL)


class Buf:
    """Full-session sample buffers on the seq grid + feature rows."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.t0_seq = None
        self.fsr: list[float] = []; self.ppg: list[float] = []
        self.beat_t: list[float] = []; self.beat_ibi: list[float] = []; self.beat_bpm: list[float] = []
        self.notes: list[tuple[float, str]] = []
        self.user_notes: list[tuple[float, str]] = []      # notes made here (labels), not ones read from a replay file
        self.times: list[float] = []; self.rows: list[list[float]] = []
        self.hist: list[list] = []        # per second: [t, score, thresh, hold_run, hold], for GUI backfill
        self.next_feat_t = MIN_HISTORY
        self.last_fire_t = -1e9; self.hold_run = 0; self.probe_misses = 0
        self.pending_fire_t = None; self.detect_t = None
        self.pending = dict(fsr=[], ppg=[], rssi=[], bpm=None, ibi=None)
        self.cur = 0
        self.rssi: list[float] = []          # forward-filled per 100 Hz tick, parallel to fsr
        self.rssi_last = np.nan; self.rssi_stats: dict = {}; self.rssi_n = 0; self.rssi_times: list[float] = []
        self.rssi_fresh = False              # a real R sample arrived since the last raw flush
        self.host_t0 = None                  # host time of grid t = 0

    @property
    def t_now(self) -> float:
        return (len(self.fsr) - 1) / FS if self.fsr else 0.0

    def session(self) -> Session:
        return Session('live', np.arange(len(self.fsr)) / FS, np.array(self.fsr), np.array(self.ppg),
                       np.array(self.beat_t), np.array(self.beat_ibi),
                       np.array(self.beat_bpm), list(self.notes),
                       rssi=np.array(self.rssi) if self.rssi_n else None)


buf = Buf()
model = None
voice = VoiceNote()
voice_done = threading.Event()   # set when AEND closes a download
btn = dict(last_ms=None, presses=0, down=False, t=0.0)
battery: dict = {}
mic = dict(rms=0, peak=0, t=0.0)
# The board owns the session clock now, so the host mirrors it rather than driving it.
board_sess = dict(state='idle', elapsed=0, left=0, takes=0, t=0.0)
sess = dict(active=False, stamp=None, name='', start_host=None, start_t=None, last=None)   # current nap session
REPLAY_SRC = None
DATA_DIRS = ['data/sessions', 'data/synthetic']


def _cached_features(sess: Session, path: str):
    """build_features with an on-disk cache keyed on the capture and on features.py/train.py mtimes."""
    import hashlib
    from onset import features as fmod
    key = f'{path}:{os.path.getmtime(path)}:{os.path.getmtime(fmod.__file__)}:{os.path.getmtime(train.__file__)}'
    cdir = os.path.join(HERE, 'data', '.feat_cache'); os.makedirs(cdir, exist_ok=True)
    cp = os.path.join(cdir, hashlib.md5(key.encode()).hexdigest() + '.npz')
    if os.path.exists(cp):
        z = np.load(cp); return z['t'], z['X']
    t, X = train.build_features(sess)
    np.savez(cp, t=t, X=X)
    return t, X


def fit_model() -> None:
    """Fit train.py's detector on every session on disk (real first, synthetic as filler)."""
    global model
    from onset.parse import load_dir
    train_list = []
    for d in DATA_DIRS:
        dd = os.path.join(HERE, d)
        if os.path.isdir(dd):
            for sess, path in zip(load_dir(dd), sorted(p for p in os.listdir(dd) if p.endswith('.csv'))):
                t, X = _cached_features(sess, os.path.join(dd, path))
                train_list.append((sess, t, X))
    try:
        model = train.fit(train_list)
    except Exception as e:                       # detector needs data we do not have yet
        print(f'fit failed on {len(train_list)} sessions ({e}); falling back to fit([])')
        model = train.fit([])
    if isinstance(model, dict):
        for k in ('thresh', 'hold', 'smooth'):
            if k in model:
                cfg[k] = model[k]
    print(f'detector fitted on {len(train_list)} sessions; thresh={cfg["thresh"]} hold={cfg["hold"]} smooth={cfg["smooth"]}')


def broadcast(obj: dict) -> None:
    msg = ('data: ' + json.dumps(obj) + '\n\n').encode()
    for q in list(clients):
        try:
            q.put_nowait(msg)
        except queue.Full:
            clients.discard(q)


def event(text: str, t: float | None = None) -> None:
    t = buf.t_now if t is None else t
    broadcast(dict(type='event', t=round(t, 2), text=text))


def bridge_call(path: str, data: dict):
    """POST to the bridge; returns the parsed JSON reply or None if unreachable."""
    try:
        req = urllib.request.Request(BRIDGE + path, data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'})
        return json.loads(urllib.request.urlopen(req, timeout=2).read() or b'{}')
    except Exception:
        return None


def bridge_post(path: str, data: dict) -> bool:
    return bridge_call(path, data) is not None


def note(text: str) -> None:
    with lock:
        buf.notes.append((buf.t_now, text)); buf.user_notes.append((buf.t_now, text))
    if cfg['mode'] == 'live':
        bridge_post('/note', dict(text=text))
    event(text)
    if text.startswith('probe:'):
        with lock:
            buf.probe_misses = buf.probe_misses + 1 if text == 'probe:miss' else 0
            two = buf.probe_misses == 2
        if two:
            note('onset')       # Ogilvie rule: two consecutive missed probes = sleep onset


def fire(reason: str) -> None:
    p = cfg['pattern']
    ok, why, info = haptic.guard(p)
    if not ok:
        event(f'fire blocked: {why}'); return
    with lock:
        buf.last_fire_t = buf.t_now
    mode = cfg['haptic_mode']
    sent = ''
    if cfg['mode'] == 'live':
        if mode == 'tap':
            r = bridge_call('/cmd', dict(c='b'))
            sent = f'tap sent via {r.get("via")}' if r and r.get('sent') else 'NOT SENT: bridge down or no board link'
        elif mode == 'pattern':
            r = bridge_call('/line', dict(line=haptic.serial_line(p)))
            sent = 'DB line sent (firmware does not parse it yet: no vibration)' if r and r.get('sent') else 'NOT SENT: bridge down'
        else:
            sent = 'NOT SENT: haptic mode is "none" (log only) -- pick tap'
    note(f'haptic:{cfg["preset"]}:{reason}')
    event(f'FIRE {cfg["preset"]} ({reason}) {info["total_ms"]:.0f} ms  {sent}')
    broadcast(dict(type='fire', t=round(buf.last_fire_t, 2), pattern=haptic.normalise(p), reason=reason,
                   ask_report=True))     # GUI asks "And were you asleep?" (Awake / Halfway / Asleep)


def session_start(name: str, notes: str) -> dict:
    if sess['active']:
        return dict(ok=False, error='session already running')
    with lock:
        buf.reset()
    stamp = filing.new_stamp()
    sess.update(active=True, stamp=stamp, name=name[:40], notes=notes[:200], start_host=time.time(), start_t=0.0, last=None)
    if cfg['mode'] == 'live':
        r = bridge_call('/record', dict(on=True, stamp=stamp))
        if not r or not r.get('recording'):
            sess['active'] = False
            return dict(ok=False, error='bridge not reachable or already recording; is the bridge running?')
        sess['rec_path'] = r['recording']              # whatever the bridge chose (an older bridge ignores the stamp)
    note(f'session:start:{name}')
    event(f'SESSION {stamp} started ({name or "unnamed"})')
    broadcast(dict(type='session', state='running', stamp=stamp, name=name))
    return dict(ok=True, stamp=stamp)


def session_end(notes: str) -> dict:
    if not sess['active']:
        return dict(ok=False, error='no session running')
    note('session:end')
    sess['active'] = False
    end_host = time.time()
    if cfg['mode'] == 'live':
        bridge_post('/record', dict(on=False))
        time.sleep(0.6)                                   # let the bridge close the file
        src = pathlib.Path(sess.get('rec_path') or filing.CAPTURES / f'bench-{sess["stamp"]}.csv')
        replay_notes = None
    else:
        src = pathlib.Path(REPLAY_SRC); replay_notes = list(buf.user_notes)
    if not src.exists():
        return dict(ok=False, error=f'capture not found: {src}')
    try:
        with lock:
            host_t0 = buf.host_t0 or sess['start_host']
        meta = filing.file_session(sess['stamp'], sess['name'], notes or sess.get('notes', ''), src,
                                   sess['start_host'], end_host, dict(cfg), replay_notes, host_t0)
    except Exception as e:
        event(f'session filing failed: {e.__class__.__name__}: {e}')
        return dict(ok=False, error=f'{e.__class__.__name__}: {e}')
    sess['last'] = meta
    sm = meta['summary']
    event(f'SESSION {sess["stamp"]} filed: {sm["duration_min"]} min, onset {sm["onset_s"]}, first cue {sm["first_cue_s"]}, '
          f'latency {sm["cue_latency_s"]} s, dHR {sm["d_hr_bpm"]}, dFlex {sm["d_flex_kohm"]}')
    broadcast(dict(type='session', state='ended', meta=meta))
    def _review():
        try:
            filing.regenerate_review(); event('review page regenerated: /review')
        except Exception as e:
            event(f'review regeneration failed: {e}')
    threading.Thread(target=_review, daemon=True).start()
    return dict(ok=True, meta=meta)


def step_features() -> None:
    """Called whenever a whole second of stream has arrived."""
    with lock:
        tn = buf.next_feat_t
        buf.next_feat_t += 1.0
        s = buf.session()
        row = feature_row(tn, s.t, s.fsr, s.ppg, s.beat_t, s.beat_ibi, s.rssi)
        buf.times.append(tn); buf.rows.append(row)
        times = np.array(buf.times); X = np.array(buf.rows)
        if isinstance(model, dict):
            model.update(thresh=cfg['thresh'], hold=cfg['hold'], smooth=cfg['smooth'])
        score = train.drowsiness_score(model, times, X) if hasattr(train, 'drowsiness_score') else np.zeros(len(times))
        trig = train.predict(model, times, X, s)
        sc = float(score[-1]) if np.isfinite(score[-1]) else 0.0
        buf.hold_run = buf.hold_run + 1 if sc > cfg['thresh'] else 0
        calib_left = max(0.0, train.CALIB_S - tn)
        in_refr = tn - buf.last_fire_t < REFRACTORY_S
        state = ('calibrating' if calib_left > 0 else 'cue pending' if buf.pending_fire_t is not None
                 else 'refractory' if in_refr else 'watching')
        fire_now = bool(trig[-1]) and calib_left == 0 and not in_refr
        mu = np.nanmean(X[times <= train.CALIB_S], axis=0) if (times <= train.CALIB_S).sum() >= 10 else np.full(len(FEATURES), np.nan)
        sd = np.nanstd(X[times <= train.CALIB_S], axis=0) + 1e-6 if (times <= train.CALIB_S).sum() >= 10 else np.full(len(FEATURES), np.nan)
        z = {n: (None if not np.isfinite(v) else round(float((v - mu[i]) / sd[i]), 2)) for i, (n, v) in enumerate(zip(FEATURES, row))}
        feats = {n: (None if not np.isfinite(v) else round(float(v), 3)) for n, v in zip(FEATURES, row)}
        buf.hist.append([round(tn, 1), round(sc, 3), cfg['thresh'], buf.hold_run, cfg['hold']])
        if len(buf.hist) > 14400: del buf.hist[:-14400]      # 4 h ceiling; the payload is sliced to HIST_WIN_S
    broadcast(dict(type='feat', t=round(tn, 1), score=round(sc, 3), thresh=cfg['thresh'], hold=cfg['hold'],
                   hold_run=buf.hold_run, state=state, calib_left=round(calib_left), features=feats, z=z,
                   trigger=fire_now, armed=cfg['armed'], last_fire_t=round(buf.last_fire_t, 1),
                   probe_misses=buf.probe_misses, rssi_stats=buf.rssi_stats,
                   rssi_last=(None if not np.isfinite(buf.rssi_last) else buf.rssi_last)))
    # Dormio timing: detection starts a variable timer; the cue fires when it expires
    with lock:
        pend = buf.pending_fire_t
    if pend is not None and tn >= pend:
        with lock:
            buf.pending_fire_t = None
        if cfg['armed']:
            fire(f'auto after {tn - buf.detect_t:.0f} s delay')
        else:
            event('delayed cue due now (not armed)')
    elif fire_now and pend is None:
        dmin, dmax = float(cfg['delay_min_s']), float(cfg['delay_max_s'])
        if dmax > 0:
            import random
            wait = random.uniform(dmin, max(dmin, dmax))
            with lock:
                buf.pending_fire_t = tn + wait; buf.detect_t = tn; buf.last_fire_t = tn
            note('detected')
            event(f'onset detected; cue in {wait:.0f} s (Dormio timer {dmin:.0f}-{dmax:.0f} s)')
        elif cfg['armed']:
            note('detected'); fire('auto')
        else:
            note('detected'); event('detector would fire (not armed)')
            with lock:
                buf.last_fire_t = tn      # refractory anyway so the log is not spammed


def on_line(line: str, host_t: float | None = None) -> None:
    """One serial line (without host time). host_t = the bridge/capture host_time, if known."""
    parts = line.split(',')
    typ = parts[0]
    dbl = False; push = None; phase_change = None; take_ready = False
    with lock:
        try:
            if typ == 'S':                                          # v2: ms,fsr,pulse,thresh[,ignored]
                seq = int(parts[1]) // 10; v = float(parts[2])
                if buf.t0_seq is None:
                    buf.t0_seq = seq
                idx = seq - buf.t0_seq
                while len(buf.fsr) < idx:
                    buf.fsr.append(buf.fsr[-1] if buf.fsr else v); buf.ppg.append(buf.ppg[-1] if buf.ppg else np.nan)
                ppg = float(parts[3])
                while len(buf.rssi) < idx: buf.rssi.append(buf.rssi_last)
                buf.fsr.append(v); buf.ppg.append(ppg); buf.rssi.append(buf.rssi_last)
                if buf.host_t0 is None: buf.host_t0 = (host_t if host_t else time.time()) - idx / FS
                buf.pending['fsr'].append(v); buf.pending['ppg'].append(ppg)
                buf.pending['rssi'].append(buf.rssi_last)
            elif typ == 'F':
                seq = int(parts[1]); v = float(parts[2])
                if buf.t0_seq is None:
                    buf.t0_seq = seq
                idx = seq - buf.t0_seq
                while len(buf.fsr) < idx:               # dropped packets -> hold last value
                    buf.fsr.append(buf.fsr[-1] if buf.fsr else v); buf.ppg.append(buf.ppg[-1] if buf.ppg else np.nan)
                while len(buf.rssi) < idx: buf.rssi.append(buf.rssi_last)
                buf.fsr.append(v); buf.ppg.append(np.nan); buf.rssi.append(buf.rssi_last)
                if buf.host_t0 is None: buf.host_t0 = (host_t if host_t else time.time()) - idx / FS
                buf.pending['fsr'].append(v)
            elif typ == 'P' and buf.ppg:
                buf.ppg[-1] = float(parts[1]); buf.pending['ppg'].append(float(parts[1]))
            elif typ == 'B':
                if len(parts) >= 4:                                 # v2: ms,bpm,ibi
                    bt = (int(parts[1]) // 10 - (buf.t0_seq or 0)) / FS; bpm, ibi = float(parts[2]), float(parts[3])
                else:                                               # v1: bpm,ibi
                    bt, bpm, ibi = buf.t_now, float(parts[1]), float(parts[2])
                buf.beat_t.append(bt); buf.beat_bpm.append(bpm); buf.beat_ibi.append(ibi)
                buf.pending['bpm'] = bpm; buf.pending['ibi'] = ibi
            elif typ == 'R':                                        # R,<ms>,<rssi> from the board (per connection event) or R,<rssi> from the bridge
                v = float(parts[2] if len(parts) >= 3 else parts[1]); ht = host_t if host_t else time.time()
                src = 'board' if len(parts) >= 3 else 'bridge readRSSI'
                buf.rssi_last = v; buf.rssi_n += 1; buf.rssi_fresh = True
                buf.rssi_times.append(ht); buf.rssi_times = [x for x in buf.rssi_times if ht - x <= 2.0]
                buf.rssi_stats = dict(fw=src, interval_ms=None, fw_hz=None, dropped=None,
                                      obs_hz=round(len(buf.rssi_times) / 2.0, 1), gaps=0, link='streaming')
            elif typ == 'G':                                    # G,<ms>,<state>,<elapsed>,<left>,<takes>
                prev = board_sess['state']
                board_sess.update(state=parts[2], elapsed=int(parts[3]), left=int(parts[4]),
                                  takes=int(parts[5]), t=host_t or time.time())
                if parts[2] != prev:
                    phase_change = (prev, parts[2])
                push = dict(type='board_session', t=round(buf.t_now, 2), **{k: v for k, v in board_sess.items() if k != 't'})
            elif typ == 'K':                                    # K,<ms>,<down>,<count>  button edge
                ms = float(parts[1]); down = parts[2] == '1'
                btn['down'] = down; btn['t'] = host_t or time.time()
                if down:
                    btn['presses'] = int(parts[3]) if len(parts) > 3 else btn['presses'] + 1
                    prev = btn['last_ms']
                    btn['last_ms'] = ms
                    # the board reports edges only; the double-click window is judged here, on the
                    # board's own ms clock, so BLE jitter cannot turn one press into two
                    if prev is not None and 0 < ms - prev <= BTN_DOUBLE_MS:
                        btn['last_ms'] = None                       # consume, so a triple is not two doubles
                        dbl = True
                push = dict(type='button', t=round(buf.t_now, 2), down=down, presses=btn['presses'])
            elif typ == 'V':                                    # V,<ms>,<mV>,<pct>,<chg>,<fast>,<raw>,<sag>
                f = [int(x) for x in parts[2:8]] + [0] * 6
                battery.update(mv=f[0], pct=f[1], charging=bool(f[2]), fast=bool(f[3]), raw=f[4],
                               sag_mv=f[5], t=host_t or time.time())
                push = dict(type='battery', t=round(buf.t_now, 2), **{k: v for k, v in battery.items() if k != 't'})
            elif typ == 'M':                                    # M,<ms>,<rms>,<peak>  50 Hz level meter
                mic.update(rms=int(parts[2]), peak=int(parts[3]), t=host_t or time.time())
            elif typ in ('A', 'AEND'):                          # voice note audio coming back
                if voice.feed(typ, parts[1:]):
                    voice_done.set()
            elif typ == 'I' and 'rec done' in line:             # a take finished on the board
                take_ready = True
            elif typ == 'N':
                txt = ','.join(parts[1:]).strip()
                buf.notes.append((buf.t_now, txt))
                threading.Thread(target=event, args=(f'[file] {txt}',), daemon=True).start()
        except (ValueError, IndexError):
            return
        due = buf.t_now >= buf.next_feat_t
    if push:
        broadcast(push)
    if phase_change:
        threading.Thread(target=on_phase_change, args=phase_change, daemon=True).start()
    if take_ready:
        threading.Thread(target=collect_take, daemon=True).start()
    if dbl:
        threading.Thread(target=on_double_click, daemon=True).start()
    if due:
        step_features()


def ingest_rssi(samples: list, status: dict | None) -> None:
    """samples: [[host_time, rssi_dbm], ...] from rssi_capture.py (live) or the companion file (replay)."""
    with lock:
        for ht, v in samples:
            buf.rssi_last = float(v); buf.rssi_n += 1; buf.rssi_fresh = True
        if status:
            buf.rssi_stats = dict(status)
            buf.rssi_stats['link'] = 'streaming'
    if not samples and status:
        pass


def flush_raw() -> None:
    while True:
        time.sleep(0.1)
        with lock:
            p = buf.pending
            if not p['fsr'] and p['bpm'] is None and not p['rssi']:
                continue
            msg = dict(type='raw', t=round(buf.t_now, 2), fsr=p['fsr'], ppg=p['ppg'],
                       rssi=[None if not np.isfinite(x) else x for x in p['rssi']],
                       rssi_live=buf.rssi_fresh, bpm=p['bpm'], ibi=p['ibi'],
                       mic=(dict(rms=mic['rms'], peak=mic['peak'])                 # 10 Hz is plenty for a VU bar
                            if mic['t'] and time.time() - mic['t'] < 1.0 else None))
            buf.pending = dict(fsr=[], ppg=[], rssi=[], bpm=None, ibi=None)
            buf.rssi_fresh = False
        broadcast(msg)


def broadcast_voice() -> None:
    broadcast(dict(type='voice', t=round(buf.t_now, 2), **voice.snapshot()))


def voice_dir() -> pathlib.Path:
    """Filed sessions keep their note next to the capture; a bench test drops it in captures/."""
    return filing.SESSIONS if (sess.get('stamp') and not sess['active']) else filing.CAPTURES


def _voice_worker() -> None:
    snap = voice.snapshot()
    stamp = snap['stamp']
    if cfg['mode'] != 'live':
        voice.fail('replay mode: no board to record from'); broadcast_voice(); return
    if not (bridge_call('/cmd', dict(c='r')) or {}).get('sent'):
        voice.fail('could not send r -- bridge down or no board link'); broadcast_voice(); return
    event(f'voice note: recording {REC_SECONDS:.0f} s -- speak now')
    t_end = time.time() + REC_SECONDS + 0.9                 # board needs a beat to close the buffer
    while time.time() < t_end:
        time.sleep(0.2); broadcast_voice()
    voice.downloading(); broadcast_voice()
    if not (bridge_call('/cmd', dict(c='d')) or {}).get('sent'):
        voice.fail('could not send d -- bridge down'); broadcast_voice(); return
    event('voice note: downloading audio from the board')
    got = voice_done.wait(timeout=90.0)
    if not got:
        voice.fail('timed out waiting for AEND'); broadcast_voice(); return
    path = str(voice_dir() / f'voice-{stamp}.wav')
    info = voice.save_wav(path)
    if not info.get('ok'):
        broadcast_voice(); return
    event(f'voice note: {info["seconds"]:.1f} s saved, peak {info["peak"]}/32767 -> {os.path.basename(path)}')
    if info['truncated']:
        event(f'voice note: short -- {info["bytes"]} of {info["expected"]} bytes arrived')
    broadcast_voice()
    res = stt.transcribe(path, second_opinion=SECOND_OPINION)
    paths = journal.save(stamp, path, res, out_dir=voice_dir(), session_name=sess.get('name', ''))
    voice.finish(res, paths['txt'])
    if res.get('no_speech'):
        event(f'voice note: {res.get("note")} -- nothing sent to the recogniser')
    elif res.get('ok'):
        event(f'voice note ({res.get("backend")}): {res.get("text") or "(nothing heard)"}')
    else:
        event(f'voice note: not transcribed -- {res.get("error")}')
    event(f'voice note written to {os.path.basename(paths["txt"])} and the dream journal')
    journal.update_session_json(stamp, path, res, paths)
    broadcast_voice()


def start_voice_note(reason: str) -> dict:
    st = voice.snapshot()['state']
    if st in ('recording', 'downloading', 'transcribing'):
        return dict(ok=False, error=f'voice note already {st}')
    stamp = sess.get('stamp') or time.strftime('%Y%m%d-%H%M%S')
    voice_done.clear()
    voice.begin(reason, stamp, REC_SECONDS)
    broadcast_voice()
    threading.Thread(target=_voice_worker, daemon=True).start()
    return dict(ok=True, stamp=stamp)


def on_phase_change(prev: str, now: str) -> None:
    """Follow the board through its own session: it is the clock, the host is the recorder."""
    event(f'board session: {prev} -> {now}')
    if now == 'calib' and not sess['active']:
        with lock:
            buf.reset()
        r = session_start('', '')                    # start recording alongside the board
        if not r.get('ok'):
            event(f'could not start host session: {r.get("error")}')
    elif now == 'report':
        event('session over: wake cue fired, board is waiting for spoken takes')
    elif now in ('done', 'idle') and sess['active']:
        session_end('')


def collect_take() -> None:
    """The board finished an 8 s take and is holding it in RAM. Ask for it."""
    st = voice.snapshot()['state']
    if st in ('downloading', 'transcribing'):
        return
    stamp = sess.get('stamp') or time.strftime('%Y%m%d-%H%M%S')
    voice_done.clear()
    voice.begin('button', stamp, 0.0)
    voice.downloading()
    broadcast_voice()
    if not (bridge_call('/cmd', dict(c='d')) or {}).get('sent'):
        voice.fail('could not send d -- bridge down'); broadcast_voice(); return
    event('take finished on the board; downloading')
    if not voice_done.wait(timeout=90.0):
        voice.fail('timed out waiting for AEND'); broadcast_voice(); return
    n = board_sess.get('takes') or 1
    path = str(voice_dir() / f'voice-{stamp}-{n}.wav')
    info = voice.save_wav(path)
    if not info.get('ok'):
        broadcast_voice(); return
    event(f'take {n}: {info["seconds"]:.1f} s, peak {info["peak"]}/32767')
    broadcast_voice()
    res = stt.transcribe(path, second_opinion=SECOND_OPINION)
    paths = journal.save(f'{stamp}-{n}', path, res, out_dir=voice_dir(), session_name=sess.get('name', ''))
    voice.finish(res, paths['txt'])
    event(f'take {n} ({res.get("backend")}): {res.get("text") or res.get("note") or "(nothing heard)"}')
    journal.update_session_json(stamp, path, res, paths)
    broadcast_voice()


def on_double_click() -> None:
    """The glove's own end-of-session gesture: stop recording, then take the spoken report."""
    event('button: double-click')
    if sess['active']:
        r = session_end('')
        if not r.get('ok'):
            event(f'button: session end failed -- {r.get("error")}')
    else:
        event('button: no session running, taking a standalone voice note')
    start_voice_note('button')


def backfill_payload() -> dict:
    """Everything a freshly connected GUI needs to draw the session so far.

    The server has held the whole session in `buf` all along; until now it only ever sent what
    arrived after the page opened, so the traces started empty and filled forward. The client's
    queue is registered before this is sent, so nothing broadcast meanwhile is lost or doubled.
    """
    def clean(xs):
        return [None if not np.isfinite(x) else round(float(x), 2) for x in xs]

    with lock:
        n_raw = int(FS * RAW_WIN_S)
        t_cut = buf.t_now - HIST_WIN_S
        return dict(fsr=clean(buf.fsr[-n_raw:]), ppg=clean(buf.ppg[-n_raw:]), rssi=clean(buf.rssi[-n_raw:]),
                    hist=[h for h in buf.hist if h[0] >= t_cut],
                    marks=[[round(t, 2), txt] for t, txt in buf.notes if t >= t_cut],
                    bpm=buf.beat_bpm[-1] if buf.beat_bpm else None,
                    ibi=buf.beat_ibi[-1] if buf.beat_ibi else None,
                    voice=voice.snapshot(), battery=dict(battery), mic=dict(mic),
                    board_session={k: v for k, v in board_sess.items() if k != 't'})


def replay(path: str, speed: float) -> None:
    global REPLAY_SRC
    REPLAY_SRC = path
    cfg['mode'] = 'replay'; cfg['source'] = os.path.basename(path)
    event(f'replay {cfg["source"]} x{speed}')
    from onset.parse import find_rssi_companion
    comp = None
    try:
        import pathlib as _pl
        with open(path) as fh:
            next(fh); first_host = float(next(fh).split(',')[0])
        comp = find_rssi_companion(_pl.Path(path), first_host, first_host + 24 * 3600)
    except (StopIteration, ValueError):
        comp = None
    rssi_rows: list[tuple[float, float]] = []
    if comp is not None:
        with open(comp, newline='') as fh:
            for r in csv.reader(fh):
                if len(r) >= 4 and r[1] == 'S':
                    try: rssi_rows.append((float(r[0]), float(r[3])))
                    except ValueError: pass
        event(f'replay companion {comp.name}: {len(rssi_rows)} RSSI samples')
        ingest_rssi([], dict(fw='replay', interval_ms=15.0, fw_hz=66, dropped=0, obs_hz=66.0, gaps=0))
    ri = 0
    with open(path, newline='') as fh:
        rd = csv.reader(fh)
        last_seq = None; t_wall = time.time()
        for row in rd:
            if len(row) < 2 or row[0] == 'host_time':
                continue
            try: ht = float(row[0])
            except ValueError: ht = None
            if ht is not None and rssi_rows:
                j = ri
                while j < len(rssi_rows) and rssi_rows[j][0] <= ht: j += 1
                if j > ri:
                    ingest_rssi([[a, b] for a, b in rssi_rows[ri:j]], None); ri = j
            on_line(','.join(row[1:]), ht)
            if row[1] in ('F', 'S'):
                seq = int(row[2]) // 10 if row[1] == 'S' else int(row[2])
                if last_seq is not None:
                    t_wall += (seq - last_seq) / FS / speed
                    d = t_wall - time.time()
                    if d > 0:
                        time.sleep(d)
                last_seq = seq
    event('replay finished')
    cfg['mode'] = 'idle'


def bridge_client() -> None:
    cfg['mode'] = 'live'
    while True:
        try:
            with urllib.request.urlopen(BRIDGE + '/events', timeout=30) as r:
                cfg['source'] = 'bridge'; event('connected to bridge')
                for raw in r:
                    line = raw.decode(errors='ignore').strip()
                    if not line.startswith('data: '):
                        continue
                    for _t, ln in json.loads(line[6:]):
                        if ln.startswith('#'):
                            if ln.startswith('#status'):
                                cfg['source'] = ln
                                broadcast(dict(type='event', t=round(buf.t_now, 2), text=ln))
                            continue
                        on_line(ln)
        except Exception as e:
            cfg['source'] = 'bridge down'
            broadcast(dict(type='event', t=round(buf.t_now, 2), text=f'bridge not reachable ({e.__class__.__name__}); retrying'))
            time.sleep(2)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, body: bytes, ctype='application/json', code=200):
        self.send_response(code); self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def _json(self, obj, code=200): self._send(json.dumps(obj).encode(), code=code)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        try: return json.loads(self.rfile.read(n) or b'{}')
        except ValueError: return {}

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            with open(os.path.join(HERE, 'gui.html'), 'rb') as fh:
                self._send(fh.read(), 'text/html; charset=utf-8')
        elif self.path == '/sessions':
            self._json(dict(current={k: v for k, v in sess.items() if k != 'last'}, last=sess['last'], ledger=filing.ledger_rows()))
        elif self.path == '/review':
            pth = filing.HERE / 'data' / 'exports' / 'session-review.html'
            if pth.exists():
                self._send(pth.read_bytes(), 'text/html; charset=utf-8')
            else:
                self._send(b'<p>No review page yet. End a session, or run session_view.py.</p>', 'text/html; charset=utf-8')
        elif self.path == '/state':
            self._json(dict(cfg=cfg, presets=haptic.PRESETS, limits=haptic.LIMITS, features=FEATURES,
                            calib_s=train.CALIB_S, t=round(buf.t_now, 1), guard=haptic.guard(cfg['pattern'])[1:],
                            serial=haptic.serial_line(cfg['pattern']), rssi_stats=buf.rssi_stats,
                            session={k: v for k, v in sess.items() if k != 'last'},
                            voice=voice.snapshot(), battery=battery, mic=mic, rec_seconds=REC_SECONDS,
                            board_session={k: v for k, v in board_sess.items() if k != 't'},
                            button={k: v for k, v in btn.items() if k != 'last_ms'},
                            stt=dict(zip(('backend', 'hint'), stt.available()))))
        elif self.path == '/voice.wav':
            pth = voice.snapshot().get('wav') or ''
            if pth and os.path.exists(pth):
                with open(pth, 'rb') as fh:
                    self._send(fh.read(), 'audio/wav')
            else:
                self.send_error(404)
        elif self.path == '/pattern.wav':
            b = io.BytesIO()
            import wave
            y = (haptic.render(cfg['pattern']) * 32767).astype('<i2')
            with wave.open(b, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(y.tobytes())
            self._send(b.getvalue(), 'audio/wav')
        elif self.path == '/events':
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache'); self.end_headers()
            q: queue.Queue = queue.Queue(maxsize=2000); clients.add(q)
            try:
                hello = dict(type='hello', cfg=cfg, t=round(buf.t_now, 1), backfill=backfill_payload())
                self.wfile.write(('data: ' + json.dumps(hello) + '\n\n').encode()); self.wfile.flush()
                while True:
                    try: msg = q.get(timeout=15)
                    except queue.Empty: msg = b': ping\n\n'
                    self.wfile.write(msg); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                clients.discard(q)
        else:
            self.send_error(404)

    def do_POST(self):
        d = self._body()
        if self.path == '/set':
            for k in ('thresh', 'hold', 'smooth', 'probe_s', 'delay_min_s', 'delay_max_s'):
                if k in d: cfg[k] = float(d[k]) if k in ('thresh',) else int(d[k])
            if 'armed' in d: cfg['armed'] = bool(d['armed'])
            if 'haptic_mode' in d and d['haptic_mode'] in ('none', 'tap', 'pattern'): cfg['haptic_mode'] = d['haptic_mode']
            if 'preset' in d and d['preset'] in haptic.PRESETS:
                cfg['preset'] = d['preset']; cfg['pattern'] = dict(haptic.PRESETS[d['preset']])
            if 'pattern' in d:
                cfg['pattern'] = haptic.normalise({**cfg['pattern'], **d['pattern']})
                if 'preset' not in d: cfg['preset'] = 'custom'
            ok, why, info = haptic.guard(cfg['pattern'])
            broadcast(dict(type='cfg', cfg=cfg, guard=[why, info], serial=haptic.serial_line(cfg['pattern'])))
            self._json(dict(cfg=cfg, guard=[why, info], serial=haptic.serial_line(cfg['pattern'])))
        elif self.path == '/session':
            act = d.get('action')
            if act == 'start':
                self._json(session_start(str(d.get('name', '')), str(d.get('notes', ''))))
            elif act == 'end':
                self._json(session_end(str(d.get('notes', ''))))
            else:
                self._json(dict(ok=False, error='action must be start|end'), 400)
        elif self.path == '/rssi':
            ingest_rssi(d.get('samples') or [], d.get('status')); self._json(dict(ok=True))
        elif self.path == '/fire':
            fire('manual'); self._json(dict(ok=True))
        elif self.path == '/note':
            note(str(d.get('text', ''))[:80]); self._json(dict(ok=True))
        elif self.path == '/record':
            ok = bridge_post('/record', dict(on=bool(d.get('on')))) if cfg['mode'] == 'live' else False
            event('recording ' + ('on' if d.get('on') else 'off') + ('' if ok else ' (bridge not reachable)'))
            self._json(dict(ok=ok))
        elif self.path == '/voice':
            act = d.get('action', 'start')
            if act == 'start':
                self._json(start_voice_note(str(d.get('reason', 'manual'))))
            elif act == 'cancel':
                voice_done.set(); voice.fail('cancelled'); broadcast_voice(); self._json(dict(ok=True))
            elif act == 'clear':
                voice.reset(); broadcast_voice(); self._json(dict(ok=True))
            elif act == 'retranscribe':
                snap = voice.snapshot()
                if not snap.get('wav'):
                    self._json(dict(ok=False, error='no audio to transcribe'), 400)
                else:
                    def _again(sn=snap):
                        res = stt.transcribe(sn['wav'])
                        voice.finish(res)
                        event(f'voice note re-transcribed: {res.get("text") or res.get("error")}')
                        broadcast_voice()
                    threading.Thread(target=_again, daemon=True).start()
                    self._json(dict(ok=True))
            else:
                self._json(dict(ok=False, error='action must be start|cancel|clear|retranscribe'), 400)
        elif self.path == '/reset':
            with lock: buf.reset()
            event('reset: calibration restarted'); self._json(dict(ok=True))
        else:
            self.send_error(404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--replay'); ap.add_argument('--speed', type=float, default=8.0)
    ap.add_argument('--port', type=int, default=PORT)
    a = ap.parse_args()
    fit_model()
    threading.Thread(target=flush_raw, daemon=True).start()
    if a.replay:
        threading.Thread(target=replay, args=(a.replay, a.speed), daemon=True).start()
    else:
        threading.Thread(target=bridge_client, daemon=True).start()
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), H); srv.daemon_threads = True
    print(f'onset GUI: http://localhost:{a.port}   ({"replay " + a.replay if a.replay else "live via bridge"})')
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == '__main__':
    main()
