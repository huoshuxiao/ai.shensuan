# -*- coding: utf-8 -*-
"""只读探针：量「日线链路自己那一路」的成交时点值多少钱（10-06 选项A）

为什么这一场不复刻代码：09-29 那把尺（`timing_shift_0929.py`）复的是研究层
`etf_admission.topk_rebalance`，它默认不进日更链路。链路每天写的
`equity_daily.csv` / `dsr_daily.csv` / `multi_*` 走的是 `PortfolioEngine`，
到点即在**同一根 bar**采纳新目标、并按**当日收盘价**成交
（`portfolio_engine.py:117-120` 采纳、`:166`+`:72-73` 取 `"close"`）。
两笔偏差互不相干，不合并。

这一场的旋钮不动引擎一行代码，而是把归档信号表的**日期整体推后 N 根 bar**
再喂给同一个官方引擎 ⇒ 两臂都走真代码路径，零复刻漂移。

  A0 现况重放    信号日 s → 按 close(s) 成交（官方行为）
  A1 推迟一根    信号日 s → 按 close(s+1) 成交
  A2 推迟两根    方向对照（同一构造，再推一根）

三道闸（任何一道不过就如实报，不许把"没差异"念成"没偏差"）：
  G1  身份闸：A0 的逐日净值 vs 归档 `equity_daily.csv`，max **相对**差 < 1e-15
      ⇒ 否则本场的构造不忠实，全部读数作废。（绝对闸不可用：净值到 3.8e3 时
      一个 ULP≈4.5e-13，逐位相同的重放也过不了 1e-18。）
  G1b 整数闸：A0 的成交笔数必须等于归档 `trades_daily.csv` 的行数（浮点没余地）。
  G2  牙：A1 的日收益必须与 A0 不同 ⇒ 相同即旋钮无牙（平值池那种假绿），
      读数只报不当结论。
  G3  单调：A2 与 A1 的日收益也不同 ⇒ 证明推的是同一根轴、不是随机抖。

年化/夏普口径直接复用官方 `DailyBacktester._stats`，不在这里另起一把尺
（`backtest_daily.py:67-102`：ann = adapter 按 252 折算，sharpe = 年化收益/年化波动）。

写盘面：只有 stdout 与 `/tmp/probe_engine_fill_timing_1006/`，本线 `data/` 一个字节不动。
"""

import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))          # etf/v1/temp
LINE = os.path.normpath(os.path.join(HERE, ".."))          # etf/v1
SRC = os.path.join(LINE, "src")
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401  必须先于项目模块导入（平铺导入名的来源）

from config import RISK_CONTROL                              # noqa: E402
from etf_universe import get_universe                        # noqa: E402
from data_loader import DataLoader                           # noqa: E402
from backtest import get_backtester                          # noqa: E402

RESULTS = os.path.join(LINE, "data", "results")
OUTDIR = "/tmp/probe_engine_fill_timing_1006"
os.makedirs(OUTDIR, exist_ok=True)


def log(*a):
    print(*a, flush=True)


def archived_risk_params():
    """官方那场用的 risk_params 出处：`optimized_params_daily.json`（AUX 第 5 项）

    取不到就退回 `RISK_CONTROL` 默认，但要把"退回"念出来——它会让两臂与归档
    那条腿不是同一套风控，身份闸 G1 随之变红，别悄悄换尺子。
    """
    p = os.path.join(RESULTS, "optimized_params_daily.json")
    if not os.path.exists(p):
        return dict(RISK_CONTROL), "文件不在 ⇒ 退回 RISK_CONTROL 默认"
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    rp = d.get("risk_params") or {}
    merged = {**RISK_CONTROL, **rp}
    return merged, f"取到 {len(rp)} 键（n_factors={d.get('n_factors')}）"


def load_signals():
    p = os.path.join(RESULTS, "signals_daily.csv")
    # ⚠️ code 必须按字符串读：ETF 代码全是数字，默认解析会变成 '159206.0'，
    #    与池里的 '159206' 交集为 0 ⇒ 引擎一行都买不了、三臂恒等于空仓（10-06 首跑就
    #    栽在这一格：0 成交被 G1 身份闸当场抓出来，max|Δ|=6.275e+03）。
    s = pd.read_csv(p, encoding="utf-8-sig", parse_dates=["datetime"],
                    dtype={"code": str})
    s = s.set_index("datetime")[["code", "weight", "score"]].sort_index()
    # 空仓哨兵在 CSV 里是空单元格 ⇒ 读回来是 NaN，而官方在内存里给的是空串 ""。
    # `expand_rebalance` 走 `str(row["code"])` 会把 NaN 变成 "nan" 这个假标的，
    # 虽然后面被 `c in self.pool` 挡掉、净值不受影响，但回放要逐字对上契约 ⇒ 填回 ""。
    s["code"] = s["code"].fillna("")
    return s, p


def shift_signals(signals, timeline, n_bars):
    """把每行信号的日期推到时间轴上第 n_bars 根之后（引擎自己的 bar 序列）

    空哨兵（code=""）一起推——契约里它也是调仓日的一员，只推有仓的行会让
    空仓日与持仓日的栅格错位。越过时间轴末端的行**丢掉**（最后一次调仓在
    数据尽头之外没得成交），丢几行要报数。
    """
    tl = pd.DatetimeIndex(timeline)
    pos = tl.searchsorted(signals.index, side="left")
    new = np.where(pos + n_bars < len(tl), tl[np.clip(pos + n_bars, 0, len(tl) - 1)],
                   pd.NaT)
    out = signals.copy()
    out.index = pd.DatetimeIndex(new)
    dropped = int(out.index.isna().sum())
    out = out[out.index.notna()]
    return out, dropped


