# -*- coding: utf-8 -*-
"""因子衰减预测

用滚动 IC 序列外推因子未来预测力：IC 绝对值显著走低 => 因子
失效预警（danger/warn），提示重挖或降权。"""

import warnings
import numpy as np
import pandas as pd
from config import DECAY_PREDICT, RESULTS_DIR
from factor_dsl import compute_ic, safe_spearman


def extract_ic_series(factor, pool, window=None, step=None):
    """滚动 IC 时间序列（参考标的代理）：窗口 window 内因子值与
    下期收益的 Spearman 相关，每 step 采样一个点。"""
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
        ics.append(safe_spearman(a, b))
        idx.append(pair.index[i])
    return pd.Series(ics, index=idx)


def _predict_arima(ic, horizon):
    """预测 horizon 步后的 IC 水平：ARIMA(2,1,2) 差分整合一次
    （IC 序列常带趋势）；拟合失败/库缺失时回退到最近 20 点
    线性趋势外推。"""
    try:
        from statsmodels.tsa.arima.model import ARIMA
        with warnings.catch_warnings():
            # ARIMA 小样本常不收敛（ConvergenceWarning），结果仍可用；
            # 局部抑制，避免全局 filterwarnings 被其他库重置后仍然刷屏
            warnings.simplefilter("ignore")
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
        """单因子衰减判定。delta = (|预测IC| - |当前IC|) · sign(当前IC)：
        带符号的"有效预测力变化量"（负=衰减）。
        alert: delta < danger_threshold => danger；
               delta < warn_threshold  => warn；否则 ok。"""
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
        df.to_csv(f"{RESULTS_DIR}/factor_decay_predict.csv",
                  index=False, encoding="utf-8-sig")
    return df