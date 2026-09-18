# -*- coding: utf-8 -*-
"""因子衰减预测"""

import warnings
import numpy as np
import pandas as pd
from config import DECAY_PREDICT
from factor_dsl import compute_ic

warnings.filterwarnings("ignore")


def extract_ic_series(factor, pool, window=None, step=None):
    cfg = DECAY_PREDICT
    window = window or cfg["ic_window_bars"]
    step = step or cfg["ic_step_bars"]
    ref_code = next(iter(pool))
    if ref_code not in factor.get("impl", {}):
        return pd.Series(dtype=float)
    f_vals = factor["impl"][ref_code]["factor"]
    df = pool[ref_code]
    fwd = df["close"].pct_change().shift(-1)
    pair = pd.concat([f_vals, fwd], axis=1).dropna()
    if len(pair) < window:
        return pd.Series(dtype=float)
    ics, idx = [], []
    for i in range(window, len(pair), step):
        sub = pair.iloc[i - window:i]
        a, b = sub.iloc[:, 0], sub.iloc[:, 1]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        ics.append(a.corr(b, method="spearman"))
        idx.append(pair.index[i])
    return pd.Series(ics, index=idx)


def _predict_arima(ic, horizon):
    try:
        from statsmodels.tsa.arima.model import ARIMA
        model = ARIMA(ic, order=(2, 1, 2))
        fit = model.fit()
        forecast = fit.forecast(steps=horizon)
        return float(forecast.iloc[-1])
    except Exception:
        if len(ic) < 5:
            return float(ic[-1]) if len(ic) > 0 else 0.0
        x = np.arange(len(ic))
        coef = np.polyfit(x[-20:], ic[-20:], 1)
        return float(np.polyval(coef, len(ic) + horizon - 1))


class FactorDecayPredictor:
    def __init__(self, params=None):
        self.p = {**DECAY_PREDICT, **(params or {})}
        self.predictions = {}

    def predict_one(self, factor, pool):
        ic_series = extract_ic_series(factor, pool)
        if len(ic_series) < 10:
            return {"factor": factor["name"], "current_ic": 0.0,
                    "predicted_ic": 0.0, "delta": 0.0,
                    "alert": "no_data"}
        ic = ic_series.values
        current = float(ic[-1])
        horizon = self.p["forecast_horizon"]
        predicted = _predict_arima(ic, horizon)
        current_abs, predicted_abs = abs(current), abs(predicted)
        delta = (predicted_abs - current_abs) * np.sign(current)
        if delta < self.p["danger_threshold"]:
            alert = "danger"
        elif delta < self.p["warn_threshold"]:
            alert = "warn"
        else:
            alert = "ok"
        return {"factor": factor["name"], "current_ic": current,
                "predicted_ic": predicted, "delta": delta,
                "alert": alert, "n_ic_points": len(ic_series)}

    def predict_all(self, factors, pool):
        print("\n========== 因子衰减预测 ==========")
        if not self.p["enabled"]:
            return pd.DataFrame()
        rows = []
        for f in factors:
            r = self.predict_one(f, pool)
            self.predictions[f["name"]] = r
            rows.append(r)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df = df.sort_values("delta").reset_index(drop=True)
        n_d = (df["alert"] == "danger").sum()
        n_w = (df["alert"] == "warn").sum()
        print(f"  🔴 危险: {n_d}  🟡 警告: {n_w}  "
              f"🟢 正常: {len(df) - n_d - n_w}")
        return df


def predict_factor_decay(factors, pool):
    predictor = FactorDecayPredictor()
    df = predictor.predict_all(factors, pool)
    if not df.empty:
        df.to_csv("factor_decay_predict.csv", index=False,
                  encoding="utf-8-sig")
    return df