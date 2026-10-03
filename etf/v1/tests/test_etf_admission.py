# -*- coding: utf-8 -*-
"""etf_admission（ETF 因子准入链底座）的离线对拍测试。

本文件不联网、不读 akshare、不写 `data/library/`，全部用小数组 / 临时目录 /
`synth.py` 合成池。测的是最容易悄悄出错的那几件事：

1. 逐日截面相关（秩变换、缺失格、常量日、薄截面跳过）—— 与"照着定义写的暴力版"对拍
2. 前向 h 日收益 + 份额折算护栏（跨伪影日的整段作废，不跨的一个数都不变）
3. 分层归组、单调性、高分侧换手
4. 判重的 complete-case 逐日两两相关（含时间错位必须接近 0）
5. 组合回放的成本恒等式（平值池上净收益 = -首次建仓手续费）与五道可交易闸门
6. 池子装载的三道筛选是否真按 docstring 执行
"""

import os

import numpy as np
import pandas as pd
import pytest

import etf_admission as EA
from synth import make_daily_pool

V1_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

rng = np.random.default_rng(11)
DATES = pd.bdate_range("2020-01-01", periods=90)
CODES = [f"5103{i:02d}" for i in range(40)]


def _panels():
    """一张带缺失/薄截面/常量陷阱的小截面面板（因子 40 只 × 90 日）。"""
    fac = pd.DataFrame(rng.normal(size=(len(DATES), len(CODES))),
                       index=DATES, columns=CODES)
    fwd = fac * 0.3 + pd.DataFrame(rng.normal(size=fac.shape),
                                   index=DATES, columns=CODES)
    fac.iloc[5, 3] = np.nan
    fwd.iloc[5, 4] = np.nan
    fac.iloc[70:, 35:] = np.nan       # 后段一批标的退市 → 截面变薄
    fac.iloc[0, :] = np.nan
    fac.iloc[1, 6:] = np.nan           # 第 2 日只剩 6 只有值 → 应被 MIN_CS 丢弃
    return fac, fwd


def _close():
    p = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, (len(DATES), len(CODES))), axis=0)
    return pd.DataFrame(p, index=DATES, columns=CODES)


def brute_ic(a, b, min_cs, rank=False):
    """暴力版逐日截面 IC：concat→dropna→corr，完全不共用模块代码。"""
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


# ---------- 1. 逐日截面 IC ----------

def test_daily_cs_ic_matches_bruteforce():
    """IC_t = corr_i(rank(F_{t,i}), rank(r_{t,i}))，向量化版必须等于暴力版。"""
    fac, fwd = _panels()
    p, s, n = EA.daily_cs_ic(fac, fwd, min_cs=20)
    bp, bs = brute_ic(fac, fwd, 20), brute_ic(fac, fwd, 20, rank=True)
    assert (p.dropna() - bp).abs().max() < 1e-10
    assert (s.dropna() - bs).abs().max() < 1e-10
    assert int(p.notna().sum()) == len(bp)
    # 薄截面日：只剩 6 只 → 整日丢弃（记 NaN，不是记 0）
    assert pd.isna(s.iloc[1]) and n.iloc[1] == 6


def test_daily_cs_ic_edge_cases():
    fac, fwd = _panels()
    # 完美预测（因子自身当标签）→ RankIC ≡ 1
    assert abs(EA.daily_cs_ic(fac, fac.rank(axis=1), min_cs=20)[1].mean() - 1) < 1e-12
    # 常量日：corr 无定义，必须给 NaN 而不是 0（记 0 等于把"没法判"当"预测失败"）
    const = fac.copy()
    const.iloc[:] = 1.0
    assert EA.daily_cs_ic(const, fwd, min_cs=20)[1].isna().all()


def test_ic_summary_counts():
    fac, fwd = _panels()
    _p, s, n = EA.daily_cs_ic(fac, fwd, min_cs=20)
    st = EA.ic_summary(s, n, min_cs=20)
    assert st["days"] == len(brute_ic(fac, fwd, 20, rank=True))
    assert st["days_thin"] == int((n < 20).sum())
    assert abs(st["icir"] - s.dropna().mean() / s.dropna().std()) < 1e-6
    # ICIR 与 t 值的关系：t = ICIR × √T（同一条 mean/std，只差样本数）
    assert abs(st["t"] - st["icir"] * np.sqrt(st["days"])) < 1e-3
    assert 0.0 <= st["win"] <= 1.0
    assert EA.ic_summary(pd.Series(dtype="float64"))["days"] == 0


# ---------- 2. 前向收益与份额折算护栏 ----------

def test_forward_returns_definition():
    """r_{t→t+h} = Π(1+r_{t+k})-1，必须等于价格比 close_{t+h}/close_t - 1。"""
    close = _close()
    ret = close.pct_change(fill_method=None)
    f3 = EA.forward_returns(ret, 3)
    assert float((f3.iloc[10] - (close.iloc[13] / close.iloc[10] - 1))
                 .abs().max()) < 1e-12
    # 窗口内含缺失日 → 整段作废（不做部分累乘）
    r2 = ret.copy()
    r2.iloc[11, 0] = np.nan
    f3b = EA.forward_returns(r2, 3)
    assert np.isnan(f3b.iloc[10, 0]) and np.isfinite(f3b.iloc[10, 1])


def test_guard_ret_kills_share_conversion():
    """份额折算 = 价格水平一次性永久抬移（实测 159901 在 2010-11-22 变 5.10 倍）。

    护栏必须让**窗口内含该日**的前向收益全部作废，而**不含该日**的日子一个数都不变。
    """
    close = _close()
    dirty = close.copy()
    dirty.iloc[20:, 0] *= 5.1
    rd = dirty.pct_change(fill_method=None)
    assert rd.iloc[20, 0] > 3.0                      # +410% 的假收益
    assert np.isnan(EA.guard_ret(rd).iloc[20, 0])     # 本线是置 NaN，不是 clip
    raw3 = EA.forward_returns(rd, 3)
    gd3 = EA.forward_returns(EA.guard_ret(rd), 3)
    assert raw3.iloc[17, 0] > 3.0 and raw3.iloc[19, 0] > 3.0
    assert all(np.isnan(gd3.iloc[t, 0]) for t in (17, 18, 19))
    assert abs(raw3.iloc[20, 0] - gd3.iloc[20, 0]) < 1e-15   # 折算日之后不再回来
    assert abs(raw3.iloc[16, 0] - gd3.iloc[16, 0]) < 1e-15   # 窗口不触及 → 不误伤


# ---------- 3. 分层 ----------

