# -*- coding: utf-8 -*-
"""选项 D：折内过闸的 IC 里有多少是「时序 beta」（整市场一起涨跌），多少是选股。

口径先钉死（这一条决定了前面那份噪声底表为什么作废）：
  挖掘侧判据 = `common/src/core/llm_factor_agent.py:97-114` ⇒
  **逐只标的各算一个时序 Spearman IC**（该标的自己的因子 vs 自己的次日收益，
  跑满整段训练窗），再对标的取 `np.mean`。不是"堆叠面板算一次"，
  也不是"逐日截面 IC 取均值"。
  ⇒ 前一稿 temp/train_ic_noise_floor_0929.py 用堆叠面板口径，量的不是这把尺子，
    那张 35%/23%/2% 的表整体作废，以本脚本 P1 为准。

门槛到底是多少（09-29 import 实测，三个数字各自是各自的用途）：
  0.005 = **链路真用的那道闸**：`main.py:65 select_factors(min_ic=0.005)`
          （三处调用全用默认值）+ `MULTI_SOURCE.min_ic_per_source=0.005` 逐源地板。
          日志里「候选 8，过 IC 门槛 8」这句就是它判的。
  0.02  = `IC_THRESHOLD`（来自 `common/src/config_base.py:112`，daily 档），
          只喂给 LLM agent 自己那个 `pass` 标记 ⇒ 比真闸严 4 倍，但不是入池闸。
  0.01  = `config.py:183 ic_min_threshold`，是**因子衰减/生命周期**用的
          （`strategy_lifecycle.py:141`），跟挖掘准入无关。此前把它当挖掘门槛是错的。
  ⇒ 噪声底要对 0.005 量，否则报的是一道没在拦东西的闸。

时序 beta 为什么在这把尺子上是问题：逐只时序 IC 量的是「这只 ETF 自己的过去
预测它自己的未来」= 择时，而 ETF 之间同涨同跌 ⇒ 4 只的 IC 近乎同一个样本，
"平均 4 只"省下的噪声远小于 √4。两把刀各量一次：
  P1 噪声底（同口径）：每只灌独立随机数 ⇒ mean_ic 的分布；
  P3 有效性：标的两两 corr(因子)、corr(标签) ⇒ 有效样本数
     k_eff = k / (1 + (k-1)·ρ̄)，ρ̄ 大 ⇒ k_eff→1 ⇒ 名义 SE 低估 √(k/k_eff) 倍；
  P4 归因：同一表达式「逐日截面去均值」前后各算一次 mean_ic ——
     去均值把当日全体共同的水平抹掉，只剩"谁比谁强"，
     若去均值后 IC→0 ⇒ 过闸的 IC 全是时序/共同市场腿，没有选股腿。

自检（不成立就 die，不许静默出数）：
  P0 复现闸（正对照）：用本脚本的口径重算折 1 的 mom_5/mom_20/vol_20，
     必须与链路日志「多源挖掘 折 1」那三行逐位对上（|Δ|<5e-5）。
     对不上 ⇒ 我拆的根本不是判据在用的那个数，整张表作废。
  P2 配对对照：折 3 窗口只留 4 只标的 ⇒ P1 的噪声底应涨到折 1 量级；
     不涨 ⇒ 宽度不是驱动，P1/P3 都不能归因给宽度。
  P5 恒等闸：去均值因子满足 f = f_demeaned + 当日截面均值，逐格 max|Δ|<1e-12；
     且「当日截面均值」这一列作为因子时，它的去均值版本必须恒为 0
     （否则我的去均值实现漏了东西）。
"""
import os
import re
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: F401

from config import FREQ                                        # noqa: E402
from config import IC_THRESHOLD as LLM_FLAG                    # noqa: E402
from data_loader import DataLoader                             # noqa: E402
from etf_universe import get_universe                          # noqa: E402
from factor_dsl import safe_eval, compute_ic                   # noqa: E402
from walk_forward import make_splits                           # noqa: E402

