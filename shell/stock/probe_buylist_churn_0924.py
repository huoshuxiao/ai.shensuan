# -*- coding: utf-8 -*-
"""P0 补测（09-24）：并集闸**多久咬一次待买入名单、一次咬几只**

组合层两张表已经给出「并集使短名单净收益 −1.4pp/年、单程换手 0.31→0.48」，但
−1.4pp 是「平均」，看不出分布：是每次都换掉七八只，还是极少发生、一发生就换掉
整批？这两种情形对「要不要把剔除闸从名单候选生成里摘掉」的含义完全不同，所以
在 569 个调仓日上逐个对账：

    churn_s = | top50(过闸池) \\ top50(过闸池 ∧ 保留池) |        被换掉的只数
    报告：命中日占比、平均只数、以及被换进来的那批的「安静度」名次分布

口径与 run_ashare_portfolio_eval.buylist_rows 严格一致：同一套闸门（含次日开盘
涨停前瞻）、同一个调仓网格（每 5 个真实交易日）、同一根排序轴 ts_std(volume,20)。
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src")
import _bootstrap  # noqa: F401,E402  (把 config/ strategy/ 与 common 内核挂上 sys.path)

from config import (ASHARE_PORT_HOLD, ASHARE_SCREEN_QUANTILE,
                    ASHARE_BUY_TOP_N, ASHARE_PORT_START)
from ashare_screen import (BUY_EXPR, RULE_EXPRS, VOLUME_RULES, build_matrices,
                           factor_matrices, load_panel)
from run_ashare_portfolio_eval import EXCL_VARIANTS, exclusion_mask, exclusion_hits

WANT = {"参照·不剔除", "并集(命中>=1)", "并集(命中>=2)", "并集(命中>=3)",
        "单条·量能水平", "单条·量能波动", "单条·量能动量", "单条·量能比"}

wide, _ = load_panel()
mtx = build_matrices(wide)
exprs = sorted(set(RULE_EXPRS) | {BUY_EXPR})
fms = factor_matrices(exprs, mtx)
live = mtx["ret_open"].index >= pd.Timestamp(ASHARE_PORT_START)
mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
fms = {e: m.loc[m.index[live]] for e, m in fms.items()}
days = mtx["ret_open"].index
pool, hit = exclusion_hits(mtx, days, fms, ASHARE_PORT_HOLD, ASHARE_SCREEN_QUANTILE)
quiet = fms[BUY_EXPR]
N = ASHARE_BUY_TOP_N

rows = []
for label, _kind, subs, k in EXCL_VARIANTS:
    if label not in WANT:
        continue
    allow = exclusion_mask((label, _kind, subs, k), pool, hit)
    n_hit, sizes = 0, []
    own, vs_ref, prev_b, prev_r = [], [], None, None
    for i in range(0, len(days) - ASHARE_PORT_HOLD - 1, ASHARE_PORT_HOLD):
        s = days[i]
        base = quiet.loc[s].where(pool.loc[s]).dropna().sort_values().index[:N]
        got = quiet.loc[s].where(pool.loc[s] & allow.loc[s]).dropna() \
                        .sort_values().index[:N]
        # own = 本形态相对自己上一期的换票率（与 run_signal 的 one_way_turnover 同式）
        if prev_b is not None:
            own.append(1.0 - len(set(prev_b) & set(got)) / N)
            vs_ref.append(1.0 - len(set(prev_r) & set(base)) / N)
        prev_b, prev_r = list(got), list(base)
        d = [c for c in got if c not in set(base)]
        if d:
            n_hit += 1
            sizes.append(len(d))
    rows.append({"形态": label, "调仓次数": i // ASHARE_PORT_HOLD + 1,
                 "名单被改动次数": n_hit,
                 "被改动占比": n_hit / (i // ASHARE_PORT_HOLD + 1),
                 "平均改动只数": float(np.mean(sizes)) if sizes else 0.0,
                 "单次最多改动": int(max(sizes)) if sizes else 0,
                 # 判决列：own 若与 ashare_portfolio_buylist.csv 的 one_way_turnover
                 # 对得上，−1.4pp 那条成本账就成立；对不上说明 allow 路径有 bug
                 "own换票率": float(np.mean(own)),
                 "参照own换票率": float(np.mean(vs_ref))})

r = pd.DataFrame(rows)
pd.set_option("display.unicode.east_asian_width", True)
print(f"\n===== 待买入 top{N} 名单在各剔除形态下的改动频次 =====")
print(r.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