def test_quintile_layer_assignment():
    """q_{i,t} = ceil(Q·rank_pct) 的层均值必须等于手工分组。"""
    fac, _fwd = _panels()
    ret = _close().pct_change(fill_method=None)
    lay = EA.quintile_layers(fac, ret, q=5, min_cs=20)["layers"]
    d = lay.index[10]
    man = pd.concat([fac.loc[d].rename("f"), ret.loc[d].rename("r")],
                    axis=1).dropna()
    man["g"] = np.ceil(man["f"].rank(pct=True) * 5)
    for b in (1, 3, 5):
        assert abs(man[man.g == b]["r"].mean() - lay.loc[d, f"Q{b}"]) < 1e-12


def test_layer_summary_properties():
    ret = _close().pct_change(fill_method=None)
    fac, _ = _panels()
    # 排名恒定 → 高分侧换手为 0（层成员从不换人）
    stable = pd.DataFrame(np.tile(np.arange(len(CODES), dtype=float),
                                  (len(DATES), 1)), index=DATES, columns=CODES)
    qs = EA.layer_summary(EA.quintile_layers(stable, ret, q=5, min_cs=20))
    assert qs["top_turnover_ann"] < 1e-6
    # 完全预测（r = 0.01·F）→ 单调性 ~1、高分侧跑赢自身均值
    qg = EA.layer_summary(EA.quintile_layers(fac, fac * 0.01, q=5, min_cs=20))
    assert qg["mono"] > 0.95 and qg["q_spread_ann"] > 0
    assert abs(qg["q_top_excess_ann"] - (qg["q_ann"][-1] - np.mean(qg["q_ann"]))) < 1e-12


# ---------- 4. 判重口径 ----------

def test_cs_corr_mean_matches_bruteforce():
    """判重主口径 = 逐日截面相关的时间平均，必须等于逐日暴力版。

    这里刻意用**无缺失**的干净块：模块按"当日全部候选齐备"取 complete-case
    （任一条因子在 (日,标的) 缺失则该标的当日不参与、对**所有**两两对生效），
    暴力版按"这一对两侧齐备"取 —— 有缺失时两者会差在个别格子上（模块口径更严，
    且严得有道理：一张表里所有两两对必须在同一个截面上比）。complete-case 那条
    性质单独用 test_cs_corr_mean_is_complete_case 钉住。
    """
    clean = pd.DataFrame(rng.normal(size=(60, 30)),
                         index=pd.bdate_range("2021-01-04", periods=60),
                         columns=[f"5104{i:02d}" for i in range(30)])
    near = clean + 0.02 * pd.DataFrame(rng.normal(size=clean.shape),
                                       index=clean.index, columns=clean.columns)
    lag = clean.shift(1).dropna()
    near = near.loc[lag.index]
    M = {"orig": clean.loc[lag.index], "near": near, "lag": lag}
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

    assert abs(brute_pair(M["orig"], M["near"], 20) - P.loc["orig", "near"]) < 1e-10
    assert abs(brute_pair(M["orig"], M["near"], 20, True) - S.loc["orig", "near"]) < 1e-10
    # 自我判重 = 1；时间错位 → 接近 0（证明没有把不同日期混进同一个截面）
    assert P.loc["orig", "orig"] > 0.999
    assert abs(P.loc["orig", "lag"]) < 0.2
    assert abs(S.loc["orig", "near"] - P.loc["orig", "near"]) < 0.02   # 无离群值时两口径贴合
    # 交叉块：candidates × library 形状与恒等
    Px, _Sx = EA.cs_corr_mean({"c1": M["near"], "c2": M["lag"]},
                              {"l1": M["orig"], "l2": M["lag"]},
                              min_cs=20, verbose=False)
    assert Px.shape == (2, 2) and abs(Px.loc["c2", "l2"] - 1) < 1e-10
    # 不对称的两集（候选 2 条 × 在库 3 条）才是生产里的形状（实测 31×21）。
    # 只测对称形状放过过一个真崩掉的 bug：秩相关那步的 mask 曾按 xa 的形状生成，
    # 喂给行数不同的 xb 时直接广播失败（cs_corr_mean 里 mask 各按各的形状给）。
    Py, Sy = EA.cs_corr_mean({"c1": M["near"], "c2": M["lag"]},
                             {"l1": M["orig"], "l2": M["lag"],
                              "l3": M["near"]},
                             min_cs=20, verbose=False)
    assert Py.shape == (2, 3) and Sy.shape == (2, 3)
    assert abs(Sy.loc["c1", "l3"] - 1) < 1e-10                       # 自我判重=1
    assert abs(Sy.loc["c1", "l1"] - S.loc["near", "orig"]) < 1e-10   # 与对称版同值


def test_cs_corr_mean_is_complete_case():
    """判重用的当日截面 = **全部候选齐备**的交集，不是各对自己的并集。

    这张性质只有三条以上候选同时参与时才显现：a~b 这一对在第 10 天会因为
    第三条候选 c 缺失而被少算一只。用两种暴力口径夹出模块实际用的是哪一种。
    """
    cols = [f"5105{i:02d}" for i in range(25)]
    idx = pd.bdate_range("2021-01-04", periods=40)
    a = pd.DataFrame(rng.normal(size=(40, 25)), index=idx, columns=cols)
    b = a + 0.10 * pd.DataFrame(rng.normal(size=(40, 25)), index=idx, columns=cols)
    c = a + 0.20 * pd.DataFrame(rng.normal(size=(40, 25)), index=idx, columns=cols)
    c.iloc[10, 0] = np.nan               # 只有 c 在这一格缺

    def brute(x, y, skip_cell):
        vals = []
        for d in x.index:
            s = y.loc[d]
            if skip_cell:
                s = s[x.loc[d].notna() & c.loc[d].notna()]
            else:
                s = s[x.loc[d].notna()]
            j = pd.concat([x.loc[d].reindex(s.index).rename("f"), s.rename("r")],
                          axis=1).dropna()
            vals.append(np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1])
        return float(np.mean(vals))

    P, S = EA.cs_corr_mean({"a": a, "b": b, "c": c}, min_cs=20, verbose=False)
    # 模块 = 交集口径（当日 24 只），而不是 a~b 自己的成对口径（当日 25 只）
    assert abs(P.loc["a", "b"] - brute(a, b, True)) < 1e-12
    assert abs(P.loc["a", "b"] - brute(a, b, False)) > 1e-12
    assert P.shape == (3, 3) and abs(P.loc["a", "a"] - 1) < 1e-12
    assert abs(P.loc["a", "b"] - P.loc["b", "a"]) < 1e-12      # 对称
    assert S.loc["a", "c"] > P.loc["a", "c"] - 0.1             # 秩口径同向


# ---------- 5. 合成打分 ----------

def test_icir_weights_flip_sign_and_drop_noise():
    """w_j = |ICIR_j|/Σ|ICIR| 且负 IC 翻向；|ICIR| 低于下限的噪声不入选。"""
    wts, _d = EA.icir_weights({"a": {"rank_ic": -0.05, "rank_icir": -0.6, "expr": "x"},
                               "b": {"rank_ic": 0.02, "rank_icir": 0.2, "expr": "y"}},
                              top=5)
    assert abs(wts["a"] + 0.75) < 1e-9 and abs(wts["b"] - 0.25) < 1e-9
    assert EA.icir_weights({"a": {"rank_ic": 0.001, "rank_icir": 0.02}}, top=5)[0] == {}