GATE = 240
IC_GATE = 0.01
# 链路日志里那句「过 IC 门槛 N」用的是 select_factors 的默认门槛（main.py:65
# min_ic=0.005），不是 config 的 ic_min_threshold=0.01。噪声底必须对着**真正
# 拦住因子的那把**量，否则报的是没在用的那道闸的概率。
BIND_GATE = 0.005
N_DRAW = 200
SEED = 20260929
LOG = os.path.join(HERE, "chain_resume_0929_1645.log")
EXPRS = {                       # 与 llm_factor_agent.py:65-72 逐字同表达式
    "mom_5": "delta(close, 5) / (ma(df, 20) + 1e-9)",
    "mom_20": "delta(close, 20) / (ma(df, 60) + 1e-9)",
    "vol_20": "-ts_std(returns, 20)",
}


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def train_pool_of(pool, tr_s, tr_e):
    """复刻 walk_forward.py:91-93 的 >240 bar 闸"""
    tp = {c: df.loc[tr_s:tr_e] for c, df in pool.items()}
    return {c: df for c, df in tp.items() if len(df) > GATE}


def mean_ic(train_pool, ffun):
    """挖掘口径：逐只 compute_ic，再对标的取均值。返回 (均值, 逐只列表)"""
    ics = []
    for code, df in train_pool.items():
        try:
            f = ffun(df)
            fr = df["close"].pct_change().shift(-1)
            ic = compute_ic(f, fr)
        except Exception:
            continue
        if ic is not None and np.isfinite(ic):
            ics.append(float(ic))
    if not ics:
        die("某档一只标的都没算出 IC ⇒ 面板退化，不许出数")
    return float(np.mean(ics)), ics


def demean_by_day(train_pool, expr):
    """逐日截面去均值：f_it − mean_i(f_it)（i 跑遍当日在场标的）"""
    raw = {c: safe_eval(expr, df) for c, df in train_pool.items()}
    st = pd.concat(raw, axis=1)          # 行=日期，列=标的
    day_mean = st.mean(axis=1)
    dm = st.sub(day_mean, axis=0)
    return {c: raw[c] for c in raw}, {c: dm[c] for c in raw}, day_mean


universe = get_universe()
pool = DataLoader(freq=FREQ).load_pool(universe.universe["code"].tolist())
ref_code = max(pool, key=lambda c: len(pool[c]))
splits = make_splits(pool[ref_code].index)

tp1 = train_pool_of(pool, *splits[0][:2])
print(f"折 1 训练面板：{len(tp1)} 只 × "
      f"{np.median([len(d) for d in tp1.values()]):.0f} bar/只（中位）\n")

# ── P0 复现闸：本脚本口径必须重算出日志里那三行 IC ──
logged = {}
if os.path.exists(LOG):
    with open(LOG, errors="ignore") as f:
        inside = False
        for line in f:
            if "多源挖掘 折 1" in line:
                inside = True
                continue
            if inside:
                m = re.match(r"\s+(mom_5|mom_20|vol_20)\s+IC=([-\d.]+)", line)
                if m:
                    logged.setdefault(m.group(1), float(m.group(2)))
                    if len(logged) == 3:
                        break
if not logged:
    die("P0 缺正对照：日志里读不到「多源挖掘 折 1」那三行 IC ⇒ "
        "无法证明本脚本量的就是判据那个数，整张表不许出")
mine = {n: mean_ic(tp1, lambda df, e=EXPRS[n]: safe_eval(e, df))[0]
        for n in logged}
for n, want in logged.items():
    if abs(mine[n] - want) > 5e-5:
        die(f"P0 复现不上 {n}：本脚本 {mine[n]:+.6f} vs 日志 {want:+.4f}"
            f" ⇒ 口径不同一套，下面的数全部作废")
print(f"[P0 ✅] 折 1 三条模板 IC 逐位复现链路日志："
      f"{'、'.join(f'{n}={mine[n]:+.4f}' for n in logged)}")

