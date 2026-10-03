# -*- coding: utf-8 -*-
"""验证 etf_admission 的截面原语：关键算法全部与"照着定义写的暴力版"对拍。

对拍的是最容易出错的六件事：
1. 逐日截面相关（秩变换 + 缺失格 + 常量日 + 薄截面跳过）
2. 前向 h 日收益（跨护栏日必须整段作废）
3. 分层归组、单调性与高分侧换手
4. 判重的 complete-case 逐日两两相关
5. 组合回放的成本恒等式（平值池上净收益 = -手续费）
6. 数据装载的三道池子筛选是否真按 docstring 执行
"""
import os
import shutil
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "etf", "v1", "src"))
import _bootstrap  # noqa: E402,F401
import etf_admission as EA  # noqa: E402

rng = np.random.default_rng(11)
DATES = pd.bdate_range("2020-01-01", periods=90)
CODES = [f"5103{i:02d}" for i in range(40)]

fac = pd.DataFrame(rng.normal(size=(len(DATES), len(CODES))), index=DATES, columns=CODES)
fwd = fac * 0.3 + pd.DataFrame(rng.normal(size=fac.shape), index=DATES, columns=CODES)
fac.iloc[5, 3] = np.nan
fwd.iloc[5, 4] = np.nan
fac.iloc[70:, 35:] = np.nan        # 后段一批标的退市 → 截面变薄
fac.iloc[0, :] = np.nan
fac.iloc[1, 6:] = np.nan            # 第 2 日只剩 6 只有值 → 应被 MIN_CS 丢弃


def brute_ic(a, b, min_cs, rank=False):
    """暴力版：逐日 concat→dropna→corr，完全照着定义写，不共用模块里的任何代码。"""
    out = {}
    for d in a.index:
        j = pd.concat([a.loc[d].rename("f"), b.loc[d].rename("r")],
                  axis=1).dropna()
        if len(j) < min_cs:
            continue
        x, y = j.iloc[:, 0], j.iloc[:, 1]
        if rank:
            x, y = x.rank(), y.rank()
        if x.std() == 0 or y.std() == 0:
            continue
        out[d] = float(np.corrcoef(x, y)[0, 1])
    return pd.Series(out)


# ---------- 1. 逐日截面相关 ----------
p, s, n = EA.daily_cs_ic(fac, fwd, min_cs=20)
bp, bs = brute_ic(fac, fwd, 20), brute_ic(fac, fwd, 20, rank=True)
print("1) Pearson 最大绝对差 %.2e（模块 %d 天 / 暴力 %d 天）"
      % ((p.dropna() - bp).abs().max(), int(p.notna().sum()), len(bp)))
print("   Spearman 最大绝对差 %.2e" % ((s.dropna() - bs).abs().max()))
print("   第2日仅 6 只 → 该日应为 NaN:", bool(pd.isna(s.iloc[1])), " n=", int(n.iloc[1]))
assert (p.dropna() - bp).abs().max() < 1e-10 and (s.dropna() - bs).abs().max() < 1e-10
assert pd.isna(s.iloc[1]) and n.iloc[1] == 6
st = EA.ic_summary(s, n, min_cs=20)
print("   ic_summary:", {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in st.items()})
assert st["days"] == len(bs) and st["days_thin"] == int((n < 20).sum())
assert abs(EA.daily_cs_ic(fac, fac.rank(axis=1), min_cs=20)[1].mean() - 1) < 1e-12
const = fac.copy(); const.iloc[:] = 1.0
assert EA.daily_cs_ic(const, fwd, min_cs=20)[1].isna().all()
print("   完美预测 RankIC≡1 ✓ / 常量列全 NaN（不假装「不相关」）✓")

# ---------- 2. 前向收益与护栏 ----------
close = pd.DataFrame(100.0 * np.cumprod(1 + rng.normal(0, 0.01, fac.shape), axis=0),
                     index=DATES, columns=CODES)
