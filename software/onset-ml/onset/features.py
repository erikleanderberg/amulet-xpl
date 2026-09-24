"""
Causal, windowed features from a Session (or from a live buffer).

Everything here is computed at HOP-second steps using only samples at or before the step
time, so the same code runs offline (evaluate.py) and online (live.py).

Columns (order is the FEATURES list). All raw units, un-normalised: normalisation against
the person's own calibration window is the model's job (see train.py), not the feature's.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from .parse import FS, Session

HOP = 1.0          # seconds between feature rows
MIN_HISTORY = 60.0 # seconds before the first row (needs HRV windows)

# Flex sensor divider on A0 (bench notes, 11-14 Sep 2026): sensor from 3V3 to A0, 10 kOhm from A0 to GND,
# 10-bit ADC. R_flex = R_FIXED * (1023 / adc - 1). Dormio (Horowitz 2020, sec. 2.2) expresses its
# flex threshold in kOhm (8 kOhm), so the detector works in kOhm, not ADC counts.
FLEX_R_FIXED_KOHM = 10.0


def flex_kohm(adc):
    a = np.clip(np.asarray(adc, dtype=float), 1, 1022)
    return FLEX_R_FIXED_KOHM * (1023.0 / a - 1.0)

FEATURES = [
    'fsr_mean10',    # grip, mean over 10 s
    'fsr_std10',     # grip variability over 10 s (re-grips, jerks)
    'fsr_slope30',   # grip trend, ADC/s over 30 s
    'hr_mean30',     # bpm from beats in last 30 s
    'hr_mean120',    # bpm over 120 s (slow trend)
    'rmssd60',       # ms, short-term HRV over 60 s
    'sdnn60',        # ms
    'rsa60',         # ms, respiratory modulation amplitude of IBI (0.15-0.4 Hz band) over 60 s
    'resp_hz30',     # breathing rate from PPG baseline wander over 30 s
    'ppg_amp10',     # pulse amplitude (P95-P5 of waveform) over 10 s
    'beat_ok30',     # fraction of expected beats actually detected (signal-quality gate)
    'ppg_amp30',     # pulse amplitude, median of per-second P95-P5 over 30 s (vasodilation rises at onset)
    'ppg_amp_slope60',  # trend of pulse amplitude over 60 s
    'resp_cv60',     # breathing irregularity: CV of breath intervals from PPG wander over 60 s
    'fsr_dropout30', # seconds in last 30 s with grip below 25 % of the 10 s value at row start... see code
    'fsr_spikes30',  # count of brief (<300 ms) grip spikes > 3 SD over 30 s (hypnic jerks)
    'lfhf120',       # LF/HF ratio of IBI tachogram over 120 s (falls toward onset)
    'flex_kohm10',   # flex sensor resistance, kOhm, mean over 10 s (Dormio's unit)
    'hr_mean15',     # bpm over 15 s (openSleep HeartQueue window)
    'rssi_mean10',   # palm-XIAO BLE RSSI, dBm, mean over 10 s (nan when no rssi capture)
    'rssi_std10',    # RSSI fluctuation, dB, over 10 s
    'rssi_slope30',  # RSSI trend, dB/s over 30 s
]


def _slope(y: np.ndarray, x: np.ndarray) -> float:
    if len(y) < 3 or np.all(np.isnan(y)):
        return 0.0
    m = ~np.isnan(y)
    if m.sum() < 3:
        return 0.0
    return float(np.polyfit(x[m], y[m], 1)[0])


def _beats_from_ppg(ppg: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Own peak detector on the PPG waveform (fallback / cross-check for firmware beats).
    Returns (beat_times, ibi_ms)."""
    x = ppg - np.nanmean(ppg)
    x = np.nan_to_num(x)
    pk, _ = find_peaks(x, distance=int(0.33 * FS), prominence=np.nanstd(x) * 0.8)
    bt = t[pk]
    ibi = np.diff(bt) * 1000
    return bt[1:], ibi


def _hrv(ibi: np.ndarray) -> tuple[float, float]:
    ibi = ibi[(ibi > 300) & (ibi < 2000)]
    if len(ibi) < 5:
        return np.nan, np.nan
    d = np.diff(ibi)
    return float(np.sqrt(np.mean(d ** 2))), float(np.std(ibi))


def _rsa(bt: np.ndarray, ibi: np.ndarray) -> float:
    """Band-limited (0.15-0.4 Hz) amplitude of the IBI tachogram, in ms."""
    if len(ibi) < 8:
        return np.nan
    g = np.arange(bt[0], bt[-1], 0.25)
    if len(g) < 32:
        return np.nan
    x = np.interp(g, bt, ibi)
    x = x - x.mean()
    spec = np.fft.rfft(x * np.hanning(len(x)))
    f = np.fft.rfftfreq(len(x), 0.25)
    band = (f >= 0.15) & (f <= 0.4)
    return float(np.sqrt(np.sum(np.abs(spec[band]) ** 2)) / len(x) * 2)


def _resp_hz(ppg: np.ndarray) -> float:
    x = np.nan_to_num(ppg - np.nanmean(ppg))
    if len(x) < 10 * FS:
        return np.nan
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / FS)
    band = (f >= 0.1) & (f <= 0.5)
    if not band.any():
        return np.nan
    return float(f[band][np.argmax(spec[band])])