def test_composite_score_matches_manual_weighting():
    fac, _ = _panels()
    near = fac + 0.02 * pd.DataFrame(rng.normal(size=fac.shape),
                                     index=DATES, columns=CODES)
    wts = {"a": -0.75, "b": 0.25}
    sc, used = EA.composite_score({"a": fac, "b": near}, wts)
    hand = (fac.rank(axis=1, pct=True) * (-0.75)
            + near.rank(axis=1, pct=True) * 0.25)
    assert set(used) == {"a", "b"}
    assert float((sc - hand).abs().max().max()) < 1e-12


# ---------- 6. 组合回放与闸门 ----------

@pytest.fixture(scope="module")
def flat_pool():
    """平值池：OHLC 恒定 → 毛收益必为 0，任何非 0 都只能来自成本或闸门。"""
    idx = pd.bdate_range("2020-01-01", periods=80)
    return {c: pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0,
                             "close": 10.0, "volume": 1e6, "amount": 1e8},
                            index=idx) for c in CODES}


def test_topk_cost_identity(flat_pool):
    m = EA.build_matrices(flat_pool)
    days = m["close"].index
    score = pd.DataFrame(np.tile(np.arange(len(CODES), dtype=float), (80, 1)),
                         index=days, columns=CODES)
    net, gross, s = EA.topk_rebalance(score, m, days, k=10, hold=10, cost=0.001,
                                      min_listed=0, min_amount=1e6)
    assert gross.abs().max() < 1e-12                  # 平值 → 毛收益恒 0
    # 恒定篮子：只有首次建仓付一次双边成本 2c×1
    assert abs(net.sum() + 2 * 0.001) < 1e-12
    assert s["basket_size"] == 10 and s["avg_amount20"] == 1e8
    # 调仓日 = range(0, 80-10-1, 10) = 0,10,…,60 共 7 次；φ 逐次 1,0,…,0（恒定
    # 篮子只有首次建仓付一次双边成本）→ 均值 1/7。
    # 这里曾经钉的是 5 次、均值 0.2：那不是回放算出来的，是 amount20 用
    # rolling(20) 默认 min_periods=20 时头 19 天的 NaN 把前两次调仓**偷偷**挡掉了
    # （调用方明明传了 min_listed=0）。闸门只该由显式参数决定，见 build_matrices。
    assert s["n_rebal"] == 7
    assert s["one_way_turnover"] == pytest.approx(1 / 7)


def test_topk_handles_ragged_pool():
    """池子在长：晚上市标的留下的缺失开盘价绝不能把整个组合日变成 NaN。

    NaN 会被 portfolio_stats 的 dropna 悄悄吃掉，看起来像"少了几天数据"，
    实际是"每天都算不出来"。
    """
    codes = [f"5106{i:02d}" for i in range(30)]
    idx = pd.bdate_range("2020-01-01", periods=120)
    pool = {}
    for j, c in enumerate(codes):
        df = pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                           "volume": 1e6, "amount": 1e8}, index=idx)
        df.iloc[:j * 3] = np.nan            # 每三只晚一批上市
        pool[c] = df
    pool["510600"].iloc[60:70] = np.nan     # 分数最高那只中途断档 10 天
    m = EA.build_matrices(pool)
    days = m["close"].index
    score = pd.DataFrame(np.tile(np.arange(len(codes) - 1, -1, -1, dtype=float),
                                 (len(days), 1)), index=days, columns=codes)
    net, gross, s = EA.topk_rebalance(score, m, days, k=10, hold=10, cost=0.001,
                                      min_listed=0, min_amount=1e6)
    assert gross.notna().all() and net.notna().all()
    assert gross.abs().max() < 1e-12                      # 平值池：毛收益仍为 0
    assert s["n_missing_in_basket"] > 0                   # 断档确实被数出来了
    # 未上市的格子不能进篮子（可交易闸门第 1 道：当日有 close）。
    # 第 5 根 K 线上只有 j*3<=5 的前两批（j=0,1）共 2 只已有行情。
    ok, _n = EA.tradable_mask(m, days[5], None, min_listed=0, min_amount=1e6)
    assert int(ok.sum()) == 2


def test_tradable_gates(flat_pool):
    m = EA.build_matrices(flat_pool)
    days = m["close"].index
    # 次新闸门：第 10 日 listed_days=10 < 15 → 全挡
    ok, _n = EA.tradable_mask(m, days[10], None, min_listed=15, min_amount=1e6)
    assert (~ok).all()
    # 容量闸门：amount20=1e8 < 1e9 → 全挡
    ok2, _ = EA.tradable_mask(m, days[30], None, min_listed=15, min_amount=1e9)
    assert (~ok2).all()
    # 涨停近似闸门：次日高开 12% 的普通 ETF（±10% 段）必须买不进
    up = {c: df.copy() for c, df in flat_pool.items()}
    up["510300"].loc[days[31], "open"] = 10.0 * 1.12
    mu = EA.build_matrices(up)
    oku, nb = EA.tradable_mask(mu, days[30], days[31], min_listed=0, min_amount=1e6)
    assert not oku["510300"] and nb == 1
    # 同一涨幅放在科创板段（±20%）就不该被挡
    up2 = {c: df.copy() for c, df in flat_pool.items()}
    up2["588000"] = up2.pop("510300")
    mu2 = EA.build_matrices(up2)
    oku2, nb2 = EA.tradable_mask(mu2, days[30], days[31], min_listed=0,
                                 min_amount=1e6)
    assert oku2["588000"] and nb2 == 0
    assert EA.limit_of("588080") > EA.limit_of("510300")


def test_equal_weight_benchmarks(flat_pool):
    m = EA.build_matrices(flat_pool)
    b1, b2 = EA.equal_weight_benchmarks(m, universe=(m["listed_days"] >= 15))
    assert b1.abs().max() == 0.0 and b2.abs().max() == 0.0
    assert b1.name == "ew_all" and b2.name == "ew_universe"


# ---------- 7. 池子装载 ----------