ret = close.pct_change(fill_method=None)
f3 = EA.forward_returns(ret, 3)
print("2) 前向3日 vs 价格比 最大差 %.2e"
      % float((f3.iloc[10] - (close.iloc[13] / close.iloc[10] - 1)).abs().max()))
assert float((f3.iloc[10] - (close.iloc[13] / close.iloc[10] - 1)).abs().max()) < 1e-12
r2 = ret.copy()
r2.iloc[11, 0] = np.nan
f3b = EA.forward_returns(r2, 3)
print("   跨缺失日整段作废:", bool(np.isnan(f3b.iloc[10, 0])),
      " 未跨缺失的同期标的仍有效:", bool(np.isfinite(f3b.iloc[10, 1])))
assert np.isnan(f3b.iloc[10, 0]) and np.isfinite(f3b.iloc[10, 1])
dirty = close.copy()
# 真实份额折算的形态是「价格水平一次性永久抬移」（实测 159901 在 2010-11-22 变 5.10 倍
# 之后再不回来），所以整段后移而不是只改一天
dirty.loc[dirty.index[20:], 0] *= 5.1
rd = dirty.pct_change(fill_method=None)
gd = EA.guard_ret(rd)
raw3 = EA.forward_returns(rd, 3)
gd3 = EA.forward_returns(gd, 3)
print("   折算日（第 20 根）单日收益 = %+.3f = +410%%，且此后价格水平不再回来"
      % rd.iloc[20, 0])
print("   含该日的 t=17/18/19 三日前向收益：未过护栏 %+.3f、%+.3f、%+.3f"
      % (raw3.iloc[17, 0], raw3.iloc[18, 0], raw3.iloc[19, 0]))
print("                          过护栏后：   %s、%s、%s"
      % (gd3.iloc[17, 0], gd3.iloc[18, 0], gd3.iloc[19, 0]))
print("   不含该日的 t=20：%+.4f vs 过护栏 %+.4f；t=16：%+.4f vs %+.4f  ← 不误伤"
      % (raw3.iloc[20, 0], gd3.iloc[20, 0], raw3.iloc[16, 0], gd3.iloc[16, 0]))
assert raw3.iloc[17, 0] > 3.0 and raw3.iloc[19, 0] > 3.0
assert all(np.isnan(gd3.iloc[t, 0]) for t in (17, 18, 19))
assert abs(raw3.iloc[20, 0] - gd3.iloc[20, 0]) < 1e-15
assert abs(raw3.iloc[16, 0] - gd3.iloc[16, 0]) < 1e-15
print("   ⇒ 凡窗口内含伪影日的整段作废，不含该日的日子一个数都不变")

# ---------- 3. 分层 ----------
qz = EA.quintile_layers(fac, ret, q=5, min_cs=20)
lay = qz["layers"]
d = lay.index[10]
man = pd.concat([fac.loc[d].rename("f"), ret.loc[d].rename("r")], axis=1).dropna()
man["g"] = np.ceil(man["f"].rank(pct=True) * 5)
for b in (1, 3, 5):
    v = man[man.g == b]["r"].mean()
    print("3) Q%d 手工核对 暴力 %.10f 模块 %.10f" % (b, v, lay.loc[d, f"Q{b}"]))
    assert abs(v - lay.loc[d, f"Q{b}"]) < 1e-12
stable = pd.DataFrame(np.tile(np.arange(len(CODES), dtype=float), (len(DATES), 1)),
                      index=DATES, columns=CODES)
qs = EA.layer_summary(EA.quintile_layers(stable, ret, q=5, min_cs=20))
print("   恒定排名因子高分侧年化换手 %.6f（应≈0）  mono %.3f（随机因子应接近0）"
      % (qs["top_turnover_ann"], qs["mono"]))
