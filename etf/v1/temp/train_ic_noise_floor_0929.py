# -*- coding: utf-8 -*-
"""「折 1 训练段只有 4 只标的」到底让 IC 门槛变得多没牙？——只读量，不改判据。

⚠️⚠️ 本脚本口径已证伪、读数作废，正确版本见 temp/ic_timeseries_beta_0929.py。
   原因：这里把因子与标签堆成一张 (日期×标的) 长面板、**算一次** Spearman；
   而挖掘侧判据（`common/src/core/llm_factor_agent.py:97-114`）是**逐只标的各算
   一个时序 IC 再对标的取均值**。长面板口径让 677 天替我摊薄了噪声（sd 只 0.007），
   于是得出「随机因子过不了 0.01 闸」的相反结论；真口径下折 1 的 sd 是 0.020、
   85% 的随机因子过闸。下面的表保留只为记录这条错误是怎么发生的。

背景读数（temp/train_breadth_0929.py，同批跑出）：三折训练段**深度完全一样**
（各 677 bar），差的是**宽度**——折 1 可用 4/100、折 2 17/100、折 3 69/100。
挖因子的 IC 是截面量，所以真正的约束是宽度。

量法：往每一折的**真实训练面板**里灌纯随机因子（逐日在当天的可用标的之间随机
打乱名次，与未来收益毫无关系），用主线同一把尺子 `factor_dsl.compute_ic` 算 IC，
重复 200 次 ⇒ 得到「什么都不懂时 IC 能有多高」的分布。门槛 0.01
（`config.py:183 ic_min_threshold`）与本场折 1 的真读数（mom_5 IC=-0.0371、
vol_20 IC=-0.0331，都被判 ✅）就放进这个分布里看分位。

配对对照（这把尺子的牙）：同一个折 3 窗口，把每日截面**随机抽到折 1 的只数**，
噪声底若涨到折 1 的量级 ⇒ 驱动是宽度；若不动 ⇒ 我归因错了，两条读数一起作废。

自检（不成立就 die，不许静默出数）：
  C1 随机因子的 IC 均值必须贴着 0（|mean| < 0.01）⇒ 否则堆叠口径本身有系统偏；
  C2 面板格数必须 ≤ train_breadth_0929.py 的「总格数」2411/9815/42507
     （dropna 会掉标签末行，允许少、不能多）⇒ 两把尺子量的必须是同一张面板；
  C3 配对对照的噪声底必须 ≥ 同折全宽度噪声底 ⇒ 否则「宽度驱动」不成立；
  C4 有限 IC 的抽样次数必须 ≥ 90%，否则分布不可用；每日只数从数据现读，不写死；
  C5 种子固定 ⇒ 把本脚本连跑两遍，sd 与概率表必须逐位相同。
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: F401  必须最先：挂载 src/ 与 common/ 的路径

from config import FREQ, WALK_FORWARD                        # noqa: E402
from data_loader import DataLoader                           # noqa: E402
from etf_universe import get_universe                        # noqa: E402
from factor_dsl import compute_ic                            # noqa: E402
from walk_forward import make_splits                         # noqa: E402

GATE = 240                 # walk_forward.py:93 的「需 >240 根 bar」
IC_GATE = 0.01             # config.py:183 ic_min_threshold
N_DRAW = 200
SEED = 20260929
FOLD1_REAL_IC = 0.0331     # 本场折 1 真通过的 vol_20（|IC|）
GRID_EXPECT = {1: 2411, 2: 9815, 3: 42507}   # train_breadth_0929.py 的总格数


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def build_panel(pool, tr_s, tr_e):
    """复刻挖掘侧看到的训练面板：>240 bar 闸 + close.pct_change().shift(-1) 标签"""
    frames = []
    for code, df in pool.items():
        sl = df.loc[tr_s:tr_e]
        if len(sl) <= GATE:
            continue
        fr = sl["close"].pct_change().shift(-1)
        j = pd.DataFrame({"fr": fr}).dropna()
        if j.empty:
            continue
        j["code"] = code
        frames.append(j)
    panel = pd.concat(frames)
    panel.index = pd.MultiIndex.from_arrays(
        [panel.index, panel["code"]], names=["datetime", "code"])
    return panel["fr"].sort_index()


def random_factor(fr, rng):
    """逐日在当日可用标的之间随机打乱名次 ⇒ 与未来收益独立"""
    days = fr.index.get_level_values("datetime")
    out = np.empty(len(fr))
    for _, pos in pd.Series(range(len(fr))).groupby(days, sort=False).groups.items():
        pos = np.asarray(pos)
        out[pos] = rng.permutation(len(pos))
    return pd.Series(out, index=fr.index)


def draw_ics(fr, n_draw=N_DRAW, seed=SEED, subsample=None):
    """抽 n_draw 个纯随机因子的 IC；subsample=k 时先把每日截面压到 k 只"""
    rng = np.random.default_rng(seed)
    days = fr.index.get_level_values("datetime")
    ics = []
    if subsample:
        uniq = pd.unique(days)
        names_by_day = {d: fr.xs(d, level="datetime").index.tolist() for d in uniq}
        for _ in range(n_draw):
            picked = []
            for d in uniq:
                names = names_by_day[d]
                if len(names) <= subsample:
                    picked.extend([(d, n) for n in names])
                else:
                    picked.extend([(d, n) for n in
                                   rng.choice(names, subsample, replace=False)])
            sub = fr.loc[picked]
            ics.append(compute_ic(random_factor(sub, rng), sub))
    else:
        for _ in range(n_draw):
            ics.append(compute_ic(random_factor(fr, rng), fr))
    a = np.asarray([v for v in ics if np.isfinite(v)], dtype=float)
    if len(a) < n_draw * 0.9:
        die(f"C4 只有 {len(a)}/{n_draw} 次抽到有限 IC ⇒ 面板退化，分布不可用")
    return a


universe = get_universe()
pool = DataLoader(freq=FREQ).load_pool(universe.universe["code"].tolist())
if not pool:
    die("池子空")
ref_code = max(pool, key=lambda c: len(pool[c]))
splits = make_splits(pool[ref_code].index)
if WALK_FORWARD["n_splits"] != len(splits):
    die(f"折数 {len(splits)} 与配置 {WALK_FORWARD['n_splits']} 不符")

print(f"随机种子={SEED}，每档抽 {N_DRAW} 次纯随机因子，"
      f"尺子=compute_ic（主线同一把）；IC 闸={IC_GATE}\n")

rows, floors = [], {}
for i, (tr_s, tr_e, _, _) in enumerate(splits):
    fold = i + 1
    fr = build_panel(pool, tr_s, tr_e)
    n_names = fr.index.get_level_values("code").nunique()
    daily = pd.Series(1, index=fr).groupby(
        fr.index.get_level_values("datetime")).sum()
    if len(fr) > GRID_EXPECT[fold]:
        die(f"C2 折 {fold} 面板 {len(fr)} 格 > train_breadth 的 "
            f"{GRID_EXPECT[fold]} 格 ⇒ 两把尺子不是同一张面板，作废")
    a = draw_ics(fr)
    mean = float(np.mean(a))
    if abs(mean) >= IC_GATE:
        die(f"C1 折 {fold} 随机因子 IC 均值 {mean:+.4f} 不贴 0 ⇒ 堆叠口径有系统偏")
    sd = float(np.std(a, ddof=1))
    absic = np.abs(a)
    floors[fold] = {"sd": sd, "n_names": n_names}
    rows.append({"折": fold, "每日只数中位": int(daily.median()),
                 "面板格数": len(fr), "随机IC_sd": round(sd, 4),
                 f"P(|IC|≥{IC_GATE})": round(float((absic >= IC_GATE).mean()), 3),
                 f"P(|IC|≥{FOLD1_REAL_IC})": round(
                     float((absic >= FOLD1_REAL_IC).mean()), 3)})
    print(f"折 {fold}：{n_names} 只 × {len(fr)} 格 ⇒ 随机 IC 均值 {mean:+.5f}、"
          f"标准差 {sd:.4f}")

print("\n──────── 纯随机因子的 IC 分布（把 0.01 的闸放进去看分位）────────")
print(pd.DataFrame(rows).to_string(index=False))

# ── 配对对照：折 3 窗口不动，只把每日截面压到折 1 的只数 ──
fr3 = build_panel(pool, splits[2][0], splits[2][1])
n4 = floors[1]["n_names"]
sd_c = float(np.std(draw_ics(fr3, subsample=n4), ddof=1))
if sd_c < floors[3]["sd"]:
    die(f"C3 配对对照噪声底 {sd_c:.4f} < 折 3 全宽度 {floors[3]['sd']:.4f}"
        f" ⇒ 驱动不是宽度，上面两条读数都不能用")
print(f"\n──────── 配对对照（同一折 3 窗口/同 677 bar，只动宽度）────────")
print(f"  全宽度 {floors[3]['n_names']} 只/日：sd={floors[3]['sd']:.4f}"
      f"  →  压到 {n4} 只/日：sd={sd_c:.4f}（{sd_c / floors[3]['sd']:.1f}×）")
print(f"  ⇒ 年份、窗口、bar 数全没动，噪声底随宽度变化 ⇒ 归因给宽度成立")

print(f"\n──────── 与本场折 1 真读数对照 ────────")
print(f"  折 1 真挖出并过闸的 vol_20 |IC|={FOLD1_REAL_IC}、mom_5 |IC|=0.0371；"
      f"纯随机因子达到 {FOLD1_REAL_IC} 以上的概率 = "
      f"{rows[0][f'P(|IC|≥{FOLD1_REAL_IC})']:.1%}")
print("读数边界：这里量的是「随机因子有多容易过闸」，不是「折 1 的因子是假的」；"
      "真因子有没有信号，要看它自己的样本外那一腿。")
