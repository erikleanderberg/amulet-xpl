#!/usr/bin/env python3
"""
Whole-session review page: every channel, the detector score, RSSI, and all labels on one time
axis, for one or more sessions. Self-contained HTML (no libraries), written to
data/exports/session-review.html. Open it in a browser; pick a session from the dropdown.

    .venv/bin/python session_view.py --data data/synthetic          # all sessions in a dir
    .venv/bin/python session_view.py --data data/sessions --out data/exports/review.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import train
from onset.features import FEATURES, build_features
from onset.parse import FS, Session, load_dir


def session_payload(s: Session, model) -> dict:
    times, X = build_features(s)
    fi = {n: FEATURES.index(n) for n in FEATURES}
    score = train.drowsiness_score(model, times, X)
    trig = train.predict(model, times, X, s)
    def col(name):
        v = X[:, fi[name]]
        return [None if not np.isfinite(x) else round(float(x), 2) for x in v]
    rssi_raw = []
    if s.rssi is not None:
        step = 10                                   # 10 Hz for the raw trace
        for i in range(0, len(s.rssi), step):
            v = s.rssi[i]
            rssi_raw.append(None if not np.isfinite(v) else int(v))
    return dict(
        name=s.name, duration=round(s.duration, 1), calib=train.CALIB_S,
        t=[round(float(x), 1) for x in times],
        flex=col('flex_kohm10'), fsr=col('fsr_mean10'), hr=col('hr_mean15'), amp=col('ppg_amp10'),
        rssi10=col('rssi_mean10'), rssi_raw=rssi_raw, rssi_raw_hz=10,
        score=[None if not np.isfinite(x) else round(float(x), 3) for x in score],
        thresh=float(model['thresh']),
        trig=[round(float(times[i]), 1) for i in np.flatnonzero(trig)],
        notes=[[round(t, 1), txt] for t, txt in s.notes],
        model=model.get('kind', train.MODEL),
    )


PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Onset Session Review</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--ground:#EEF1F4;--surface:#FFFFFF;--ink:#15202B;--muted:#5A6774;--line:#D4DBE2;--grid:#E4E9EE;--accent:#8F6310;--chip:#E9EDF1;
 --c-flex:#eda100;--c-hr:#e87ba4;--c-amp:#eb6834;--c-rssi:#2a78d6;--c-score:#1baf7a;--crit:#B4380B;--crit-bg:#FBE9E1;--calib:#E9EDF1;
 --display:"Barlow Condensed","Arial Narrow",Arial,sans-serif;--body:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#0F151B;--surface:#172029;--ink:#E5EBF0;--muted:#95A3B0;--line:#2B3743;--grid:#212C37;--accent:#E3B45C;--chip:#22303C;
 --c-flex:#c98500;--c-hr:#d55181;--c-amp:#d95926;--c-rssi:#3987e5;--c-score:#199e70;--crit:#FB8A4C;--crit-bg:#2A1A12;--calib:#1E2833}}
:root[data-theme="dark"]{--ground:#0F151B;--surface:#172029;--ink:#E5EBF0;--muted:#95A3B0;--line:#2B3743;--grid:#212C37;--accent:#E3B45C;--chip:#22303C;
 --c-flex:#c98500;--c-hr:#d55181;--c-amp:#d95926;--c-rssi:#3987e5;--c-score:#199e70;--crit:#FB8A4C;--crit-bg:#2A1A12;--calib:#1E2833}
*{box-sizing:border-box}body{margin:0;background:var(--ground);color:var(--ink);font:14px/1.5 var(--body);padding-inline:20px;padding-block:20px 48px}
.wrap{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:500}
h1{font-family:var(--display);font-size:38px;font-weight:700;margin:0;line-height:1}
.bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
select,button{font:13px var(--body);color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
button[aria-pressed="true"]{background:var(--chip)}
.meta{font-family:var(--mono);font-size:12px;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-family:var(--mono);font-size:11px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}.legend i{width:14px;height:0;border-top:2px solid var(--ink)}
.legend i.d{border-top-style:dashed}.legend b{width:10px;height:10px;background:var(--calib);border:1px solid var(--line);display:inline-block}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:4px;position:relative}
.ch{display:grid;grid-template-columns:150px 1fr;gap:10px;align-items:stretch;border-top:1px solid var(--grid)}
.ch:first-child{border-top:none}
.ch .k{padding:10px 0 0;display:flex;flex-direction:column;gap:2px}
.ch .k .n{font-family:var(--display);font-size:18px;font-weight:600;display:flex;align-items:center;gap:8px}
.ch .k .n i{width:12px;height:3px;border-radius:2px;background:var(--c)}
.ch .k .u{font-family:var(--mono);font-size:11px;color:var(--muted)}
.ch .k .v{font-family:var(--mono);font-size:13px;font-variant-numeric:tabular-nums}
canvas{display:block;width:100%;height:110px}
canvas.axis{height:26px}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-family:var(--mono);font-size:11px;box-shadow:0 2px 8px rgba(0,0,0,.12);display:none;z-index:2;white-space:nowrap}
table{border-collapse:collapse;width:100%;font-family:var(--mono);font-size:12px;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:4px 8px;border-bottom:1px solid var(--grid)}th{color:var(--muted);font-weight:500}td:first-child,th:first-child{text-align:left}
.tbl{overflow:auto;max-height:420px}
.panel[hidden]{display:none}
@media (max-width:640px){.ch{grid-template-columns:1fr}.ch .k{flex-direction:row;gap:10px;align-items:baseline}}
</style></head><body><div class="wrap">
<div><div class="eyebrow">Amulet XPL · onset-ml · whole-session review</div><h1>Onset Session Review</h1></div>
<div class="bar"><label>Session <select id="sel"></select></label><button id="tbtn" aria-pressed="false">Table</button><span class="meta" id="meta"></span></div>
<div class="legend"><span><b></b> calibration (first 120 s)</span><span><i style="border-color:var(--crit)"></i> onset (label)</span><span><i style="border-color:var(--c-rssi)"></i> cue fired</span><span><b style="background:var(--c-rssi);opacity:.25;border:none"></b> detector over threshold</span><span>R = report · L = label · × = probe miss</span></div>
<div class="panel" id="charts"><div class="tip" id="tip"></div></div>
<div class="panel tbl" id="table" hidden></div>
</div>
<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const CH = [
  {k:'flex', n:'Flex', u:'kΩ (Dormio unit)', c:'--c-flex'},
  {k:'hr', n:'Heart rate', u:'bpm, 15 s', c:'--c-hr'},
  {k:'amp', n:'Pulse amplitude', u:'PPG P95–P5, 10 s', c:'--c-amp'},
  {k:'rssi', n:'RSSI', u:'dBm · raw 10 Hz + 10 s mean', c:'--c-rssi'},
  {k:'score', n:'Detector', u:'score · fires at threshold', c:'--c-score'},
];
let S = null, hoverT = null;
const sel = $('#sel');
DATA.forEach((d, i) => { const o = document.createElement('option'); o.value = i; o.textContent = d.name; sel.appendChild(o); });
sel.addEventListener('change', () => show(+sel.value));
$('#tbtn').addEventListener('click', () => { const on = $('#tbtn').getAttribute('aria-pressed') !== 'true'; $('#tbtn').setAttribute('aria-pressed', String(on)); $('#table').hidden = !on; if (on) buildTable(); });
function mmss(t){ const m = Math.floor(t/60), s = Math.floor(t%60); return `${m}:${String(s).padStart(2,'0')}`; }
function build() {
  const root = $('#charts'); root.querySelectorAll('.ch').forEach(e => e.remove());
  for (const c of CH) {
    const row = document.createElement('div'); row.className = 'ch'; row.style.setProperty('--c', css(c.c));
    row.innerHTML = `<div class="k"><div class="n"><i></i>${c.n}</div><div class="u">${c.u}</div><div class="v" id="v_${c.k}">–</div></div><canvas id="c_${c.k}"></canvas>`;
    root.appendChild(row);
  }
  const ax = document.createElement('div'); ax.className = 'ch'; ax.innerHTML = `<div class="k"><div class="u">time · mm:ss</div></div><canvas id="c_axis" class="axis"></canvas>`; root.appendChild(ax);
  root.addEventListener('mousemove', onHover); root.addEventListener('mouseleave', () => { hoverT = null; $('#tip').style.display = 'none'; draw(); });
}
function series(k) {
  if (k === 'rssi') return { t: S.t, y: S.rssi10, raw: S.rssi_raw, rawHz: S.rssi_raw_hz };
  return { t: S.t, y: S[k] };
}
function range(arr) { const v = arr.filter(x => x != null); if (!v.length) return [0, 1]; let lo = Math.min(...v), hi = Math.max(...v); if (hi - lo < 1e-6) { lo -= 1; hi += 1; } const p = (hi - lo) * 0.08; return [lo - p, hi + p]; }
function fit(c) { const r = c.getBoundingClientRect(), dpr = devicePixelRatio || 1; c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); const ctx = c.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); return [ctx, r.width, r.height]; }
const PADL = 44, PADR = 8;
function draw() {
  if (!S) return;
  const T = S.duration;
  for (const c of CH) {
    const cv = $('#c_' + c.k), [ctx, W, H] = fit(cv); const x = t => PADL + (W - PADL - PADR) * t / T;
    const sr = series(c.k); const rawVals = sr.raw ? sr.raw : null;
    const [lo, hi] = range(rawVals ? rawVals.concat(sr.y) : sr.y); const y = v => H - 6 - (H - 12) * (v - lo) / (hi - lo);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = css('--calib'); ctx.fillRect(x(0), 0, x(S.calib) - x(0), H);
    ctx.strokeStyle = css('--grid'); ctx.lineWidth = 1; for (let m = 60; m < T; m += 60) { ctx.beginPath(); ctx.moveTo(x(m), 0); ctx.lineTo(x(m), H); ctx.stroke(); }
    ctx.fillStyle = css('--muted'); ctx.font = '10px ' + css('--mono'); ctx.textAlign = 'right';
    ctx.fillText(hi.toFixed(c.k === 'score' ? 1 : 0), PADL - 6, 10); ctx.fillText(lo.toFixed(c.k === 'score' ? 1 : 0), PADL - 6, H - 3);
    if (c.k === 'score') { ctx.strokeStyle = css('--muted'); ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(x(0), y(S.thresh)); ctx.lineTo(W - PADR, y(S.thresh)); ctx.stroke(); ctx.setLineDash([]); }
    if (rawVals) { ctx.strokeStyle = css(c.c); ctx.globalAlpha = 0.35; ctx.lineWidth = 1; ctx.beginPath(); let pen = false; rawVals.forEach((v, i) => { if (v == null) { pen = false; return; } const px = x(i / sr.rawHz), py = y(v); pen ? ctx.lineTo(px, py) : ctx.moveTo(px, py); pen = true; }); ctx.stroke(); ctx.globalAlpha = 1; }
    ctx.strokeStyle = css(c.c); ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.beginPath(); let pen = false;
    sr.y.forEach((v, i) => { if (v == null) { pen = false; return; } const px = x(sr.t[i]), py = y(v); pen ? ctx.lineTo(px, py) : ctx.moveTo(px, py); pen = true; }); ctx.stroke();
    // markers
    // over-threshold runs as a translucent band (contiguous seconds merged)
    if (S.trig.length) { ctx.fillStyle = css('--c-rssi'); ctx.globalAlpha = 0.10; let a = S.trig[0], b = S.trig[0];
      for (let i = 1; i <= S.trig.length; i++) { const tt = S.trig[i]; if (tt != null && tt - b <= 1.5) { b = tt; continue; } ctx.fillRect(x(a), 0, Math.max(2, x(b + 1) - x(a)), H); if (tt != null) { a = tt; b = tt; } }
      ctx.globalAlpha = 1; }
    for (const [tt, txt] of S.notes) {
      let col = null, lab = '';
      if (txt === 'onset') { col = css('--crit'); lab = 'onset'; }
      else if (txt.startsWith('haptic:')) { col = css('--c-rssi'); lab = 'cue'; }
      else if (txt.startsWith('report:')) { col = css('--ink'); lab = 'R:' + txt.slice(7, 8); }
      else if (txt.startsWith('label:')) { col = css('--muted'); lab = 'L:' + txt.slice(6, 7); }
      else if (txt === 'probe:miss') { col = css('--crit'); lab = '×'; }
      else if (txt === 'detected') { col = css('--c-rssi'); lab = 'det'; }
      if (!col) continue;
      ctx.strokeStyle = col; ctx.lineWidth = txt === 'onset' ? 2 : 1; ctx.beginPath(); ctx.moveTo(x(tt), 0); ctx.lineTo(x(tt), H); ctx.stroke();
      if (c.k === 'flex') { ctx.fillStyle = col; ctx.textAlign = 'left'; ctx.font = '10px ' + css('--mono'); ctx.fillText(lab, x(tt) + 3, 11); }
    }
    if (hoverT != null) { ctx.strokeStyle = css('--ink'); ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(x(hoverT), 0); ctx.lineTo(x(hoverT), H); ctx.stroke(); ctx.globalAlpha = 1;
      const i = Math.min(sr.y.length - 1, Math.max(0, Math.round((hoverT - sr.t[0])))); const v = sr.y[i]; if (v != null) { ctx.fillStyle = css(c.c); ctx.beginPath(); ctx.arc(x(sr.t[i]), y(v), 4, 0, 7); ctx.fill(); ctx.strokeStyle = css('--surface'); ctx.lineWidth = 2; ctx.stroke(); }
      $('#v_' + c.k).textContent = v == null ? '–' : (c.k === 'score' ? v.toFixed(2) : v.toFixed(1)); }
    else { const last = sr.y.filter(v => v != null); $('#v_' + c.k).textContent = last.length ? `end ${last[last.length - 1].toFixed(1)}` : '–'; }
  }
  const [ctx, W, H] = fit($('#c_axis')); const x = t => PADL + (W - PADL - PADR) * t / T; ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = css('--muted'); ctx.font = '10px ' + css('--mono'); ctx.textAlign = 'center';
  const step = T > 900 ? 120 : 60; for (let m = 0; m <= T; m += step) ctx.fillText(mmss(m), x(m), 14);
}
function onHover(e) {
  const c = $('#c_flex'); const r = c.getBoundingClientRect(); const W = r.width; if (e.clientX < r.left || e.clientX > r.right) return;
  hoverT = Math.max(0, Math.min(S.duration, (e.clientX - r.left - PADL) / (W - PADL - PADR) * S.duration));
  draw();
  const tip = $('#tip'); const i = Math.round(hoverT - S.t[0]); const g = (k, d = 1) => (S[k] && S[k][i] != null) ? S[k][i].toFixed(d) : '–';
  const near = S.notes.filter(([tt]) => Math.abs(tt - hoverT) < 8).map(([tt, txt]) => `${mmss(tt)} ${txt}`).join('<br>');
  tip.innerHTML = `<b>${mmss(hoverT)}</b><br>flex ${g('flex')} kΩ · fsr ${g('fsr', 0)}<br>hr ${g('hr')} bpm · amp ${g('amp', 0)}<br>rssi ${g('rssi10')} dBm · score ${g('score', 2)}${near ? '<br>' + near : ''}`;
  const pr = $('#charts').getBoundingClientRect(); tip.style.display = 'block'; tip.style.left = Math.min(e.clientX - pr.left + 14, pr.width - 230) + 'px'; tip.style.top = (e.clientY - pr.top + 10) + 'px';
}
function buildTable() {
  const rows = []; for (let i = 0; i < S.t.length; i += 10) rows.push(`<tr><td>${mmss(S.t[i])}</td><td>${f(S.flex[i])}</td><td>${f(S.fsr[i], 0)}</td><td>${f(S.hr[i])}</td><td>${f(S.amp[i], 0)}</td><td>${f(S.rssi10[i])}</td><td>${f(S.score[i], 2)}</td></tr>`);
  function f(v, d = 1) { return v == null ? '–' : v.toFixed(d); }
  $('#table').innerHTML = `<table><thead><tr><th>time</th><th>flex kΩ</th><th>fsr adc</th><th>hr bpm</th><th>ppg amp</th><th>rssi dBm</th><th>score</th></tr></thead><tbody>${rows.join('')}</tbody></table>`;
}
function show(i) {
  S = DATA[i]; const onset = S.notes.find(n => n[1] === 'onset'); const fires = S.notes.filter(n => n[1].startsWith('haptic:')).length;
  $('#meta').innerHTML = `<span>${(S.duration/60).toFixed(1)} min</span><span>detector: ${S.model}</span><span>onset label: ${onset ? mmss(onset[0]) : 'none'}</span><span>over threshold: ${S.trig.length} s</span><span>cues: ${fires}</span><span>rssi: ${S.rssi_raw.length ? 'yes' : 'no capture'}</span>`;
  draw(); if ($('#tbtn').getAttribute('aria-pressed') === 'true') buildTable();
}
build(); show(0); addEventListener('resize', draw);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script></body></html>
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', nargs='*', default=['data/sessions', 'data/synthetic'])
    ap.add_argument('--out', default='data/exports/session-review.html')
    ap.add_argument('--max', type=int, default=20)
    a = ap.parse_args()
    sessions = [s for d in a.data if Path(d).is_dir() for s in load_dir(d)][: a.max]
    if not sessions:
        print('no sessions'); return
    model = train.fit([(s, *build_features(s)) for s in sessions]) if train.MODEL != 'dormio' else train.fit([])
    sessions.sort(key=lambda s: (s.onset is None, s.name))       # sessions with an onset label first
    payload = [session_payload(s, model) for s in sessions]
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace('__DATA__', json.dumps(payload)))
    print(f'{len(payload)} sessions -> {out}  ({out.stat().st_size/1e6:.1f} MB)')


if __name__ == '__main__':
    main()
