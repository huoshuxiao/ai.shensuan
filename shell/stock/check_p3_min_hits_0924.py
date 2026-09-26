# -*- coding: utf-8 -*-
"""P3 落地的判据自检（09-24）：同一批构造、两个强度，各自挡谁

P3 改的是「一套剔除判据在两个位置用两个阈值」：

    n_hit_i(s) = Σ_rule 1[ pct_i(rule, s) >= ASHARE_SCREEN_QUANTILE ]
    域（不该买）   keep_i     = pool_i ∧ n_hit_i >= 1              并集
    待买入入口     keep_buy_i = pool_i ∧ n_hit_i <  ASHARE_BUY_MIN_HITS

两条都用，是因为 ⑮P0 实测它们各自是各自腿上的最优（并集在减法腿 +6.42%/年，
压在待买入名单上却 -0.42%）。风险也正好在这里：**一个函数交回两个布尔，最容易写反**
（把 keep 传给名单 = 回到那个已知的净负口径；把 keep_buy 传给域 = 悄悄放宽「不该买」）。
所以用合成数据把方向钉死，而不是等真数据跑出来再用眼睛比。

合成数据刻意让 10 只票的命中数排成 4,4,3,2,1,0…，各判一件事：
    x=0,1  四条构造全判响      ⇒ 两道闸都挡（真·共识爆量）
    x=2    三条判响            ⇒ 恰好落在阈值上，两道闸都挡（>=3 含 3）
    x=3    两条判响            ⇒ 域挡、名单放行
    x=4    只有量能水平判响    ⇒ 域挡、名单放行（单条命中的那一角）
    x=5..9 无人判响            ⇒ 两处都放行
排序轴（安静度）按代码序号单调，名单顺序就等于序号顺序，好核对。
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src")
import _bootstrap  # noqa: F401,E402

import ashare_screen as A
from config import ASHARE_BUY_MIN_HITS, ASHARE_SCREEN_QUANTILE

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
# 四条构造各自「判响」的下标集合，按 VOLUME_RULES 顺序 = 水平/波动/动量/比
HOT = ([0, 1, 2, 3, 4], [0, 1, 2, 3], [0, 1, 2], [0, 1])
rule_mats = {}
for e, hot in zip(A.RULE_EXPRS, HOT):
    d = pd.DataFrame(0.5, index=days, columns=codes, dtype="float32")
    d.loc[s, [codes[i] for i in hot]] = np.float32(0.99)
    rule_mats[e] = d
rule_mats[A.BUY_EXPR] = pd.DataFrame(
    np.tile(np.arange(10, dtype="float32")[None, :], (30, 1)),
    index=days, columns=codes)

keep, detail = A.volume_exclusion(rule_mats, s, pool)
n_hit = detail["n_hit"]
print(f"阈值 quantile={ASHARE_SCREEN_QUANTILE}  buy_min_hits={ASHARE_BUY_MIN_HITS}")
print("n_hit   :", {c[-3:]: int(v) for c, v in n_hit.items()})
print("域 keep :", sorted(c[-3:] for c, k in zip(codes, keep) if k))

# ① n_hit 与 excluded_by 必须自洽：命中条数 = 明细里那几个构造名（分号段数）
assert (n_hit.to_numpy() == np.where(detail["excluded_by"] == "", 0,
                                     detail["excluded_by"].str.count(";").to_numpy() + 1)
        ).all(), "n_hit 与 excluded_by 不一致"
assert [int(v) for v in n_hit] == [4, 4, 3, 2, 1, 0, 0, 0, 0, 0]
# ② 并集口径没被 P3 改动：n_hit>=1 ⇔ 被剔
assert (n_hit >= 1).to_numpy().tolist() == (~keep).to_numpy().tolist()
# ③ 两道闸方向不反：域留 5 只，≥3 那道留 7 只，且多出来的正是 x=3、x=4
keep_buy = pool & (n_hit < ASHARE_BUY_MIN_HITS)
assert sorted(c[-3:] for c, k in zip(codes, keep) if k) == ["005", "006", "007", "008", "009"]
assert sorted(c[-3:] for c, k in zip(codes, keep_buy) if k) == ["003", "004", "005",
                                                              "006", "007", "008", "009"]
# ④ 名单本身：并集口径下 x=3/x=4 连候选都不是；≥3 口径下 x=3 成为第 1 名
b_union, st_union = A.buy_candidates(s, mtx, rule_mats, pool, keep)
b_p3, st_p3 = A.buy_candidates(s, mtx, rule_mats, pool, keep_buy)
print("并集名单:", list(b_union.index), st_union["n_step1"])
print("≥3 名单 :", list(b_p3.index), st_p3["n_step1"])
assert "SH6000004" not in b_union.index and b_p3.index[0] == "SH6000003"
assert set(b_union.index) < set(b_p3.index), "≥3 只能是并集的子集放宽，不能换人"
assert st_p3["n_step1"] - st_union["n_step1"] == 2
# ⑤ buy_min_hits=1 时必须逐位退回旧口径（开关有效，不是两份实现）
r1 = A.screen_on_date(s, None, mtx, rule_mats, rule_mats[A.RULE_EXPRS[0]],
                      with_buy=True, buy_min_hits=1)
assert list(r1["buy"].index) == list(b_union.index)
assert r1["buy_stats"]["n_consensus"] == int((pool & ~keep).sum()) == 5
r3 = A.screen_on_date(s, None, mtx, rule_mats, rule_mats[A.RULE_EXPRS[0]],
                      with_buy=True, buy_min_hits=ASHARE_BUY_MIN_HITS)
assert r3["buy_stats"]["n_consensus"] == 3            # x=0,1,2 达到共识阈值
assert r3["buy_stats"]["n_lenient_vs_union"] == 2     # x=3,4：域剔、名单放行
assert list(r3["buy"].index) == list(b_p3.index)
# 明细表带着 n_hit 交给日频入口落 CSV（看板据此区分「几条一致判响」）
assert "n_hit" in r3["detail"].columns
print("buy_stats(≥3):", {k: r3["buy_stats"][k] for k in
                         ("buy_min_hits", "n_consensus", "n_lenient_vs_union",
                          "n_step1", "n_diff_vs_backtest")})
print("\n[P3 自检] 全部通过：n_hit 自洽、两道闸方向不反、开关=1 逐位退回旧口径")
