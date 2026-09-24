"""
THE DETECTOR.  This is the file the autoresearch loop edits (plus onset/features.py).

Contract used by evaluate.py and live.py:
  build_features(session)            -> (times, X)   causal feature rows at 1 Hz
  fit(train_list)                    -> model         train_list = [(session, times, X), ...]
  predict(model, times, X, session)  -> bool array    True where the detector would fire.
                                                       MUST be causal: row i may only use rows <= i.
  drowsiness_score(model, times, X)  -> float array   what the GUI plots; fires when > model['thresh']

Two models, switched by MODEL:

  'dormio'   Horowitz, Cunningham, Maes & Stickgold 2020, Conscious Cogn 83:102938, sec. 2.2:
             "These three measures are averaged over the first 120 seconds when the subject
             initially lies down, and predefined deviations from these mean values are interpreted
             as Dormio-defined sleep onset ... Heart Rate (BPM) deltas of > 5 BPM ... or flexor
             muscle sensor deltas of > 8 KOhm were taken as indications of sleep onset. Awakenings
             were performed whenever one of these thresholds was passed."
             Implemented here with the two channels this build has, flex and heart rate. There is
             no EDA channel in this project. Score = max(|dHR| / 5, |dFlex| / 8); fire at >= 1
             sustained for HOLD_S. The paper does not state the averaging window for the live
             value or whether the delta is signed; the openSleep app used abs(). Both are parameters.
             The paper does NOT use heart-rate variability; the optional HRV term is our addition
             and is off by default.

  'logreg'   our learned combiner (see git history): per-session z-scores vs the calibration
             window, logistic regression, labels from the session's own state marks.
"""
from __future__ import annotations

import numpy as np

from onset.features import FEATURES, build_features as _build  # noqa: F401  (re-exported)
from onset.parse import Session

MODEL = 'dormio'          # 'dormio' | 'logreg'
CALIB_S = 120.0           # Horowitz 2020 sec. 2.2 says 120 s; its Figure 1 draws 3:00. Text wins.
HOLD_S = 8                # seconds a condition must persist (paper: unstated; 1 = fire on first sample)
SMOOTH_S = 4              # causal moving average on the score

# ---- dormio parameters (paper values) ----
DORMIO_DELTA_HR_BPM = 5.0       # sec. 2.2 provisional threshold
DORMIO_DELTA_FLEX_KOHM = 8.0    # sec. 2.2 provisional threshold
DORMIO_HR_FEATURE = 'hr_mean15'     # openSleep HeartQueue window is 15 s
DORMIO_FLEX_FEATURE = 'flex_kohm10'
DORMIO_SIGNED = False           # False = abs() as in the openSleep app; True = HR down / flex down only
# Optional HRV term. NOT in the paper. What the cited and related evidence actually supports
# (Shinar et al. 2006, Auton Neurosci 130:17, abstract): across sleep onset RR lengthens, VLF and LF
# power fall (VLF from ~2 min before EEG onset), HF does not change, so LF/HF falls. No source reports
# RMSSD or SDNN at N1. So the defensible HRV term is a DECREASE in LF/HF over a >= 120 s window.
# Set DORMIO_HRV = ('lfhf120', -1, 0.5) to enable: (feature, sleep-direction sign, delta threshold).
DORMIO_HRV = None

def build_features(s: Session):
    return _build(s)


LR_FEATURES = ['fsr_mean10', 'ppg_amp30', 'hr_mean30', 'resp_cv60', 'fsr_slope30', 'fsr_dropout30', 'ppg_amp_slope60']


def _zscore(times: np.ndarray, X: np.ndarray, names) -> np.ndarray:
    """Per-session z-scores of the named features against the session's own awake calibration."""
    calib = times <= CALIB_S
    if calib.sum() < 10:
        calib = np.zeros(len(times), bool); calib[:10] = True
    mu = np.nanmean(X[calib], axis=0)
    sd = np.nanstd(X[calib], axis=0) + 1e-6
    sd = np.maximum(sd, 0.05 * np.abs(mu) + 1e-6)      # floor: a flat calibration must not explode
    cols = [FEATURES.index(n) for n in names]
    z = (X[:, cols] - mu[cols]) / sd[cols]
    return np.clip(np.nan_to_num(z, nan=0.0), -4, 8)