def _resp_cv(ppg: np.ndarray) -> float:
    """CV of breath-to-breath intervals from the PPG baseline wander (0.1-0.5 Hz)."""
    x = np.nan_to_num(ppg - np.nanmean(ppg))
    if len(x) < 20 * FS:
        return np.nan
    k = int(1.0 * FS)
    wander = np.convolve(x, np.ones(k) / k, 'same')      # crude low-pass ~1 Hz
    pk, _ = find_peaks(wander, distance=int(1.5 * FS), prominence=np.std(wander) * 0.5)
    if len(pk) < 4:
        return np.nan
    iv = np.diff(pk) / FS
    return float(np.std(iv) / np.mean(iv))


def _lfhf(bt: np.ndarray, ibi: np.ndarray) -> float:
    if len(ibi) < 16:
        return np.nan
    g = np.arange(bt[0], bt[-1], 0.25)
    if len(g) < 100:
        return np.nan
    x = np.interp(g, bt, ibi); x = x - x.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 0.25)
    lf = spec[(f >= 0.04) & (f < 0.15)].sum(); hf = spec[(f >= 0.15) & (f <= 0.4)].sum()
    return float(lf / hf) if hf > 0 else np.nan


def _spikes(fsr: np.ndarray) -> float:
    x = np.nan_to_num(fsr - np.nanmedian(fsr))
    sd = np.nanstd(x) + 1e-6
    pk, props = find_peaks(x, height=3 * sd, width=(1, int(0.3 * FS)), distance=int(0.5 * FS))
    return float(len(pk))


def feature_row(t_now: float, t: np.ndarray, fsr: np.ndarray, ppg: np.ndarray,
                beat_t: np.ndarray, beat_ibi: np.ndarray, rssi: np.ndarray | None = None) -> list[float]:
    """One row at time t_now using history only. Arrays are the full session or live buffer."""
    def win(sig: np.ndarray, secs: float) -> np.ndarray:
        i1 = int(round(t_now * FS)) + 1
        i0 = max(0, i1 - int(secs * FS))
        return sig[i0:i1]

    def tw(secs: float) -> np.ndarray:
        i1 = int(round(t_now * FS)) + 1
        i0 = max(0, i1 - int(secs * FS))
        return t[i0:i1]

    def beats(secs: float) -> tuple[np.ndarray, np.ndarray]:
        m = (beat_t <= t_now) & (beat_t > t_now - secs)
        return beat_t[m], beat_ibi[m]

    f10, f30 = win(fsr, 10), win(fsr, 30)
    b30t, b30 = beats(30); b60t, b60 = beats(60); b120t, b120 = beats(120); _, b15 = beats(15)
    p30 = win(ppg, 30)
    # per-second pulse amplitude over 30 s and 60 s
    def amp_series(seg: np.ndarray) -> np.ndarray:
        n = len(seg) // int(FS)
        if n == 0:
            return np.array([])
        blk = seg[: n * int(FS)].reshape(n, int(FS))
        return np.nanpercentile(blk, 95, axis=1) - np.nanpercentile(blk, 5, axis=1)
    a30 = amp_series(p30); a60 = amp_series(win(ppg, 60))
    f30_floor = 0.25 * (np.nanmean(f30[: int(FS)]) if len(f30) else np.nan)
    hr30 = 60000 / np.mean(b30) if len(b30) else np.nan
    hr120 = 60000 / np.mean(b120) if len(b120) else np.nan
    rmssd, sdnn = _hrv(b60)
    p10 = win(ppg, 10)
    expected = 30 * (hr30 / 60) if np.isfinite(hr30) else np.nan
    return [
        float(np.nanmean(f10)),
        float(np.nanstd(f10)),
        _slope(f30, tw(30)),
        hr30,
        hr120,
        rmssd,
        sdnn,
        _rsa(b60t, b60),
        _resp_hz(win(ppg, 30)),
        float(np.nanpercentile(p10, 95) - np.nanpercentile(p10, 5)) if len(p10) else np.nan,
        float(len(b30) / expected) if expected and np.isfinite(expected) and expected > 0 else np.nan,
        float(np.nanmedian(a30)) if len(a30) else np.nan,
        _slope(a60, np.arange(len(a60), dtype=float)) if len(a60) > 3 else np.nan,
        _resp_cv(win(ppg, 60)),
        float(np.nansum(f30 < f30_floor) / FS) if len(f30) and np.isfinite(f30_floor) else np.nan,
        _spikes(f30) if len(f30) > FS else np.nan,
        _lfhf(b120t, b120),
        float(np.nanmean(flex_kohm(f10))),
        (60000 / np.mean(b15) if len(b15) else np.nan),
        (float(np.nanmean(win(rssi, 10))) if rssi is not None and np.isfinite(win(rssi, 10)).any() else np.nan),
        (float(np.nanstd(win(rssi, 10))) if rssi is not None and np.isfinite(win(rssi, 10)).any() else np.nan),
        (_slope(win(rssi, 30), tw(30)) if rssi is not None and np.isfinite(win(rssi, 30)).sum() > 3 else np.nan),
    ]


def build_features(s: Session, hop: float = HOP) -> tuple[np.ndarray, np.ndarray]:
    """Return (times, X) with X shape (n_rows, len(FEATURES)). Causal."""
    times = np.arange(MIN_HISTORY, s.duration, hop)
    X = np.array([feature_row(tn, s.t, s.fsr, s.ppg, s.beat_t, s.beat_ibi, s.rssi) for tn in times])
    return times, X