@pytest.fixture(scope="module")
def csv_universe(tmp_path_factory):
    """写出真 CSV 目录：40 只正常 + 货基型 + 缺 amount 列 + 行数不足。"""
    d = tmp_path_factory.mktemp("universe_probe")
    long = pd.bdate_range("2020-01-01", periods=200)
    close = _close()
    r2 = np.random.default_rng(5)
    for i, code in enumerate(CODES):
        nn = len(long) - (5 if i % 7 else 0)
        out = pd.DataFrame({"open": close.iloc[0, i] * np.cumprod(
                                1 + r2.normal(0, 0.01, nn)),
                            "volume": r2.integers(1e5, 1e7, nn).astype(float)},
                           index=long[:nn])
        out["high"] = out["open"] * 1.002
        out["low"] = out["open"] * 0.997
        out["close"] = out["open"] * 1.001
        out["amount"] = out["close"] * out["volume"]
        out.rename_axis("date").to_csv(os.path.join(str(d), f"{code}_daily.csv"),
                                       encoding="utf-8-sig")     # 表头带 BOM（真实数据就是这样）
    pd.DataFrame({"open": 100.0, "high": 100.001, "low": 100.0,
                  "close": [100.0 + 1e-7 * j for j in range(200)],
                  "volume": 1e6, "amount": 1e8},
                 index=long).rename_axis("date").to_csv(
        os.path.join(str(d), "888888_daily.csv"), encoding="utf-8-sig")   # 货基型
    pd.DataFrame({"date": long, "open": 10.0, "high": 10.0, "low": 10.0,
                  "close": 10.0, "volume": 1e6}).to_csv(
        os.path.join(str(d), "777777_daily.csv"), encoding="utf-8-sig")   # 缺 amount
    pd.DataFrame({"date": long[:30], "open": 10.0, "high": 10.2, "low": 9.9,
                  "close": 10.1, "volume": 1e6, "amount": 1e7}).to_csv(
        os.path.join(str(d), "666666_daily.csv"), encoding="utf-8-sig")   # 太短
    return str(d)


def test_load_pool_three_filters(csv_universe):
    pool = EA.load_pool(csv_universe, min_bars=120, verbose=False)
    assert len(pool) == len(CODES)
    assert all(c not in pool for c in ("888888", "777777", "666666"))
    # 放开波动闸门 → 只有货基型回来（缺列/短序列仍被剔），证明三道筛选彼此独立
    pool2 = EA.load_pool(csv_universe, min_bars=120, min_ann_vol=0.0, verbose=False)
    assert len(pool2) == len(CODES) + 1 and "888888" in pool2


def test_matrices_and_window(csv_universe):
    pool = EA.load_pool(csv_universe, min_bars=120, verbose=False)
    m = EA.build_matrices(pool)
    assert {"ret", "ret_open", "thickness", "listed_days", "amount20",
            "ann_vol20", "fwd5", "fwd10", "fwd20"} <= set(m)
    assert m["ret"].shape == m["close"].shape
    th = EA.thickness_report(m, min_cs=20)
    assert th["days_total"] == len(pool[CODES[0]]) and th["cs_max"] == len(CODES)
    w = EA.take_window(m, start="2020-08-01", end=None)
    assert 20 < len(w["close"]) < len(m["close"])
    assert w["fwd5"].notna().to_numpy().any()     # 切窗口不能把标签切干净
    # listed_days 是累计计数：末行 = 该标的的 K 线根数
    assert (m["listed_days"].iloc[-1] == m["close"].notna().sum()).all()


# ---------- 8. 候选表达式全部可进 DSL ----------

def test_every_family_spec_is_evaluable(csv_universe):
    """FAMILY_SPECS 里每条表达式都必须能在主线 DSL 里算出来。

    进不了 DSL 的表达式不会报错、只会静悄悄少一列 ⇒ 这里显式对账。
    """
    pool = EA.load_pool(csv_universe, min_bars=120, verbose=False)
    small = {c: pool[c] for c in sorted(pool)[:4]}
    got = EA.evaluate_factors(small, EA.FAMILY_SPECS, verbose=False)
    missing = [EA.spec_name(s) for s in EA.FAMILY_SPECS if EA.spec_name(s) not in got]
    assert not missing, missing
    assert len(got) == len(EA.FAMILY_SPECS)
    assert all(name and fam for name, fam in
               ((EA.spec_name(s), s.get("family")) for s in EA.FAMILY_SPECS))
    assert EA.family_specs("动量,波动") and len(EA.family_specs()) == len(EA.FAMILY_SPECS)


def test_evaluate_factors_on_synth_pool():
    """synth 合成池没有 amount 列，补上后走同一条装载 → 求值路径。"""
    pool = make_daily_pool(n_codes=4, n_bars=300, seed=13,
                           codes=["510301", "510302", "510303", "510304"])
    for df in pool.values():
        df["amount"] = df["close"] * df["volume"]
    specs = EA.family_specs("动量,反转")
    facs = EA.evaluate_factors(pool, specs, verbose=False)
    assert len(facs) == len(specs)
    mom = facs["动量·20d"]
    assert mom.shape == (300, 4)
    # 动量·20d = close/delay(close,20)-1，与手算同式
    hand = pool["510301"]["close"] / pool["510301"]["close"].shift(20) - 1
    assert float((mom["510301"] - hand).abs().max()) < 1e-12
    m = EA.build_matrices(pool)
    # 前向收益与因子同形状，才能逐日配对
    assert m["fwd10"].shape == mom.shape
    ic_p, ic_s, n = EA.daily_cs_ic(mom, m["fwd10"], min_cs=3)
    assert len(ic_s) == 300 and int(n.iloc[-30]) == 4
    # 未上市的头部（20 日动量的暖机期）因子为 NaN → 不进当日截面
    assert mom.iloc[:20].isna().all().all()


# ---------- 9. 因子库只读 ----------

def test_library_is_read_only_and_translatable():
    path = os.path.join(V1_ROOT, "data", "library", "factor_library.csv")
    if not os.path.exists(path):
        pytest.skip("本环境无因子库文件")
    st0 = os.stat(path)
    rows = EA.load_active_library(path, verbose=False)
    assert len(rows) >= 20 and all(r["expr"] for r in rows)
    assert all("name" in r and "source" in r for r in rows)
    specs = EA.specs_from_rows(rows[:6])
    assert len(specs) == 6 and specs[0]["family"] == "在库"
    assert os.stat(path).st_mtime == st0.st_mtime     # 只读：mtime 不许动


# ---------- 10. 统计函数 ----------

def test_portfolio_stats_and_excess():
    r = pd.Series(rng.normal(0.0005, 0.01, 500),
                  index=pd.bdate_range("2021-01-01", periods=500))
    s = EA.portfolio_stats(r)
    assert abs(s["ann_return"] - r.mean() * 252) < 1e-12
    assert abs(s["sharpe"] - s["ann_return"] / s["ann_vol"]) < 1e-12
    assert s["max_drawdown"] < 0
    ex = EA.excess_stats(r, r * 0.5)
    assert ex["excess_bench_ann"] > 0
    yt = EA.yearly_table({"port": r, "bench": r * 0.5})
    assert len(yt) == len(set(r.index.year)) and list(yt.columns) == ["port", "bench"]
    # 分年表与全区间独立复利：把三年连乘 ≈ 全区间累计
    cum = float((1 + r).prod() - 1)
    assert abs(np.prod([1 + v for v in yt["port"]]) - (1 + cum)) < 1e-9
    assert EA.portfolio_stats(pd.Series([0.01]))["n_days"] == 1