def readout(tag, result, t0):
    eq = result["equity"]["equity"]
    st = result["stats"]
    d = {
        "arm": tag,
        "总收益率": float(eq.iloc[-1] / eq.iloc[0] - 1),
        "年化": st["年化收益率"],
        "夏普": st["夏普比率"],
        "最大回撤": st["最大回撤"],
        "交易次数": st["交易次数"],
        "年化换手率": st["年化换手率"],
        "n_bars": len(eq),
        "墙钟s": round(time.time() - t0, 1),
    }
    log(f"  [{tag}] 年化={d['年化']} 夏普={d['夏普']} 回撤={d['最大回撤']} "
        f"交易={d['交易次数']} 换手={d['年化换手率']} 用时={d['墙钟s']}s")
    return d, eq


def main():
    log("=== 只读探针：链路那一路的成交时点（10-06 选项A）===")
    risk, src_note = archived_risk_params()
    log(f"[参数] risk_params 出处：{src_note}")

    signals, sp = load_signals()
    log(f"[信号] {sp} ⇒ {len(signals)} 行 / {signals.index.nunique()} 个调仓日 "
        f"({signals.index.min():%Y-%m-%d} ~ {signals.index.max():%Y-%m-%d})")

    t0 = time.time()
    log("[池] 开始加载（只读）……")
    universe = get_universe()
    codes = universe.universe["code"].tolist()
    pool = DataLoader(freq="daily").load_pool(codes)
    log(f"[池] {len(pool)} 只标的，用时 {time.time() - t0:.1f}s")
    if not pool:
        log("❌ 池空 ⇒ 全场作废")
        return 1

    bt = get_backtester(pool, universe, risk)
    t0 = time.time()
    a0, eq0 = readout("A0 现况重放", bt.run(signals), t0)

    timeline = eq0.index
    rows = [dict(a0)]
    eqs = {"A0": eq0}

    for n, tag in ((1, "A1 推迟一根"), (2, "A2 推迟两根")):
        sh, dropped = shift_signals(signals, timeline, n)
        log(f"[旋钮] {tag}：{len(sh)} 行被推后 {n} 根 bar，末端丢掉 {dropped} 行")
        t0 = time.time()
        d, eq = readout(tag, bt.run(sh), t0)
        rows.append(d)
        eqs[tag[:2]] = eq

    # ---- 三道闸 ----
    arch = pd.read_csv(os.path.join(RESULTS, "equity_daily.csv"),
                       encoding="utf-8-sig", parse_dates=["date"]).set_index("date")["equity"]
    eq1, eq2 = eqs["A1"], eqs["A2"]
    common = eq0.index.intersection(arch.index)
    delta = np.abs(eq0.loc[common].to_numpy() - arch.loc[common].to_numpy())
    # G1 用**相对**差，不用绝对 1e-18：净值到 3.8e3 量级时一个 ULP≈4.5e-13，
    # 绝对闸在逐位相同的重放下也永远过不了（10-06 首跑我就栽在这一格，把它
    # 念成了"构造不忠实"）。归档表是十进制往返过的，残差落在几 ULP 内就是同一列数。
    rel = float((delta / np.abs(arch.loc[common].to_numpy())).max()) if len(common) else float("nan")
    first_bad = int(np.argmax(delta > 0)) if (delta > 0).any() else -1
    g1 = rel < 1e-15
    # G1b 整数闸：成交笔数与归档 trades_daily.csv 的行数对死——浮点没有的余地。
    n_trades_arch = len(pd.read_csv(os.path.join(RESULTS, "trades_daily.csv"),
                                   encoding="utf-8-sig"))
    n_trades_a0 = int(a0["交易次数"])
    # G2/G3 用**收益率**而不是净值水平：水平会累积，收益率把量级拉回 1e-2 档
    r0 = eq0.pct_change().dropna()
    r1 = eq1.pct_change().dropna()
    r2 = eq2.pct_change().dropna()
    b1 = r0.index.intersection(r1.index)
    b2 = r1.index.intersection(r2.index)
    g2 = float(np.abs(r0.loc[b1].to_numpy() - r1.loc[b1].to_numpy()).max())
    g3 = float(np.abs(r1.loc[b2].to_numpy() - r2.loc[b2].to_numpy()).max())
    log("\n=== 闸 ===")
    log(f"G1 身份闸 A0 vs 归档 equity_daily.csv：交集 {len(common)} 格，"
        f"max 相对差={rel:.3e}，首个不等行 "
        f"{'无（逐位相同）' if first_bad < 0 else f'{common[first_bad]:%Y-%m-%d} 绝对差 {delta[first_bad]:.3e}（≈{delta[first_bad] / 4.55e-13:.1f} ULP）'} "
        f"⇒ {'通过' if g1 else '不通过 ⇒ 本场构造不忠实，读数全部作废'}")
    log(f"G1b 整数闸 A0 成交笔数 {n_trades_a0} vs 归档 trades_daily.csv {n_trades_arch} 行 "
        f"⇒ {'通过' if n_trades_a0 == n_trades_arch else '不通过'}")
    log(f"G2 牙 A0 vs A1 日收益 max|Δ|={g2:.3e} "
        f"({'有牙' if g2 > 1e-12 else '无牙 ⇒ 旋钮没活到净值层，不许当结论'})")
    log(f"G3 单调 A1 vs A2 日收益 max|Δ|={g3:.3e} "
        f"({'再推一根还能动' if g3 > 1e-12 else '再推一根不动 ⇒ 只推一根是巧合，读数降级'})")

    pd.DataFrame(rows).to_csv(os.path.join(OUTDIR, "arms_1006.csv"), index=False)
    log(f"[落盘] {OUTDIR}/arms_1006.csv（本线 data/ 未写一个字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