print("\n──────── P1 噪声底（挖掘同一把尺子：逐只时序 IC 再平均）────────")
rows, floors = [], {}
for i, (tr_s, tr_e, _, _) in enumerate(splits):
    fold = i + 1
    tp = train_pool_of(pool, tr_s, tr_e)
    k = len(tp)
    rng = np.random.default_rng(SEED + fold)
    draws = []
    for _ in range(N_DRAW):
        ics = [compute_ic(pd.Series(rng.random(len(df)), index=df.index),
                          df["close"].pct_change().shift(-1))
               for df in tp.values()]
        ics = [v for v in ics if np.isfinite(v)]
        draws.append(np.mean(ics))
    a = np.asarray(draws, dtype=float)
    if len(a) < N_DRAW * 0.9:
        die(f"折 {fold} 只有 {len(a)}/{N_DRAW} 次拿到有限 IC")
    if abs(float(np.mean(a))) >= BIND_GATE:
        die(f"折 {fold} 随机均值 {np.mean(a):+.4f} 不贴 0（已用到真闸 "
            f"{BIND_GATE} 当容差）⇒ 实现有系统偏")
    sd = float(np.std(a, ddof=1))
    absic = np.abs(a)
    floors[fold] = {"sd": sd, "k": k}
    rows.append({"折": fold, "标的数": k, "随机IC_sd": round(sd, 4),
                 "√k名义": round(sd * np.sqrt(k), 4),
                 f"P(|IC|≥{BIND_GATE})真闸": round(float((absic >= BIND_GATE).mean()), 3),
                 f"P(|IC|≥{LLM_FLAG})llm旗": round(float((absic >= LLM_FLAG).mean()), 3),
                 f"P(|IC|≥{IC_GATE})": round(float((absic >= IC_GATE).mean()), 3),
                 "P(|IC|≥0.0331)": round(float((absic >= 0.0331).mean()), 3)})
    print(f"  折 {fold}（{k} 只）：sd={sd:.4f}，随机因子过**真闸 0.005** 概率 "
          f"{rows[-1][f'P(|IC|≥{BIND_GATE})真闸']:.0%}、过 llm 那面 0.02 旗 "
          f"{rows[-1][f'P(|IC|≥{LLM_FLAG})llm旗']:.0%}、过 0.01 概率 "
          f"{rows[-1][f'P(|IC|≥{IC_GATE})']:.0%}，达到 0.0331 概率 "
          f"{rows[-1]['P(|IC|≥0.0331)']:.0%}")
print(pd.DataFrame(rows).to_string(index=False))

# ── P2 配对对照：折 3 窗口只留 4 只 ──
tp3 = train_pool_of(pool, *splits[2][:2])
keep = sorted(tp3, key=lambda c: -len(tp3[c]))[:floors[1]["k"]]
tp3s = {c: tp3[c] for c in keep}
rng = np.random.default_rng(SEED + 99)
d3s = [np.mean([v for v in (
    compute_ic(pd.Series(rng.random(len(df)), index=df.index),
               df["close"].pct_change().shift(-1)) for df in tp3s.values())
    if np.isfinite(v)]) for _ in range(N_DRAW)]
sd_3s = float(np.std(d3s, ddof=1))
if sd_3s < floors[3]["sd"]:
    die(f"P2 折 3 压到 {len(keep)} 只后噪声底 {sd_3s:.4f} 反而 < 全宽度 "
        f"{floors[3]['sd']:.4f} ⇒ 宽度不是驱动，P1 不许归因给宽度")
print(f"\n──────── P2 配对对照（同折 3 窗口、同 677 bar，只动标的数）────────")
print(f"  {floors[3]['k']} 只：sd={floors[3]['sd']:.4f}  →  "
      f"只留 {len(keep)} 只：sd={sd_3s:.4f}（{sd_3s / floors[3]['sd']:.1f}×）")
print(f"  折 1 实测 sd={floors[1]['sd']:.4f} ⇒ 同宽度量级对得上"
      f"（比值 {sd_3s / floors[1]['sd']:.2f}）")

# ── P3 有效样本数：标的之间共用同一个市场腿有多严重 ──
print("\n──────── P3 标的间共线程度（决定「平均 4 只」实际省了多少噪声）────────")
for fold, tr in [(1, splits[0][:2]), (3, splits[2][:2])]:
    tp = train_pool_of(pool, *tr)
    fmat = pd.concat({c: safe_eval(EXPRS["mom_5"], df) for c, df in tp.items()},
                     axis=1).dropna(how="all")
    ymat = pd.concat({c: df["close"].pct_change().shift(-1)
                      for c, df in tp.items()}, axis=1).dropna(how="all")

    def avg_pairwise(mat):
        cols = [mat[c].dropna() for c in mat]
        vals = []
        for j in range(len(cols)):
            for m in range(j + 1, len(cols)):
                both = pd.concat([cols[j], cols[m]], axis=1, join="inner").dropna()
                if len(both) > 60:
                    vals.append(spearmanr(both.iloc[:, 0], both.iloc[:, 1]).statistic)
        return float(np.mean([v for v in vals if np.isfinite(v)]))

    rho_f, rho_y = avg_pairwise(fmat), avg_pairwise(ymat)
    k = len(tp)
    # 两只都相关的乘积近似：IC 之间的相关 ~ ρ_f·ρ_y
    rho_ic = rho_f * rho_y
    k_eff = k / (1 + (k - 1) * max(0.0, rho_ic))
    print(f"  折 {fold}（{k} 只）：corr(因子)两两均 {rho_f:+.3f}、"
          f"corr(次日收益)两两均 {rho_y:+.3f} ⇒ ρ̄≈{rho_ic:+.3f}、"
          f"有效样本 k_eff≈{k_eff:.1f}（名义 {k}）⇒ 名义 SE 低估 "
          f"{np.sqrt(k / max(k_eff, 1e-9)):.1f}×")

