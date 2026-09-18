# -*- coding: utf-8 -*-
"""策略生命周期 + 因子衰减监控"""

import numpy as np
import pandas as pd
from config import STRATEGY_LIFECYCLE, FACTOR_DECAY


class StrategyLifecycleManager:
    def __init__(self, strategy_names, params=None):
        self.p = {**STRATEGY_LIFECYCLE, **(params or {})}
        self.weights = {n: 1.0 for n in strategy_names}
        self.negative_windows = {n: 0 for n in strategy_names}
        self.active = {n: True for n in strategy_names}
        self.history = []

    @staticmethod
    def _rolling_sharpe(returns, window):
        r = returns.tail(window).dropna()
        if len(r) < 30:
            return 0.0
        s = r.std()
        if s < 1e-12:
            return 0.0
        return float(r.mean() / s * np.sqrt(240 * 252))

    def evaluate(self, equity_df, current_bar):
        if not self.p["enabled"]:
            return {}
        rets_df = equity_df.pct_change().dropna()
        if len(rets_df) < self.p["eval_window_bars"] // 2:
            return {}
        changes = {}
        for name in self.weights:
            if name not in rets_df.columns:
                continue
            sharpe = self._rolling_sharpe(rets_df[name],
                                          self.p["eval_window_bars"])
            if sharpe < self.p["sharpe_threshold"]:
                self.negative_windows[name] += 1
            else:
                self.negative_windows[name] = 0
            if (self.negative_windows[name] >=
                    self.p["max_negative_windows"]):
                old = self.weights[name]
                self.weights[name] *= self.p["weight_decay"]
                changes[name] = {"action": "decay", "sharpe": sharpe,
                                 "old_weight": old,
                                 "new_weight": self.weights[name]}
                self.negative_windows[name] = 0
            if self.weights[name] < self.p["min_weight"]:
                if sum(1 for a in self.active.values() if a) > \
                        self.p["min_active_strategies"]:
                    self.active[name] = False
                    changes[name] = {"action": "remove", "sharpe": sharpe}
            if not self.active[name] and sharpe > self.p["recover_sharpe"]:
                self.active[name] = True
                self.weights[name] = self.p["min_weight"]
                changes[name] = {"action": "recover", "sharpe": sharpe}
        if changes:
            self.history.append({"bar": current_bar,
                                 "changes": changes,
                                 "weights": dict(self.weights)})
        return changes

    def get_active_weights(self):
        active_w = {n: w for n, w in self.weights.items() if self.active[n]}
        total = sum(active_w.values())
        if total < 1e-9:
            return {}
        return {n: w / total for n, w in active_w.items()}

    def get_state(self):
        rows = []
        for name in self.weights:
            rows.append({"策略": name,
                         "权重": round(self.weights[name], 4),
                         "活跃": self.active[name]})
        return pd.DataFrame(rows)


class FactorDecayMonitor:
    def __init__(self, params=None):
        self.p = {**FACTOR_DECAY, **(params or {})}
        self.ic_history = {}
        self.decay_count = {}
        self.alerts = []

    @staticmethod
    def _estimate_halflife(ic_series):
        s = ic_series.dropna().abs()
        if len(s) < 30:
            return np.inf
        t = np.arange(len(s))
        y = np.log(s.values + 1e-6)
        try:
            A = np.vstack([t, np.ones_like(t)]).T
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            lam = -coef[0]
            if lam <= 1e-6:
                return np.inf
            return float(np.log(2) / lam)
        except Exception:
            return np.inf

    @staticmethod
    def _rolling_ic(factor, fwd, window, step=60):
        df = pd.concat([factor, fwd], axis=1).dropna()
        if df.empty or len(df) < window:
            return pd.Series(dtype=float)
        vals, idx = [], []
        for i in range(window, len(df), step):
            sub = df.iloc[i - window:i]
            a, b = sub.iloc[:, 0], sub.iloc[:, 1]
            if a.std() < 1e-9 or b.std() < 1e-9:
                continue
            vals.append(a.corr(b, method="spearman"))
            idx.append(df.index[i])
        return pd.Series(vals, index=idx)

    def evaluate(self, factors, pool):
        if not self.p["enabled"]:
            return []
        ref_code = next(iter(pool))
        df = pool[ref_code]
        fwd = df["close"].pct_change().shift(-1)
        new_alerts = []
        for f in factors:
            name = f["name"]
            impl = f.get("impl", {})
            if ref_code not in impl:
                continue
            ic_s = self._rolling_ic(impl[ref_code]["factor"], fwd,
                                     self.p["ic_window_bars"])
            if ic_s.empty:
                continue
            self.ic_history.setdefault(name, []).append(ic_s)
            latest_ic = ic_s.iloc[-1]
            hl = self._estimate_halflife(ic_s)
            decayed = (abs(latest_ic) < self.p["ic_min_threshold"] or
                       hl < self.p["halflife_warn"])
            if decayed:
                self.decay_count[name] = self.decay_count.get(name, 0) + 1
            else:
                self.decay_count[name] = 0
            if self.decay_count[name] >= self.p["consecutive_decay_rounds"]:
                alert = {"factor": name, "latest_ic": float(latest_ic),
                         "halflife": hl,
                         "action": "trigger_remining"}
                new_alerts.append(alert)
                self.alerts.append(alert)
        return new_alerts

    def get_state(self):
        rows = []
        for name, ic_list in self.ic_history.items():
            if not ic_list:
                continue
            latest = ic_list[-1]
            hl = self._estimate_halflife(latest)
            rows.append({"因子": name,
                         "最新IC": round(float(latest.iloc[-1]), 4),
                         "半衰期(bar)": round(hl, 1)
                         if np.isfinite(hl) else "∞",
                         "衰减计数": self.decay_count.get(name, 0)})
        return pd.DataFrame(rows)

    def get_alerts_for_remining(self):
        return [a for a in self.alerts
                if a.get("action") == "trigger_remining"]

    def reset_decay_counter(self, name):
        if name in self.decay_count:
            self.decay_count[name] = 0


def combine_with_lifecycle(equity_df, lifecycle, eval_every=240):
    if equity_df.empty:
        return pd.Series(dtype=float)
    rets_df = equity_df.pct_change().fillna(0.0)
    port_rets = pd.Series(0.0, index=rets_df.index)
    current = {c: 1.0 / len(rets_df.columns) for c in rets_df.columns}
    for i, ts in enumerate(rets_df.index):
        if i % eval_every == 0 and i > 0:
            lifecycle.evaluate(equity_df.iloc[:i], i)
            w = lifecycle.get_active_weights()
            if w:
                current = w
        r = 0.0
        for name, w in current.items():
            if name in rets_df.columns:
                r += w * rets_df.loc[ts, name]
        port_rets.iloc[i] = r
    return (1 + port_rets).cumprod() * equity_df.iloc[0].mean()