def test_run_params_exposed():
    p = EA.run_params()
    assert p["min_cs"] >= 2 and p["horizons"] and p["top_k"]


def test_candidate_run_never_touches_canonical_artifacts(monkeypatch):
    """`ETF_SPEC_JSON` 非空 ⇒ 环 1/环 3 的产物一律加 `.cand` 中缀，权威 csv 不动。

        护栏的判据：环 1 那张表是环 2/环 3 的**输入**（环 2 从它选因子、环 3 从它取
        RankICIR），一次候选评估写进同名文件就把「31 条族候选」的口径换成
        「候选 + 外部」。`ETF_RESULTS_DIR` 环境变量挡不住（RESULTS_DIR 由 DATA_DIR 推），
        09-24 真被覆盖过一轮，只能整环重跑复原。
    """
    import run_etf_factor_eval as R1
    import run_etf_redundancy_check as R3
    for mod in (R1, R3):
        monkeypatch.setattr(mod, "SPEC_JSON", "")
        assert mod.route("/d/results/etf_factor_eval.csv") == "/d/results/etf_factor_eval.csv"
        monkeypatch.setattr(mod, "SPEC_JSON", "cand_list.json")
        got = mod.route("/d/results/etf_factor_eval.csv")
        assert got == "/d/results/etf_factor_eval.cand.csv"
        # 中缀在扩展名之前：基名里已有的 `_cand`（候选×候选矩阵）不能被当成扩展名截掉
        m = mod.route("/d/results/etf_redundancy_matrix_cand.csv")
        assert m == "/d/results/etf_redundancy_matrix_cand.cand.csv"


def test_redundancy_twin_is_chosen_by_abs_value_and_skips_itself():
    """环 3 挑近亲的两条规矩（09-28 修）：判决看 **|corr|**，且对侧按**名字**剔掉自己。

    为什么必须看绝对值：两条因子逐日截面排序完全相反 = 同一个信息乘了 -1，那还是重复；
    生产侧真会执行的那道判重（`common/src/core/multi_source_mining.py:112`）认的就是
    `abs(spearman)`。旧实现取**带符号最大值**：09-24 那份归档把 `波动·STD20` 的近亲读成
    `gp_0` / +0.7134 ⇒"危险区"，而它与库内 `volatility_20` 恰好 -1.0000；`价量·Amihud20`
    对 `量能·成交额MA20` = -0.9622，旧读数的**候选内部**判决是"可提名（<0.70）"
    ⇒ 放进来一条只是换了个方向的重复因子。

    为什么按名字剔自己：带 `ETF_SPEC_JSON` 跑时候选常常就是从库里挑出来的几条，
    不剔的话"自己和自己的截面相关"恰好 1.0000，那一行读数不含信息还挤掉真近亲。
    ⚠️ 只按名字剔 —— 表达式相同、名字不同的对侧照算（那正是"以为挖到新的、其实库里有"）。
    """
    import run_etf_redundancy_check as R3

    row = pd.Series({"自己": 1.0, "远亲": 0.20, "反号近亲": -0.98, "全NaN": np.nan})
    nm, signed, abs_v = R3.abs_worst(row, exclude=["自己"])
    assert (nm, signed, abs_v) == ("反号近亲", -0.98, 0.98)
    # 负对照：旧规则（带号 idxmax）在同一行上挑出来的是"远亲"，两条规矩确实不等价
    assert row.drop(index=["自己"]).dropna().idxmax() == "远亲"
    # 带符号原值必须留得住（"和谁反号"是信息），判决按绝对值落档
    assert signed < 0 and abs_v == abs(signed)
    assert R3.verdict(abs_v).startswith("会被判重复")              # 0.85 <= 0.98 < 0.99
    assert R3.verdict(R3.abs_worst(pd.Series({"x": -0.995}))[2]).startswith("必被丢")
    # 剔除只按传入的名字：没点名的列（哪怕值很小）仍然参与比较
    assert R3.abs_worst(row, exclude=["自己", "全NaN"])[0] == "反号近亲"
    # 整行无可对侧 ⇒ (None, nan, nan)，判决说"无法判定"而不是"可提名"
    only_self = R3.abs_worst(row, exclude=["反号近亲", "远亲", "全NaN", "自己"])
    assert only_self[0] is None and np.isnan(only_self[2])
    assert R3.verdict(only_self[2]).startswith("无法判定")


def test_library_internal_reports_same_expression_pairs_and_cluster_count():
    """在库×在库那道对角线（09-28 丁方案）：只加读数，但读数本身要能被反证。

    夹具是四张手摆的相关表：A-B 反号 **-1.0000**（同一个信息乘了 -1，就是库里
    `volatility_20` vs `vol_20` 那种）、A-C=0.78（危险区，本线从没说它算同一条）、
    B-D=0.30（干净）、对角 A-A=1.0（自己，不许出现在上三角里）。
    三条硬要求：
    1. 反号那对必须列出来 —— 负对照：带符号口径挑近亲时 A 行最大值是 A-A=1.0 或
       A-C=0.78，`A-B` 会被读成"最不像的那个"，这对就永远看不见。
    2. `建议留` 用库自带 |ic|（那批条目没有环 1 的截面 RankICIR）：A(|ic|=0.016) 对
       B(|ic|=0.033) ⇒ 留 B。
    3. 唯一簇数只并 `|corr| >= RED_BAR(0.85)` 的对 ⇒ 0.78 那对列出来但**不并簇**，
       四节点变三簇。并早了就是把"人工复核"偷偷升成判据。
    """
    import run_etf_redundancy_check as R3

    names = ["A", "B", "C", "D"]
    LS = pd.DataFrame({"A": [1.0, -1.0, 0.78, 0.10],
                       "B": [-1.0, 1.0, 0.20, 0.30],
                       "C": [0.78, 0.20, 1.0, 0.05],
                       "D": [0.10, 0.30, 0.05, 1.0]}, index=names)
    LP = LS * 0.9                                        # Pearson 只是陪列
    det, n_unique = R3.library_internal(LS, LP, names, {"A": 0.016, "B": 0.033,
                                                        "C": 0.01, "D": 0.01})
    assert list(det["侧1"]) == ["A", "A"]                # 按 |corr| 降序：1.0 再 0.78
    assert (det["侧2"].iloc[0], det["rank_corr"].iloc[0], det["abs_rank_corr"].iloc[0]) \
        == ("B", -1.0, 1.0)                              # 带符号原值留得住
    assert det["建议留"].iloc[0] == "B" and det["剔的|ic|"].iloc[0] == 0.016
    assert det["判决"].iloc[0].startswith("必被丢")
    assert det["判决"].iloc[1].startswith("危险区")
    assert "A" not in list(det["侧2"])                   # 对角（自己=1.0）不许成对
    assert n_unique == 3                                 # 只并了 A-B，A-C 那对没并
    # 反证这条线确实在管：并簇用的是 RED_BAR(0.85) 而不是 NEAR_DUP(0.70)，
    # 0.78 那对若被并进去簇数会变 2 ⇒ "人工复核"被偷偷升成判据
    assert EA.NEAR_DUP < EA.RED_BAR and EA.NEAR_DUP <= 0.78 < EA.RED_BAR
    # 空表（库里只有一条可译因子时不该抛异常，也不该丢判决表结构）
    one, n1 = R3.library_internal(LS.loc[["A"], ["A"]], LP.loc[["A"], ["A"]], ["A"],
                                  {"A": 0.016})
    assert n1 == 1 and list(one.columns) == list(det.columns)