assert qs["top_turnover_ann"] < 1e-6
qg = EA.layer_summary(EA.quintile_layers(fac, fac * 0.01, q=5, min_cs=20))
print("   完全预测因子 mono %.3f  Q5-Q1 %+.4f  高分侧超额 %+.4f"
      % (qg["mono"], qg["q_spread_ann"], qg["q_top_excess_ann"]))
assert qg["mono"] > 0.95 and qg["q_spread_ann"] > 0

# ---------- 4. 判重 ----------
f2 = fac + 0.02 * pd.DataFrame(rng.normal(size=fac.shape), index=DATES, columns=CODES)
lag = fac.shift(1)
common = fac.index[2:]
M = {"orig": fac.loc[common].drop(columns=list(fac.columns[35:])),
     "near": f2.loc[common].drop(columns=list(f2.columns[35:])),
     "lag": lag.loc[common].drop(columns=list(lag.columns[35:]))}
P, S = EA.cs_corr_mean(M, min_cs=20, verbose=False)


def brute_pair(a, b, min_cs, rank=False):
    vals = []
    for d in a.index:
        j = pd.concat([a.loc[d].rename("f"), b.loc[d].rename("r")],
                  axis=1).dropna()
        if len(j) < min_cs:
            continue
        x, y = j.iloc[:, 0], j.iloc[:, 1]
        if rank:
            x, y = x.rank(), y.rank()
        if x.std() == 0 or y.std() == 0:
            continue
        vals.append(np.corrcoef(x, y)[0, 1])
    return float(np.mean(vals))


print("4) 判重矩阵:\n", P.round(4).to_string())
print("   orig~near 暴力 %.8f 模块 %.8f" % (brute_pair(M["orig"], M["near"], 20),
                                            P.loc["orig", "near"]))
assert abs(brute_pair(M["orig"], M["near"], 20) - P.loc["orig", "near"]) < 1e-10
assert abs(brute_pair(M["orig"], M["near"], 20, rank=True) - S.loc["orig", "near"]) < 1e-10
print("   orig~lag = %.6f（时间错位→接近 0，说明没有把不同日期混成一个截面）"
      % P.loc["orig", "lag"])
assert abs(P.loc["orig", "lag"]) < 0.2
assert P.loc["orig", "orig"] > 0.999
Px, Sx = EA.cs_corr_mean({"c1": M["near"], "c2": M["lag"]},
                         {"l1": M["orig"], "l2": M["lag"]}, min_cs=20, verbose=False)
print("   交叉块 shape", Px.shape, " c2~l2 = %.6f（同一表达式应=1）" % Px.loc["c2", "l2"])
assert Px.shape == (2, 2) and abs(Px.loc["c2", "l2"] - 1) < 1e-10

# ---------- 5. 合成打分与权重 ----------
wts, detail = EA.icir_weights({"a": {"rank_ic": -0.05, "rank_icir": -0.6, "expr": "x"},
                               "b": {"rank_ic": 0.02, "rank_icir": 0.2, "expr": "y"}},
                              top=5)
print("5) |ICIR| 加权:", {k: round(v, 4) for k, v in wts.items()})
assert abs(wts["a"] + 0.75) < 1e-9 and abs(wts["b"] - 0.25) < 1e-9    # 负 IC 翻向
sc, used = EA.composite_score({"a": fac, "b": f2}, wts)
hand = fac.rank(axis=1, pct=True) * (-0.75) + f2.rank(axis=1, pct=True) * 0.25
print("   合成打分与手算最大差 %.2e" % float((sc - hand).abs().to_numpy()[
    np.isfinite(sc.to_numpy())].max()))
assert float((sc - hand).abs().max().max()) < 1e-12
w2, _d2 = EA.icir_weights({"a": {"rank_ic": 0.001, "rank_icir": 0.02}}, top=5)
print("   |ICIR|<0.05 的噪声不入选:", w2 == {}, "（防止合成一堆噪声）")
assert w2 == {}

