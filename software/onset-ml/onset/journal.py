"""Where a voice note ends up on disk.

Three artefacts, in order of how durable they are:

1. `voice-<stamp>.wav`   the recording. Ground truth; everything else is derived from it.
2. `voice-<stamp>.txt`   the transcript, with a short header so the file explains itself if you
                         find it a year from now with no context.
3. `dream-journal.md`    every note appended in order, newest last. The thing you actually read.

The transcript is also written into `session-<stamp>.json`, but that file is machine-shaped; this
module exists so there is a plain-text copy that survives any change to the JSON schema.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SESSIONS = HERE / 'data' / 'sessions'
JOURNAL = SESSIONS / 'dream-journal.md'

JOURNAL_HEAD = """# Dream journal — Amulet XPL

Spoken reports recorded by the glove at the end of a session and transcribed on this machine.
Newest entries at the bottom. The `.wav` beside each entry is the original recording; this file
is derived from it and can be regenerated with `transcribe_notes.py --all`.
"""


def _stamp_time(stamp: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(stamp, '%Y%m%d-%H%M%S')
    except ValueError:
        return datetime.datetime.now()


def _audio_line(a: dict) -> str:
    bits = []
    if a.get('seconds'):
        bits.append(f'{a["seconds"]:.1f} s')
    if a.get('rate'):
        bits.append(f'{a["rate"]} Hz')
    if a.get('peak') is not None:
        bits.append(f'peak {a["peak"]}/32767')
    if a.get('snr_db') is not None:
        bits.append(f'SNR {a["snr_db"]:.1f} dB')
    if a.get('speech_ratio') is not None:
        bits.append(f'speech {a["speech_ratio"] * 100:.0f}%')
    return ' · '.join(bits)


def body(result: dict) -> str:
    """The transcript, or an explicit statement of why there isn't one. Never a blank file."""
    if result.get('no_speech'):
        return f'[no speech detected — {result.get("note", "the clip is below the speech threshold")}]'
    text = (result.get('text') or '').strip()
    if text:
        return text
    if result.get('error'):
        return f'[not transcribed — {result["error"]}]'
    return '[nothing heard]'


def save(stamp: str, wav: str | Path, result: dict, out_dir: Path | None = None,
         session_name: str = '') -> dict:
    """Write the .txt and append to the journal. Returns the paths written."""
    d = Path(out_dir) if out_dir else SESSIONS
    d.mkdir(parents=True, exist_ok=True)
    when = _stamp_time(stamp)
    audio = result.get('audio') or {}
    text = body(result)
    engine = result.get('backend') or 'not transcribed'

    txt = d / f'voice-{stamp}.txt'
    header = [f'Amulet XPL — voice note',
              f'session   : {stamp}' + (f'  ({session_name})' if session_name else ''),
              f'recorded  : {when:%Y-%m-%d %H:%M:%S}',
              f'audio     : {_audio_line(audio) or "unknown"}',
              f'engine    : {engine}']
    if result.get('alternates'):
        for k, v in result['alternates'].items():
            header.append(f'alternate : [{k}] {v}')
    txt.write_text('\n'.join(header) + '\n\n' + text + '\n', encoding='utf-8')

    if not JOURNAL.exists():
        JOURNAL.write_text(JOURNAL_HEAD, encoding='utf-8')
    entry = [f'\n\n## {when:%Y-%m-%d %H:%M} — {stamp}' + (f' · {session_name}' if session_name else ''),
             '', text, '',
             f'<sub>{engine} · {_audio_line(audio)} · [wav]({Path(wav).name}) · [txt]({txt.name})</sub>']
    with open(JOURNAL, 'a', encoding='utf-8') as fh:
        fh.write('\n'.join(entry))
    return dict(txt=str(txt), journal=str(JOURNAL))


def update_session_json(stamp: str, wav: str | Path, result: dict, paths: dict) -> bool:
    p = SESSIONS / f'session-{stamp}.json'
    if not p.exists():
        return False
    try:
        meta = json.loads(p.read_text())
        a = result.get('audio') or {}
        meta['voice'] = dict(wav=Path(wav).name, txt=Path(paths['txt']).name,
                             text=(result.get('text') or ''), engine=result.get('backend'),
                             no_speech=bool(result.get('no_speech')), error=result.get('error', ''),
                             alternates=result.get('alternates') or {},
                             seconds=a.get('seconds'), rate=a.get('rate'), peak=a.get('peak'),
                             snr_db=a.get('snr_db'), speech_ratio=a.get('speech_ratio'))
        for f in (Path(wav).name, Path(paths['txt']).name):
            if f not in meta.get('files', []):
                meta.setdefault('files', []).append(f)
        p.write_text(json.dumps(meta, indent=1, default=str))
        return True
    except Exception:
        return False