def _calib_mean(times, X, cols):
    calib = times <= CALIB_S
    if calib.sum() < 10:
        calib = np.zeros(len(times), bool); calib[:10] = True
    return np.nanmean(X[calib][:, cols], axis=0)


def fit(train_list):
    if MODEL == 'dormio':
        return dict(kind='dormio', thresh=1.0, hold=HOLD_S, smooth=SMOOTH_S,
                    d_hr=DORMIO_DELTA_HR_BPM, d_flex=DORMIO_DELTA_FLEX_KOHM, signed=DORMIO_SIGNED,
                    hrv=DORMIO_HRV[0] if DORMIO_HRV else None,
                    hrv_sign=DORMIO_HRV[1] if DORMIO_HRV else 1.0,
                    d_hrv=DORMIO_HRV[2] if DORMIO_HRV else 1.0)
    return _fit_logreg(train_list)


def _fit_logreg(train_list):
    """Logistic regression on baseline-normalised features; labels from the session's own state marks."""
    from sklearn.linear_model import LogisticRegression
    Zs, ys = [], []
    for s, times, X in train_list:
        y = s.state_at(times)
        m = y != 1                         # drop 'drowsy' rows: ambiguous
        Zs.append(_zscore(times, X, LR_FEATURES)[m]); ys.append((y[m] == 2).astype(int))
    Z = np.vstack(Zs); y = np.concatenate(ys)
    clf = LogisticRegression(C=0.1, class_weight='balanced', max_iter=500).fit(Z, y)
    return dict(kind='logreg', thresh=0.0, hold=HOLD_S, smooth=SMOOTH_S, w=clf.coef_[0], b=float(clf.intercept_[0]))


def _causal_mean(x: np.ndarray, n: int) -> np.ndarray:
    out = np.empty_like(x)
    for i in range(len(x)):
        seg = x[max(0, i - n + 1): i + 1]
        out[i] = np.nanmean(seg) if np.isfinite(seg).any() else np.nan
    return out


def dormio_deltas(model, times: np.ndarray, X: np.ndarray):
    """Per-row (dHR bpm, dFlex kOhm[, dHRV]) from the first-CALIB_S mean, as the paper defines them."""
    names = [DORMIO_HR_FEATURE, DORMIO_FLEX_FEATURE] + ([model['hrv']] if model.get('hrv') else [])
    cols = [FEATURES.index(n) for n in names]
    mu = _calib_mean(times, X, cols)
    d = X[:, cols] - mu
    return d, names


def drowsiness_score(model, times: np.ndarray, X: np.ndarray) -> np.ndarray:
    if model.get('kind') == 'dormio':
        d, _ = dormio_deltas(model, times, X)
        lim = [model['d_hr'], model['d_flex']] + ([model['d_hrv']] if model.get('hrv') else [])
        if model['signed']:                       # sleep direction: HR down, flex resistance down, HRV per its sign
            sign = np.array([-1.0, -1.0] + ([float(model.get('hrv_sign', 1.0))] if model.get('hrv') else []))
            r = (d * sign) / np.array(lim)
        else:
            r = np.abs(d) / np.array(lim)
        r = np.nan_to_num(r, nan=0.0)
        return _causal_mean(np.max(r, axis=1), model['smooth'])
    z = _zscore(times, X, LR_FEATURES)
    logit = z @ model['w'] + model['b']
    return _causal_mean(logit, model['smooth'])


def predict(model, times: np.ndarray, X: np.ndarray, s: Session):
    score = drowsiness_score(model, times, X)
    above = score > model['thresh']
    run = 0
    trig = np.zeros(len(times), bool)
    for i, a in enumerate(above):
        run = run + 1 if a else 0
        if run >= model['hold']:
            trig[i] = True
    return trig