# ---------- 6. 组合回放的成本恒等式 ----------
flat = {c: pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                         "volume": 1e6, "amount": 1e8},
                        index=pd.bdate_range("2020-01-01", periods=80))
        for c in CODES}
mf = EA.build_matrices(flat)
score = pd.DataFrame(np.tile(np.arange(len(CODES), dtype=float), (80, 1)),
                     index=mf["close"].index, columns=CODES)
days = mf["close"].index
net, gross, s6 = EA.topk_rebalance(score, mf, days, k=10, hold=10, cost=0.001,
                                   min_listed=0, min_amount=1e6)
print("6) 平值池：毛收益 max %.1e 净收益合计 %+.6f 调仓 %d 次 单边换手 %.3f"
      % (gross.abs().max(), net.sum(), s6["n_rebal"], s6["one_way_turnover"]))
assert gross.abs().max() < 1e-12
assert abs(net.sum() + 2 * 0.001) < 1e-12          # 恒定篮子 → 只有首次建仓有成本
assert s6["basket_size"] == 10 and s6["avg_amount20"] == 1e8
ok, nb = EA.tradable_mask(mf, days[10], None, min_listed=15, min_amount=1e6)
print("   次新闸门: 第 10 日 listed_days=10<15 → 全挡:", bool((~ok).all()))
assert (~ok).all()
ok2, _ = EA.tradable_mask(mf, days[30], None, min_listed=15, min_amount=1e9)
print("   容量闸门: amount20=1e8<1e9 → 全挡:", bool((~ok2).all()))
assert (~ok2).all()
b1, b2 = EA.equal_weight_benchmarks(mf, universe=(mf["listed_days"] >= 15))
print("   基准 ew_all max %.1e / ew_universe max %.1e（平值池两者都应为 0）"
      % (b1.abs().max(), b2.abs().max()))
assert b1.abs().max() == 0.0
# 涨跌停近似闸门：跳空高开过限价必须挡住
up = {c: df.copy() for c, df in flat.items()}
up["510300"].loc[days[31], "open"] = 10.0 * 1.12      # 次日开盘 +12%（超 ±10%）
mu = EA.build_matrices(up)
oku, nbu = EA.tradable_mask(mu, days[30], days[31], min_listed=0, min_amount=1e6)
print("   涨停近似闸门: 次日高开 12%% 的 510300 被挡 = %s（挡掉 %d 只）"
      % (bool(not oku["510300"]), nbu))
assert not oku["510300"] and nbu == 1

# ---------- 7. 数据装载三道筛选 ----------
tmp = os.path.join(ROOT, "etf", "v1", "temp", "_probe_pool")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp)
LONG = pd.bdate_range("2020-01-01", periods=200)
for i, code in enumerate(CODES):
    nn = len(LONG) - (5 if i % 7 else 0)
    out = pd.DataFrame({"open": close.iloc[0, i] * np.cumprod(
                            1 + rng.normal(0, 0.01, nn)),
                        "volume": rng.integers(1e5, 1e7, nn).astype(float)},
                       index=LONG[:nn])
    out["high"] = out["open"] * 1.002
    out["low"] = out["open"] * 0.997
    out["close"] = out["open"] * 1.001
    out["amount"] = out["close"] * out["volume"]
    out.rename_axis("date").to_csv(os.path.join(tmp, f"{code}_daily.csv"),
                                   encoding="utf-8-sig")
pd.DataFrame({"open": 100.0, "high": 100.001, "low": 100.0,
              "close": [100.0 + 1e-7 * j for j in range(200)],
              "volume": 1e6, "amount": 1e8},
             index=LONG).rename_axis("date").to_csv(
    os.path.join(tmp, "888888_daily.csv"), encoding="utf-8-sig")     # 货基型
pd.DataFrame({"date": LONG, "open": 10.0, "high": 10.0, "low": 10.0,
              "close": 10.0, "volume": 1e6}).to_csv(
    os.path.join(tmp, "777777_daily.csv"), encoding="utf-8-sig")     # 缺 amount 列