def test_library_counts_separates_blank_expr_from_eval_failure(tmp_path, monkeypatch):
    """「库里到底几条」必须分三口径数，不能拿 load_active_library 的长度当库大小。

    夹具是一份 4 行的库导出：A/B 是 active 且可译、C 是 active 但 `expr` 空（真实身份
    = 内置注册表因子 `ma_ratio_10_30`）、D 是 inactive。load_active_library 会**在内部**
    把 C 和 D 都滤掉，只返回 2 条 —— 早先那行汇总用 `len(lib) - len(facs_l)` 当"无 expr
    的条数"，于是 C 被读成不存在，盲区读成 0 条。反证就在这：C 存在时该数必须是 1，
    而那个旧算式给的是 0。
    """
    import run_etf_redundancy_check as R3

    csv = tmp_path / "factor_library.csv"
    pd.DataFrame([
        {"name": "A", "expr": "close / delay(close, 5.0) - 1", "status": "active"},
        {"name": "B", "expr": "ts_std(ret, 20.0)", "status": "active"},
        {"name": "C", "expr": "", "status": "active"},          # 内置因子，判重看不见
        {"name": "D", "expr": "ts_mean(volume, 20.0)", "status": "inactive"},
    ]).to_csv(csv, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(EA, "FACTOR_LIBRARY_CSV", str(csv))

    # A/B/C 三条 active；只有 A/B 进得了判重，本场 A 求值失败 ⇒ facs_l 只剩 B
    assert R3.library_counts(1) == (3, 1, 1)
    # 负对照：旧算式（可译数 - 已成表数）会把「无 expr」读成 1 条、又漏掉真盲区那条
    n_active, n_blank, n_broken = R3.library_counts(2)
    assert (n_active, n_blank, n_broken) == (3, 1, 0)
    assert n_blank != n_broken                     # 两件事不是一件事，不许混报
    # 表不存在时不许抛异常：按「全部可译」口径回落
    monkeypatch.setattr(EA, "FACTOR_LIBRARY_CSV", str(tmp_path / "nope.csv"))
    assert R3.library_counts(5) == (5, 0, 0)


# ---------- 11. 成交额分档滑点 与 连续低量闸门（#14 落地的两道代理判据） ----------

def test_slippage_of_is_a_tier_table_not_a_fit():
    """档位边界逐档对拍；NaN 必须落最贵档。

        slip(>=10 亿)=3bp / >=3 亿=5bp / >=1 亿=8bp / >=3000 万=15bp / 其余=30bp
    NaN 那一条是本判据的保守方向：算不出容量 = 不知道多难成交，绝不能给它 6bp 的幻觉。
    """
    for amt, want in [(2e9, 0.0003), (1e9, 0.0003), (9.9e8, 0.0005), (3e8, 0.0005),
                      (2.9e8, 0.0008), (1e8, 0.0008), (9e7, 0.0015), (3e7, 0.0015),
                      (2.9e7, 0.0030), (0.0, 0.0030)]:
        assert EA.slippage_of(amt) == pytest.approx(want), amt
    assert EA.slippage_of(np.nan) == pytest.approx(0.0030)


def _streak_pool(dead_tail=True, n=60):
    """造三只"20 日均额过关、但最近没量"的标的：闸门 3 放它们、闸门 4 必须挡。

        510300 全程 5 亿                          —— 对照，必须放行
        510310 前 50 日 5 亿、后 10 日 1 万        —— 天天没人交易（强/弱读法都该挡）
        510320 后 10 日只有**今天**放了 5 亿        —— 偶尔放量（只有强读法挡得住）

    三只的 amount20 在最后一日都 ≈ 2.5 亿 >> 3 千万，均值全被月初那批日子撑起来。
    """
    idx = pd.bdate_range("2020-01-01", periods=n)
    out = {}
    for c in ("510300", "510310", "510320"):
        df = pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                           "volume": 1e6, "amount": 5e8}, index=idx)
        if dead_tail and c == "510310":
            df.loc[idx[-10]:, "amount"] = 1e4
        if dead_tail and c == "510320":
            df.loc[idx[-10:-1], "amount"] = 1e4
        out[c] = df
    return out


def test_low_volume_streak_gate():
    """连续低量：均值闸门看的是"这个月有没有量"，这道闸看的是"最近是否天天没人交易"。"""
    m = EA.build_matrices(_streak_pool())
    days = m["close"].index
    # 均值闸门单独看是放行的（20 日均额 ≈ 2.5 亿 >> 3 千万）—— 挡下它们的必须是新那道
    assert float(m["amount20"].loc[days[-1], "510310"]) > 3e7
    ok, _n = EA.tradable_mask(m, days[-1], None, min_listed=0, min_amount=3e7)
    assert ok["510300"] and not ok["510310"]
    # 强读法（谷值）存在的理由就在这只：近 10 日峰值够量，但过去 9 天全是 1 万元，
    # 建仓那天刚好碰上放量 —— 弱读法会把它当成可投，实际出不来。
    peak10 = float(m["amount"].rolling(10, min_periods=10).max().loc[days[-1], "510320"])
    assert peak10 >= 3e7 and not ok["510320"]
    # 关掉这道闸（ETF_AMT_STREAK=0）⇒ 回到 #13 口径，三只全放行
    ok0, _ = EA.tradable_mask(m, days[-1], None, min_listed=0,
                              min_amount=3e7, low_run_days=0)
    assert ok0.all()
    # 基准域与建仓域同源：universe_mask 那一行必须与 tradable_mask 逐只一致
    assert (EA.universe_mask(m, min_listed=0, min_amount=3e7).loc[days[-1]]
            == ok).all()


