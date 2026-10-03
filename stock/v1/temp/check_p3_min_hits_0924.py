# -*- coding: utf-8 -*-
"""P3 落地的判据自检（09-24 首版 / 09-27 按生产结构重写）

P3 改的是「一套剔除判据在两个位置用两个阈值」：

    n_hit_i(s) = Σ_rule 1[ pct_i(rule, s) >= ASHARE_SCREEN_QUANTILE ]
    域（不该买）   keep_i     = pool_i ∧ n_hit_i >= 1              并集
    待买入入口     keep_buy_i = pool_i ∧ n_hit_i <  ASHARE_BUY_MIN_HITS

两条都用，是因为 ⑮P0 实测它们各自是各自腿上的最优（并集在减法腿 +6.42%/年，
压在待买入名单上却 -0.42%）。风险也正好在这里：**一个函数交回两个布尔，最容易写反**
（把 keep 传给名单 = 回到那个已知的净负口径；把 keep_buy 传给域 = 悄悄放宽「不该买」）。
所以用合成数据把方向钉死，而不是等真数据跑出来再用眼睛比。

**09-27 为什么要重写这份**：㉑ 换轴之后 `BUY_EXPR` = `ts_mean(volume,20)` = 四条构造里的
`level`，**同一个字符串键**（一根轴两头用）。旧夹具先给 level 造一份「谁判响」的矩阵、
最后一行再给排序轴造一份，两次写的是**同一个键** ⇒ 后者把前者原地覆写掉，
期望里的 `4,4,3,2,1,…` 从此不可能出现（实得 `3,3,2,1,0,0,0,1,1,1`）。
不是判据坏了，是夹具的前提没了：**生产里拿不到「四条全判响」这一格**。

现夹具就按生产的真实形状构造，并且把那条结构性事实也钉成断言：
    排序轴升序 = 由「最安静」排到「最吵」，而 level 剔的是截面高分端（最吵的 20%）
    ⇒ **level 命中的那批永远排在队列末尾，永远不是买入候选**
所以头部（名单前排）最多只能被另外三条判响 ⇒ 头部 `n_hit` 上限 = 3，正好压在阈值上；
`n_hit = 4` 这一格在现口径下**构造不出来**，硬造就是造一个生产不可能出现的配置。

各档判的事（x = 代码序号后三位）：
    x=0,1   另外三条全判响    ⇒ n_hit=3，恰好落在阈值上，两道闸都挡（>=3 含 3）
    x=2     两条判响          ⇒ 域挡、名单放行
    x=3     只有量能波动响    ⇒ 域挡、名单放行（单条命中的那一角）
    x=4..6  无人判响          ⇒ 两处都放行（并且是名单前排）
    x=7..9  只有 level 判响    ⇒ 域挡、名单放行 —— 「一根轴两头用」的实证那一格
排序轴按 x 升序就是 0..9，所以名单顺序 = 序号顺序，好核对。
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src")
import _bootstrap  # noqa: F401,E402

import ashare_screen as A
from config import ASHARE_BUY_MIN_HITS, ASHARE_SCREEN_QUANTILE

# ---- 先钉住本夹具的前提：轴就是 level（㉑ 换轴造成的） ----
assert A.BUY_EXPR == A.RULE_EXPRS[0], (
    f"排序轴 `BUY_EXPR={A.BUY_EXPR!r}` 不再是四条构造里的 level {A.RULE_EXPRS[0]!r}："
    "本夹具的前提变了（一根轴两头用没了），要按新轴重写而不是改期望数")
print(f"前提 OK：轴 = level = {A.BUY_EXPR!r}（同一个键 ⇒ 只有一份矩阵）")

days = pd.date_range("2024-01-01", periods=30)
codes = [f"SH6000{i:03d}" for i in range(10)]
# 日涨幅固定 0.1%：让第三道执行闸（贴涨停）恒不触发，本脚本只判剔除这一环
close = pd.DataFrame(np.tile((100 * 1.001 ** np.arange(30))[:, None], (1, 10)),
                     index=days, columns=codes).astype("float32")
vol = pd.DataFrame(np.full((30, 10), 1e5), index=days, columns=codes).astype("float32")
mtx = {"close": close, "open": close.shift(1).fillna(close.iloc[0]),
       "volume": vol, "raw_price": close,
       "factor": pd.DataFrame(1.0, index=days, columns=codes),
       "ret_open": close.pct_change().fillna(0.0).astype("float32"),
       "ret_open0": close.pct_change().fillna(0.0).astype("float32"),
       "listed_days": pd.DataFrame(np.tile((np.arange(30) + 100)[:, None], (1, 10)),
                                   index=days, columns=codes),
       "volume_real": vol,
       "amount20": pd.DataFrame(1e9, index=days, columns=codes)}

s = days[-1]
pool = pd.Series(True, index=codes)
N = len(codes)

# ---- 轴 / level 共用**同一份**矩阵：值 = x 本身（0..9），升序排 = 序号排 ----
axis_mat = pd.DataFrame(np.tile(np.arange(N, dtype="float32")[None, :], (30, 1)),
                        index=days, columns=codes)
rule_mats = {A.BUY_EXPR: axis_mat}                  # ← 这一份同时是排序轴和 level 判据
# 另外三条构造：平值 0.5，被判响的那几只当日给 0.99（并列同值 ⇒ 共享平均名次）
HOT = {A.RULE_EXPRS[1]: [0, 1, 2, 3],               # 量能波动
       A.RULE_EXPRS[2]: [0, 1, 2],                  # 量能动量
       A.RULE_EXPRS[3]: [0, 1]}                     # 量能比
for e, hot in HOT.items():
    d = pd.DataFrame(0.5, index=days, columns=codes, dtype="float32")
    d.loc[s, [codes[i] for i in hot]] = np.float32(0.99)
    rule_mats[e] = d
assert set(rule_mats) == set(A.RULE_EXPRS), "四条构造必须都在，少一条就少一次命中"

keep, detail = A.volume_exclusion(rule_mats, s, pool)
n_hit = detail["n_hit"]
print(f"阈值 quantile={ASHARE_SCREEN_QUANTILE}  buy_min_hits={ASHARE_BUY_MIN_HITS}")
print("n_hit   :", {c[-3:]: int(v) for c, v in n_hit.items()})
print("域 keep :", sorted(c[-3:] for c, k in zip(codes, keep) if k))

# ① n_hit 与 excluded_by 必须自洽：命中条数 = 明细里那几个构造名（分号段数）
assert (n_hit.to_numpy() == np.where(detail["excluded_by"] == "", 0,
                                     detail["excluded_by"].str.count(";").to_numpy() + 1)
        ).all(), "n_hit 与 excluded_by 不一致"
assert [int(v) for v in n_hit] == [3, 3, 2, 1, 0, 0, 0, 1, 1, 1]
# ② 并集口径没被 P3 改动：n_hit>=1 ⇔ 被剔
assert (n_hit >= 1).to_numpy().tolist() == (~keep).to_numpy().tolist()
# ③ 「一根轴两头用」那条结构事实：level 判响的恰好是轴上最高分的那批 = 升序队列的**末尾**
level_hot = [i for i, v in enumerate(n_hit.to_numpy())
             if "量能水平" in str(detail["excluded_by"].iloc[i])]
assert level_hot == [7, 8, 9], f"level 应只咬轴值最高的一批（尾部 20%），实得 {level_hot}"
assert level_hot == sorted(level_hot) and axis_mat.loc[s].argsort()[-3:].tolist() == level_hot, \
    "level 命中的那批必须是升序队列最后几名 ⇒ 前排候选天然拿不到这条命中"
assert max(int(v) for v in n_hit[:7]) == ASHARE_BUY_MIN_HITS, \
    "前排的 n_hit 上限就该正好压在阈值上（=3）：构造不出 4，这是口径不是巧合"

# ④ 两道闸方向不反：域留 3 只，≥3 那道多留的正是「域剔、名单放行」那几只
keep_buy = pool & (n_hit < ASHARE_BUY_MIN_HITS)
assert sorted(c[-3:] for c, k in zip(codes, keep) if k) == ["004", "005", "006"]
assert sorted(c[-3:] for c, k in zip(codes, keep_buy) if k) == ["002", "003", "004",
                                                              "005", "006", "007",
                                                              "008", "009"]
# ⑤ 名单本身：并集口径下 x=2/x=3 连候选都不是；≥3 口径下 x=2 成为第 1 名
b_union, st_union = A.buy_candidates(s, mtx, rule_mats, pool, keep)
b_p3, st_p3 = A.buy_candidates(s, mtx, rule_mats, pool, keep_buy)
print("并集名单:", list(b_union.index), st_union["n_step1"])
print("≥3 名单 :", list(b_p3.index), st_p3["n_step1"])
assert "SH6000002" not in b_union.index and "SH6000003" not in b_union.index
assert b_p3.index[0] == "SH6000002"
assert set(b_union.index) < set(b_p3.index), "≥3 只能是并集的子集放宽，不能换人"
assert st_p3["n_step1"] - st_union["n_step1"] == 5          # x=2,3 与 level 那三只
# 名单顺序 = 轴升序 = 序号顺序（顺带证明 level 那三只确实排在尾巴上）
assert list(b_p3.index) == ["SH6000002", "SH6000003", "SH6000004", "SH6000005",
                            "SH6000006", "SH6000007", "SH6000008", "SH6000009"]
# ⑥ buy_min_hits=1 时必须逐位退回旧口径（开关有效，不是两份实现）
r1 = A.screen_on_date(s, None, mtx, rule_mats, rule_mats[A.RULE_EXPRS[0]],
                      with_buy=True, buy_min_hits=1)
assert list(r1["buy"].index) == list(b_union.index)
assert r1["buy_stats"]["n_consensus"] == int((pool & ~keep).sum()) == 7
r3 = A.screen_on_date(s, None, mtx, rule_mats, rule_mats[A.RULE_EXPRS[0]],
                      with_buy=True, buy_min_hits=ASHARE_BUY_MIN_HITS)
assert r3["buy_stats"]["n_consensus"] == 2                  # x=0,1 达到共识阈值
assert r3["buy_stats"]["n_lenient_vs_union"] == 5           # x=2,3 + level 那三只
assert list(r3["buy"].index) == list(b_p3.index)
# 明细表带着 n_hit 交给日频入口落 CSV（看板据此区分「几条一致判响」）
assert "n_hit" in r3["detail"].columns
print("buy_stats(≥3):", {k: r3["buy_stats"][k] for k in
                         ("buy_min_hits", "n_consensus", "n_lenient_vs_union",
                          "n_step1", "n_diff_vs_backtest")})
print("\n[P3 自检] 全部通过：n_hit 自洽、两道闸方向不反、level 只咬队列尾部、"
      "开关=1 逐位退回旧口径")