pd.DataFrame({"date": LONG[:30], "open": 10.0, "high": 10.2, "low": 9.9,
              "close": 10.1, "volume": 1e6,
              "amount": 1e7}).to_csv(os.path.join(tmp, "666666_daily.csv"),
                                     encoding="utf-8-sig")           # 行数不足
pool = EA.load_pool(tmp, min_bars=120, verbose=False)
print("7) 合成池载入 %d 只；货基型/缺列/行数不足均被剔: %s"
      % (len(pool), all(c not in pool for c in ("888888", "777777", "666666"))))
assert len(pool) == len(CODES)
pool2 = EA.load_pool(tmp, min_bars=120, min_ann_vol=0.0, verbose=False)
print("   放开波动闸门 → %d 只（货基型回来，缺列/短序列仍被剔）" % len(pool2))
assert len(pool2) == len(CODES) + 1 and "888888" in pool2
m7 = EA.build_matrices(pool)
th = EA.thickness_report(m7, min_cs=20)
print("   thickness_report:", th)
assert th["days_total"] == len(LONG)
assert set(["ret", "ret_open", "thickness", "listed_days", "amount20",
            "ann_vol20", "fwd5", "fwd10", "fwd20"]) <= set(m7)
w = EA.take_window(m7, start="2020-08-01", end=None)
print("   take_window: %d → %d 行，切完 fwd5 仍非全 NaN: %s"
      % (len(m7["close"]), len(w["close"]), bool(w["fwd5"].notna().any())))
assert 20 < len(w["close"]) < len(m7["close"])
# DSL 求值：全部族候选都要能译
small = {c: pool[c] for c in list(pool)[:4]}
got = EA.evaluate_factors(small, EA.FAMILY_SPECS, verbose=False)
print("8) %d 条族候选在 4 只样本上可求值 %d 条" % (len(EA.FAMILY_SPECS), len(got)))
assert len(got) == len(EA.FAMILY_SPECS), \
    [s["name"] for s in EA.FAMILY_SPECS if EA.spec_name(s) not in got]
lib = EA.load_active_library(verbose=True)
print("9) 在库可求值 active %d 条；样例 %s | %s"
      % (len(lib), lib[0]["name"], lib[0]["expr"][:36]))
assert len(lib) >= 20 and all(r["expr"] for r in lib)
mt0 = os.path.getmtime(EA.FACTOR_LIBRARY_CSV)
EA.load_active_library(verbose=False)
assert os.path.getmtime(EA.FACTOR_LIBRARY_CSV) == mt0
print("   因子库 mtime 未变 ✓（只读）")
lib_specs = EA.specs_from_rows(lib[:5])
got2 = EA.evaluate_factors(small, lib_specs, verbose=False)
print("   在库前 5 条表达式可求值 %d 条" % len(got2))

# ---------- 10. 统计函数 ----------
r = pd.Series(rng.normal(0.0005, 0.01, 500),
              index=pd.bdate_range("2021-01-01", periods=500))
s10 = EA.portfolio_stats(r)
print("10) portfolio_stats:", {k: (round(v, 4) if isinstance(v, float) else v)
                               for k, v in s10.items()})
assert abs(s10["ann_return"] - r.mean() * 252) < 1e-12
assert abs(s10["sharpe"] - s10["ann_return"] / s10["ann_vol"]) < 1e-12
assert s10["max_drawdown"] < 0
ex = EA.excess_stats(r, r * 0.5)
print("    excess:", {k: (round(v, 4) if isinstance(v, float) else v)
                      for k, v in ex.items()})
assert ex["excess_bench_ann"] > 0
yt = EA.yearly_table({"port": r, "bench": r * 0.5})
print("    yearly:\n", yt.round(4).to_string())
assert len(yt) == 3 and list(yt.columns) == ["port", "bench"]
print("\n全部对拍通过 ✅")
