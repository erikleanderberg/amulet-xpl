#!/usr/bin/env python3
"""Transcribe session voice notes, or re-transcribe old ones with a different engine.

The WAV is the record; the transcript is derived, so it can always be rebuilt:

    .venv/bin/python transcribe_notes.py --all                 every note that has no .txt yet
    .venv/bin/python transcribe_notes.py --all --force         redo them all
    .venv/bin/python transcribe_notes.py path/to/voice.wav     just this one
    .venv/bin/python transcribe_notes.py --compare note.wav    every installed engine, side by side
    .venv/bin/python transcribe_notes.py --backends            what is installed and what is not
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time
from pathlib import Path

from onset import journal
from onset import transcribe as stt

STAMP = re.compile(r'voice-(\d{8}-\d{6})')


def stamp_of(path: str) -> str:
    m = STAMP.search(os.path.basename(path))
    return m.group(1) if m else time.strftime('%Y%m%d-%H%M%S')


def show_backends() -> None:
    cur, label = stt.available()
    print(f'{"backend":<16} {"installed":<10} what it is')
    print('-' * 74)
    for b in stt.ORDER:
        ok = stt.installed(b)
        mark = 'yes' if ok else 'no'
        star = ' <- default' if b == cur else ''
        print(f'{b:<16} {mark:<10} {stt.LABELS.get(b, b)}{star}')
    print()
    if cur:
        print(f'active: {label}')
    else:
        print(label)
    print('\npin one with  ONSET_STT=faster_whisper  (comma-separated list sets the fallback order)')


def one(path: str, backend: str | None, second: bool, quiet: bool) -> dict:
    t0 = time.time()
    res = stt.transcribe(path, backend=backend, second_opinion=second)
    dt = time.time() - t0
    stamp = stamp_of(path)
    paths = journal.save(stamp, path, res, out_dir=Path(path).parent)
    journal.update_session_json(stamp, path, res, paths)
    a = res.get('audio') or {}
    if not quiet:
        head = f'{os.path.basename(path)}  {a.get("seconds", 0):.1f}s  SNR {a.get("snr_db", 0):.0f}dB  {dt:.2f}s'
        print(f'\n{head}\n{"-" * len(head)}')
        print(journal.body(res))
        for k, v in (res.get('alternates') or {}).items():
            print(f'  [{k}] {v}')
        print(f'  -> {os.path.basename(paths["txt"])}')
    return res


def compare(path: str) -> None:
    m = stt.measure(path)
    print(f'{os.path.basename(path)}: {m.get("seconds")} s, peak {m.get("peak")}, '
          f'SNR {m.get("snr_db")} dB, speech {m.get("speech_ratio", 0) * 100:.0f}%')
    if not m.get('has_speech'):
        print('VAD: no speech in this clip — nothing would be sent to a recogniser.'); return
    print()
    for b in stt.ORDER:
        if not stt.installed(b):
            print(f'{b:<16} (not installed)'); continue
        t0 = time.time()
        r = stt.transcribe(path, backend=b)
        dt = time.time() - t0
        print(f'{b:<16} {dt:6.2f}s  {r.get("text") or "[" + (r.get("error") or "nothing") + "]"}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--all', action='store_true', help='every voice-*.wav under data/sessions')
    ap.add_argument('--force', action='store_true', help='redo notes that already have a .txt')
    ap.add_argument('--backend', help='pin one engine (see --backends)')
    ap.add_argument('--second-opinion', action='store_true', help='also run the next engine and keep both')
    ap.add_argument('--compare', action='store_true', help='run every installed engine on one file')
    ap.add_argument('--backends', action='store_true')
    ap.add_argument('-q', '--quiet', action='store_true')
    a = ap.parse_args()

    if a.backends:
        show_backends(); return

    paths = list(a.paths)
    if a.all:
        paths += sorted(glob.glob(str(journal.SESSIONS / 'voice-*.wav')))
    if not paths:
        ap.error('give a wav path, or --all')

    if a.compare:
        for p in paths:
            compare(p)
        return

    if not stt.available()[0] and not a.backend:
        print(stt.available()[1], file=sys.stderr); sys.exit(1)

    done = skipped = 0
    for p in paths:
        if not os.path.exists(p):
            print(f'missing: {p}', file=sys.stderr); continue
        if a.all and not a.force and os.path.exists(p.replace('.wav', '.txt')):
            skipped += 1; continue
        one(p, a.backend, a.second_opinion, a.quiet)
        done += 1
    print(f'\n{done} transcribed'
          + (f', {skipped} already had a .txt (use --force)' if skipped else '')
          + (f'\njournal: {journal.JOURNAL}' if done else ''))


if __name__ == '__main__':
    main()