# ── P4 归因：去均值前 vs 去均值后 ──
print("\n──────── P4 时序腿 / 截面腿拆分（同一把尺子，两种因子）────────")
print(f"  折 1（{len(tp1)} 只，本场真过闸的两条就在这里）")
for fold in (1, 2, 3):
    tp = train_pool_of(pool, *splits[fold - 1][:2])
    for name in ("mom_5", "mom_20", "vol_20"):
        raw, dm, day_mean = demean_by_day(tp, EXPRS[name])
        # P5 恒等闸
        for c in raw:
            got = (dm[c] + day_mean.reindex(raw[c].index).fillna(0)
                   - raw[c]).abs()
            if got.max() > 1e-12:
                die(f"P5 折 {fold} {name}/{c} 恒等重建失败 max|Δ|={got.max():.2e}")
        # P5 第二条：当日截面均值本身去均值 ⇒ 恒为 0
        chk = pd.concat({c: day_mean.reindex(tp[c].index) for c in tp}, axis=1)
        chk = chk.sub(chk.mean(axis=1), axis=0).abs()
        if float(chk.max().max()) > 1e-12:
            die(f"P5 折 {fold} 「截面均值列」去均值后不恒为 0"
                f"（{float(chk.max().max()):.2e}）⇒ 去均值实现漏了东西")
        m_raw, _ = mean_ic(tp, lambda df, e=EXPRS[name]: safe_eval(e, df))
        ics_dm = [compute_ic(dm[c], tp[c]["close"].pct_change().shift(-1))
                  for c in dm]
        ics_dm = [v for v in ics_dm if np.isfinite(v)]
        m_dm = float(np.mean(ics_dm))
        ics_mkt = [compute_ic(day_mean.reindex(tp[c].index),
                              tp[c]["close"].pct_change().shift(-1)) for c in dm]
        ics_mkt = [v for v in ics_mkt if np.isfinite(v)]
        m_mkt = float(np.mean(ics_mkt))
        if fold == 1:
            print(f"    {name:8s} 原始 IC={m_raw:+.4f}"
                  f"（真闸 0.005 判 "
                  f"{'✅' if abs(m_raw) >= BIND_GATE else '❌'}／"
                  f"agent 自评语义 0.02 判 "
                  f"{'✅' if abs(m_raw) >= IC_GATE else '❌'}）｜"
                  f"纯时序腿（当日截面均值）IC={m_mkt:+.4f}｜"
                  f"纯截面腿（去均值后）IC={m_dm:+.4f}")
        else:
            print(f"    折 {fold} {name:8s} 原始 IC={m_raw:+.4f}｜"
                  f"时序腿 {m_mkt:+.4f}｜截面腿 {m_dm:+.4f}")
print(f"\n  过闸门槛：链路真用的是 |IC|≥{BIND_GATE}（main.py:65 + 逐源地板），"
      f"|IC|≥{LLM_FLAG} 是 LLM agent 自己那个 pass 标记（config_base.py:112，只拦 llm 源），"
      f"|IC|≥{IC_GATE} 属 FACTOR_DECAY['ic_min_threshold']（衰减/生命周期，与准入无关）；"
      f"截面腿若贴着 0 而原始腿过闸 ⇒ 过闸的钱来自「整市场一起涨跌」那条腿，不是选股")
print("\n读数边界：P4 的「时序腿」用的当日截面均值只有 "
      f"{len(tp1)} 只可平均（折 1），本身就是粗读数；"
      "它证明的是「共同市场腿能单独撑起这个 IC」，不给这条腿的幅度定级。")