def test_topk_pays_per_name_slippage():
    """分档计费：同一笔调仓里贵的标的按贵的手数计，不再全表一个 6bp。

        c_i = 佣金 + slip(amount20_i)
    平值池（毛收益恒 0）上净收益必等于 -Σc_i/k 逐腿累加，能逐项对账。
    """
    pool = {c: df.copy() for c, df in _streak_pool(dead_tail=False).items()}
    pool["510310"]["amount"] = 5e7          # 15bp 档；510300 留 5e8 ⇒ 3bp 档
    m = EA.build_matrices(pool)
    days = m["close"].index
    score = pd.DataFrame({"510300": 1.0, "510310": 2.0}, index=days)   # 首选贵的这只
    net, gross, s = EA.topk_rebalance(score, m, days, k=1, hold=10,
                                      min_listed=0, min_amount=1e6)
    assert gross.abs().max() < 1e-12
    exp = EA.slippage_of(5e7) + EA.COMMISSION_RATE
    assert s["avg_cost_one_way"] == pytest.approx(exp)
    # k=1、恒定篮子 ⇒ 首次建仓付 c，期末清算再付 c，共 2c，别的日子 0
    assert net.sum() == pytest.approx(-2 * exp)
    # 显式传 cost ⇒ 压平，与历史（单一 6bp）口径逐位可比
    _, _, s2 = EA.topk_rebalance(score, m, days, k=1, hold=10, cost=0.0006,
                                 min_listed=0, min_amount=1e6)
    assert s2["avg_cost_one_way"] == pytest.approx(0.0006)


# ---------- 12. 规模面板：清盘线读数与"不知道 ≠ 规模小"（#17 的两张长表） ----------

def _fake_shares(monkeypatch, sh):
    """份额面板换成内存宽表：测试既不碰 `data/risk/`，也不碰网络。

    `load_scale_matrix`/`risk_readout` 都是函数内 `import fetch_etf_risk_panel`，
    改模块属性即可命中；`has_table` 一律关成 False ⇒ 净值那条腿留给单独的测试。
    """
    import fetch_etf_risk_panel as RP
    monkeypatch.setattr(RP, "load_shares_matrix", lambda *a, **k: sh)
    monkeypatch.setattr(RP, "has_table", lambda p: False)
    return RP


def test_scale_gate_never_removes_a_name_it_cannot_read(monkeypatch):
    """规模闸只剔"知道它小"的：份额读不出来（NaN）一律放行。

        A_{t,i} = shares_{t,i} × close_{t,i}
    510300=1e9×10=100 亿、510310=1e6×10=0.1 亿、510320 整列没有份额记录。
    沪市份额只有月末快照 ⇒ NaN 是数据源的结构性缺口，把"不知道"当"规模小"剔掉，
    等于按 akshare 的披露节奏做投资决策。
    """
    m = EA.build_matrices(_streak_pool(dead_tail=False))
    days = m["close"].index
    sh = pd.DataFrame({"510300": 1e9, "510310": 1e6}, index=days)
    _fake_shares(monkeypatch, sh)
    ok = EA.universe_mask(m, min_listed=0, min_amount=1e6, min_scale=5e7).loc[days[-1]]
    assert ok["510300"] and ok["510320"] and not ok["510310"]
    # 关闸（min_scale=0，即默认）⇒ 510310 回来：剔掉它的只有规模那一道，不是别的门
    assert EA.universe_mask(m, min_listed=0, min_amount=1e6).loc[days[-1]].all()


def test_risk_readout_reads_each_name_own_last_readable_day(monkeypatch):
    """镜像按市场滞后时，体检不能只读全池末日那一行。

    09-24 实测：沪市日线到 09-22、深市 388 只停在 09-21，而深市份额恰好有 09-22。
    旧写法 `aum.loc[aum.index[-1]]` 在滞后列上给 NaN ⇒ 打印"规模可读 0 只"，看着像
    面板全废，其实只差一天。这里造同样的错位：510310 的日线少最后两天。
    """
    pool = _streak_pool(dead_tail=False, n=70)
    pool["510310"].iloc[-2:] = np.nan          # 深市镜像滞后两天
    m = EA.build_matrices(pool)
    days = m["close"].index
    sh = pd.DataFrame({"510300": 1e9, "510310": 1e6, "510320": np.nan}, index=days)
    _fake_shares(monkeypatch, sh)
    rk = EA.risk_readout(m, codes=["510300", "510310", "510320"])
    assert rk["n"] == 3 and rk["n_scale_known"] == 2
    assert rk["scale_min"] == pytest.approx(0.1)        # 0.1 亿，且不是来自末日那行
    # 510310 天天 0.1 亿 < 0.5 亿，到它自己的最近可读日已连 68 日 ⇒ 越过 60 日线
    assert rk["n_below_line"] == 1 and rk["n_clearing_line"] == 1
    assert rk["streak_max"] == len(days) - 2
    assert rk["n_prem_known"] == 0              # 净值这条腿被 has_table 关掉了




# ---------- 14. 调仓时序：调仓日那根「开盘到开盘」记给谁（09-29 实测的钉子）----------
#
# 这一节的存在理由：上面三条 topk_rebalance 测试全用平值池（价格恒 10 元、毛收益
# 恒 0），把权重往后挪一天在平值池上差是 0 —— 时间口径**一条钉子都没有**。
# 09-29 实测（`temp/timing_shift_0929.py` 逐行复刻 + `temp/timing_shift_xcheck_0929.py`
# 零复刻构造，两条独立路径给出同一个量级）：现行 `lo = i + 1` 让**新**篮子拿到
# 区间 O_s→O_d1，而信号要到 s 收盘才算得出、要到 d1 开盘才买得进，这段拿不到。
# 族间合成 k=10 上这笔账值 **27.8~29.9pp 净年化/年**：
#   全窗口 2019+   43.38% → 15.57%（复刻 lag=2）/ 13.45%（零复刻 shift=1），回撤 −16.5% → −32.3%/−48.3%
#   样本外 2020-26 36.48% →  8.00% /  6.71%，超等权可投域 +27.54pp → −0.94pp/−2.23pp，回撤 −16.9% → −37.9%/−47.4%
# 八族各自付多少（全窗口 lag1−lag2）：反转 +39.7pp、日内隔夜 +37.7pp、价量交互 +27.5、
# 趋势位置 +27.3、动量 +7.5、价格水平 +0.3，而**波动 −1.0、量能 −0.6**（倒收）⇒ 这笔偏差
# 是快价格信号的属性，不是全池通胀。
#
# 所以这里用**变价池**把它钉住，两条钉子各管一件事、互不混淆：
#   ① 归属（`test_..._to_new_basket`）：边界那一格记给**新**篮子还是旧篮子
#      —— 分数每 2 行换一次点名对象，两列收益串不同，所以"记给谁"能分辨；
#   ② 时点（`test_..._needs_the_signal_a_day_early`）：这一格要不要送
#      —— 分数恒定只点同一只，两臂持有的标的完全相同，差的就是那一格。
# 注意 `topk_rebalance` 的 docstring 里"调仓日 d1 的收益仍归旧篮子"那句与代码
# 不符（代码是 `w[lo:hi]` 整段覆盖成新篮子）；本节的读数是**代码**的读数。


