# -*- coding: utf-8 -*-
"""衰减预测可解释性（SHAP）"""

import warnings
import numpy as np
import pandas as pd
from config import DECAY_EXPLAIN as CFG
from factor_decay_predict import extract_ic_series

warnings.filterwarnings("ignore")


def build_features(ic_series, n_lags=10, extra_features=None,
                    pool=None, ref_code=None):
    ic = ic_series.values
    if len(ic) < n_lags + 10:
        return None, None, []
    X, y = [], []
    for i in range(n_lags, len(ic)):
        X.append(ic[i - n_lags:i])
        y.append(ic[i])
    feature_names = [f"lag_{k}" for k in range(n_lags, 0, -1)]
    X = np.array(X)
    y = np.array(y)

    extra_cols = {}
    if extra_features:
        for feat in extra_features:
            col = []
            for i in range(n_lags, len(ic)):
                window = ic[i - n_lags:i]
                if feat == "ic_mean_5":
                    col.append(np.mean(window[-5:]))
                elif feat == "ic_std_5":
                    col.append(np.std(window[-5:]))
                elif feat == "ic_slope_5":
                    if len(window) >= 5:
                        x = np.arange(5)
                        coef = np.polyfit(x, window[-5:], 1)
                        col.append(coef[0])
                    else:
                        col.append(0.0)
                else:
                    col.append(0.0)
            extra_cols[feat] = col
            feature_names.append(feat)
    if extra_cols:
        X = np.hstack([X, np.array(list(extra_cols.values())).T])
    return pd.DataFrame(X, columns=feature_names), pd.Series(y), feature_names


def shap_explain(X, y, feature_names):
    try:
        import lightgbm as lgb
    except ImportError:
        return _fallback(X, y, feature_names)

    model = lgb.LGBMRegressor(n_estimators=100, max_depth=4,
                               learning_rate=0.05, verbose=-1)
    model.fit(X, y)
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)
        importance = np.abs(shap_values).mean(axis=0)
        imp_df = pd.DataFrame({"feature": feature_names,
                                "importance": importance})
        imp_df = imp_df.sort_values("importance",
                                     ascending=False).reset_index(drop=True)
        return {"method": "shap", "importance": imp_df,
                "shap_values": shap_values}
    except ImportError:
        imp = model.feature_importances_
        imp_df = pd.DataFrame({"feature": feature_names,
                                "importance": imp / (imp.sum() + 1e-9)})
        imp_df = imp_df.sort_values("importance",
                                     ascending=False).reset_index(drop=True)
        return {"method": "lightgbm_gain", "importance": imp_df}


def _fallback(X, y, feature_names):
    cors = []
    for col in feature_names:
        c = X[col].corr(y)
        cors.append(abs(c) if not np.isnan(c) else 0.0)
    imp_df = pd.DataFrame({"feature": feature_names,
                            "importance": np.array(cors) /
                                          (sum(cors) + 1e-9)})
    imp_df = imp_df.sort_values("importance",
                                 ascending=False).reset_index(drop=True)
    return {"method": "lag_corr", "importance": imp_df}


def explain_decay(factor, pool):
    ic_series = extract_ic_series(factor, pool)
    if len(ic_series) < 20:
        return {"factor": factor["name"], "error": "IC 序列太短"}
    n_lags = CFG["n_lags"]
    X, y, names = build_features(ic_series, n_lags,
                                   CFG["extra_features"], pool,
                                   next(iter(pool)))
    if X is None:
        return {"factor": factor["name"], "error": "特征构造失败"}
    shap_result = shap_explain(X, y, names)
    imp = shap_result["importance"]
    top_lags = imp[imp["feature"].str.startswith("lag_")].head(
        CFG["top_n_lags"])
    top_extra = imp[~imp["feature"].str.startswith("lag_")].head(3)

    lag_importance = {}
    for _, row in top_lags.iterrows():
        k = int(row["feature"].split("_")[1])
        lag_importance[k] = row["importance"]

    if lag_importance:
        recent = sum(v for k, v in lag_importance.items() if k <= 3)
        old = sum(v for k, v in lag_importance.items() if k > 3)
        if recent > old * 1.5:
            root = "因子依赖近期 IC，短期波动，可能恢复"
        elif old > recent * 1.5:
            root = "因子依赖久远 IC，趋势性衰减，建议重挖"
        else:
            root = "衰减原因不明确"
    else:
        root = "无有效 lag"

    return {"factor": factor["name"], "method": shap_result["method"],
            "importance": imp, "top_lags": top_lags,
            "top_extra": top_extra, "lag_importance": lag_importance,
            "root_cause": root, "shap_result": shap_result,
            "ic_series": ic_series}


def explain_all(factors, pool):
    print("\n========== 衰减可解释性 ==========")
    if not CFG["enabled"]:
        return {}
    results = {}
    rows = []
    for f in factors:
        r = explain_decay(f, pool)
        results[f["name"]] = r
        if "error" in r:
            continue
        top1 = r["top_lags"].iloc[0] if not r["top_lags"].empty else None
        rows.append({"factor": f["name"],
                     "method": r["method"],
                     "top_lag": top1["feature"] if top1 is not None else "",
                     "top_lag_importance":
                         top1["importance"] if top1 is not None else 0,
                     "root_cause": r["root_cause"]})
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv("decay_explanation.csv", index=False,
                  encoding="utf-8-sig")
    try:
        from llm_shap_explainer import generate_llm_report
        llm_report = generate_llm_report(factors, results)
        print(f"  ✅ LLM 生成 {len(llm_report)} 个解释")
    except Exception as e:
        print(f"  ⚠️ LLM 解释失败: {e}")
    return results