_ROT = pd.bdate_range("2020-01-06", periods=8)
# 收益串全取 2 的幂次分数：float64 下 `O_d = O_{d-1}·(1+r)` 的累乘与回除都逐位
# 精确，钉子能钉在 1e-15；且单笔最大 +6.25%，涨停闸（±10%）咬不到想测的那只。
_R500 = [0.0, 1 / 16, 1 / 32, -1 / 16, 0.0, 1 / 32, 0.0, 0.0]      # 510300 = A
_R510 = [0.0, 1 / 32, 1 / 16, 1 / 32, 1 / 16, 0.0, -1 / 32, 0.0]    # 510310 = B
_CODES = ["510300", "510310"]


def _rotating_pool():
    """两只标的 × 8 个交易日，开盘价按上面两串收益累乘，close=open。"""
    pool = {}
    for c, r in zip(_CODES, (_R500, _R510)):
        o = [100.0]
        for x in r[1:]:
            o.append(o[-1] * (1.0 + x))
        pool[c] = pd.DataFrame({"open": o, "high": o, "low": o, "close": o,
                                "volume": 1e6, "amount": 1e8}, index=_ROT)
    return pool


def _rotating_score(days, favor):
    """`favor[i]` 是第 i 行被点名的那一列（None = 整行 NaN）。"""
    sc = pd.DataFrame(1.0, index=days, columns=_CODES)
    for i, c in enumerate(favor):
        if c is None:
            sc.iloc[i, :] = np.nan
        else:
            sc.iloc[i, _CODES.index(c)] = 2.0
    return sc


def _gross_on_score(favor, mod=EA):
    """hold=2、k=1、cost=0 ⇒ 毛收益逐格就是"哪一天记给了哪一篮"，不含任何费率混入。

    闸门留法：`min_listed=0`（第 0 天就上市满 0 天）、`min_amount=1e6`（日成交额
    1e8 过容量与连续低量两道）、规模闸由 `tests/conftest.py` 的 `ETF_MIN_SCALE=0`
    关掉；涨停那道要看 O_d1/O_s−1，串里最大 +6.25% < ±10%，咬不上。
    """
    m = mod.build_matrices(_rotating_pool())
    days = m["close"].index
    sc = _rotating_score(days, favor)
    _net, gross, s = mod.topk_rebalance(sc, m, days, k=1, hold=2, cost=0.0,
                                        min_listed=0, min_amount=1e6)
    return gross, s


# 每 2 行换一次点名 ⇒ 三个调仓块（栅格 i=0/2/4，hold=2）各换一次人
FAVOR_SWITCH = ["510300", "510300", "510310", "510310",
                "510300", "510300", "510310", "510310"]
# 恒定只点 A ⇒ 三块持有同一只，两臂之差只剩时点
FAVOR_A = ["510300"] * 8


def test_rebalance_credits_boundary_interval_to_new_basket():
    """① 归属钉子（**现状钉子，不是验收**）：调仓日 d1 那格记给**新**篮子。

    hold=2、k=1 ⇒ 栅格落在 i=0/2/4，权重写在行 [1,2] / [3,4] / [5,6]：
        行 1,2 ← 信号日 0 选的 A   行 3,4 ← 信号日 2 选的 B   行 5,6 ← 信号日 4 选的 A
    行 1/3/5 是 O_s→O_d1：新篮子要到 d1 **开盘**才存在，这段它没持有。
    把行 3 单独念出来即可分辨"记给谁"：+1/32 是 **B** 的隔日跳空（记给新篮子），
    若代码把 d1 归旧篮子，这里会是 −1/16（A 的那格）。
    哪天把 `lo` 改成 `i + 2`，这条会立刻红 ⇒ 那一改是有意的、要连着重建环 2
    归档基线，不是顺手改掉。
    """
    gross, s = _gross_on_score(FAVOR_SWITCH)
    assert s["n_rebal"] == 3                       # 三块都在（六道闸门没把夹具吃掉）
    exp = [0.0, 1 / 16, 1 / 32, 1 / 32, 1 / 16, 1 / 32, 0.0, 0.0]
    assert gross.to_numpy() == pytest.approx(exp, abs=1e-15)
    assert gross.iloc[1] == pytest.approx(1 / 16, abs=1e-15)     # A：还没建仓就给了 +1/16
    assert gross.iloc[3] == pytest.approx(1 / 32, abs=1e-15)     # B：归新篮子（归旧篮子这里是 −1/16）
    assert gross.abs().sum() == pytest.approx(7 / 32, abs=1e-15)


def test_rebalance_boundary_interval_needs_the_signal_a_day_early():
    """② 时点钉子：分数恒定只点 A，把分数整体后移一天 ⇒ 那一格才"合法"。

    两臂持有的标的逐行相同（全程 A），唯一区别是行 1/2 归谁：
      现口径 `lo=i+1`：行 1,2 = A 的 +1/16、+1/32，共 3/32 —— 可 A 的分数要到
        行 0 收盘才算得出、行 1 开盘才买得进，这两格是白送的；
      后移一天：行 0 变 NaN ⇒ 首块被跳，行 1,2 归谁都不是（0），从行 3 起两臂
        逐格相同 ⇒ **差的就是那 3/32**，而且拿掉的正是"买不进去的那段"。
    这就是 09-29 零复刻复核（`score.shift(1)` 喂官方函数）在同一把小尺子上的读数。
    若 `lo` 被挪到 `i + 2`，这条同样会红（行 3 会变成 0）⇒ 与上一条一前一后，
    改哪儿都躲不掉。
    """
    cur, s_cur = _gross_on_score(FAVOR_A)
    shf, s_shf = _gross_on_score([None] + FAVOR_A[:-1])
    assert s_cur["n_rebal"] == 3 and s_shf["n_rebal"] == 2       # 首块因整行 NaN 被跳
    assert cur.to_numpy() == pytest.approx(
        [0.0, 1 / 16, 1 / 32, -1 / 16, 0.0, 1 / 32, 0.0, 0.0], abs=1e-15)
    assert shf.to_numpy() == pytest.approx(
        [0.0, 0.0, 0.0, -1 / 16, 0.0, 1 / 32, 0.0, 0.0], abs=1e-15)
    assert cur.iloc[1] == pytest.approx(1 / 16, abs=1e-15)       # 白送的那格
    assert shf.iloc[1] == pytest.approx(0.0, abs=1e-15)          # 拿不到的那段没人拿
    assert (cur - shf).abs().to_numpy()[3:] == pytest.approx(0.0, abs=1e-15)  # 行 3 起两臂相同
    assert cur.abs().sum() - shf.abs().sum() == pytest.approx(3 / 32, abs=1e-15)
    assert cur.abs().sum() > 1e-9                  # 不许是"两臂全 0 所以都过"的恒真
