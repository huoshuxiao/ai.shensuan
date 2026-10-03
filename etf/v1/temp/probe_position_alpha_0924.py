# -*- coding: utf-8 -*-
"""一次性核查（09-24）：判重后第二环报出 趋势位置族 单因子净年化 +40~48%、超可投域
+21~36%/年，比此前"量能族无超额"的结论大反转。要么是真信号要么是算错了，拆开看：

  1) 集中度：187 次调仓里，累计对数超额有多少来自最大的那 20%
  2) 具体篮子：最好/最差的几次建仓买了谁、实现收益多少、篮子容量
  3) 分年：超额是不是只活在 2025-2026（近因段），2019-2021 有没有
  4) 成本敏感性：把单边成本从 6bp 提到 30bp，超额还剩多少（换手 ~0.8/次）

闸门一律直接调 EA.tradable_mask，不另写一份：复刻口径的探针证不了主口的数。
只读，不写产物。CWD 须为 etf/v1/src。
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                    "etf", "v1", "src")
sys.path.insert(0, _SRC)
import _bootstrap  # noqa: E402,F401

import numpy as np
import pandas as pd

import etf_admission as EA

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 30)

TARGETS = [t.strip() for t in os.environ.get(
    "TARGETS", "位置·20日Donchian,位置·距20日高,位置·C/MA20,量能·水平MA20").split(",") if t.strip()]
SPEC_JSON = os.environ.get("SPEC_JSON", "")
K = 10


def baskets(nm, fac, m, days, ew_uni, k=K):
    """按 topk_rebalance 同一套闸门摊开每次调仓的篮子与其持有期收益。

    基准用**同一段窗口**的 ew_uni 复利，不按日期查表：调仓日与索引时间戳只要差一个
    时分就会静默查成 NaN，把超额算成收益本身。
    """
    rows = []
    hold = EA.HOLD
    for i in range(0, len(days) - hold - 1, hold):
        s, d1 = days[i], days[i + 1]
        ok, n_block = EA.tradable_mask(m, s, d1)
        cand = fac.loc[s].where(ok).dropna()
        if len(cand) < max(1, k // 2):
            continue
        basket = list(cand.sort_values(ascending=False).index[:k])
        lo, hi = i + 1, min(i + 1 + hold, len(days))
        r = m["ret_open"].iloc[lo:hi][basket]
        # 篮子区间收益：各只先复利自己的 hold 天，再等权平均（与回放的逐日等权略差
        # hold 天内的权重漂移项，用于看集中度足够）
        basket_ret = float(((1 + r.fillna(0.0)).prod() - 1).mean())
        bench_ret = float((1 + ew_uni.iloc[lo:hi].fillna(0.0)).prod() - 1)
        rows.append({"signal": s.date(), "entry": d1.date(), "n": len(basket),
                     "n_block": n_block, "hold_ret": basket_ret,
                     "ex": basket_ret - bench_ret,
                     "amt_med": float(m["amount20"].loc[s, basket].median()),
                     "amt_min": float(m["amount20"].loc[s, basket].min()),
                     "basket": " ".join(basket)})
    return pd.DataFrame(rows)


def main():
    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    days = m["close"].index
    all_specs = list(EA.FAMILY_SPECS)
    if os.environ.get("SPEC_JSON"):
        import json
        with open(os.environ["SPEC_JSON"], encoding="utf-8") as f:
            all_specs += EA.specs_from_rows(json.load(f), family="外部")
    specs = [s for s in all_specs if EA.spec_name(s) in TARGETS]
    facs = {k: EA.slice_to_start(v, EA.EVAL_START)
            for k, v in EA.evaluate_factors(pool, specs).items()}
    # 翻向表：环 1 的 rank_ic_h10 决定"做多高分侧还是低分侧"，与环 2 同口径
    ev = pd.read_csv(EA.FACTOR_EVAL_OUT).set_index("name")
    ic_col = f"rank_ic_h{int(os.environ.get('PRIMARY_H', '10'))}"
    universe = (m["listed_days"] >= EA.MIN_LISTED) & (m["amount20"] >= EA.MIN_AMOUNT)
    ew_all, ew_uni = EA.equal_weight_benchmarks(m, universe=universe)

    for nm in TARGETS:
        if nm not in facs:
            print(f"[跳过] {nm} 既不在 FAMILY_SPECS 也不在 SPEC_JSON")
            continue
        print("\n" + "=" * 110)
        expr = [s["expr"] for s in specs if EA.spec_name(s) == nm][0]
        sgn = float(np.sign(ev.loc[nm, ic_col])) if nm in ev.index and ic_col in ev.columns else 1.0
        fac = facs[nm] * sgn
        print(f"{nm}  =  {expr}      （按环1 {ic_col} 翻向：×{sgn:+.0f}）")
        net, gross, st = EA.topk_rebalance(fac, m, days, k=K, hold=EA.HOLD,
                                           cost=EA.COST_ONE_WAY)
        print(f"[回放] 净年化 {net.mean()*252:+.2%} | 毛 {gross.mean()*252:+.2%} | 超可投域 "
              f"{EA.excess_stats(net, ew_uni, 'u')['excess_u_ann']:+.2%} | "
              f"夏普 {portfolio_sharpe(net):.2f} | 单次换手 {st['one_way_turnover']:.2f} | "
              f"被涨停挡 {st['blocked_limit_up']:.2f} 只/次")
        tr = baskets(nm, fac, m, days, ew_uni)
        tr = tr[np.isfinite(tr["ex"])].copy()
        tr["logex"] = np.log1p(tr["ex"])
        pos = tr.loc[tr["logex"] > 0, "logex"].sum()
        srt = tr["logex"].sort_values(ascending=False)
        top20 = max(1, int(np.ceil(len(srt) * 0.2)))
        print(f"[集中度] 调仓 {len(tr)} 次；正贡献 {pos:.2f} 对数点，其中最大 {top20} 次"
              f"（{top20/len(tr):.0%} 的调仓）占 {srt.iloc[:top20].sum()/pos:.0%}")
        print(f"[容量] 篮子 20 日均成交额：中位数全期 {tr['amt_med'].median()/1e8:.2f} 亿，"
              f"最薄一只的分期中位 {tr['amt_min'].median()/1e8:.2f} 亿，"
              f"最小 {tr['amt_min'].min()/1e8:.2f} 亿")
        print(f"[闸门] 每次调仓平均被涨停挡掉 {tr['n_block'].mean():.2f} 只，"
              f"最多 {tr['n_block'].max()} 只（挡掉后从次优里补，未记成交不了的钱）")
        print("[分年] 次数 / 累计对数超额 / 胜率 / 该年篮子最薄容量(亿)")
        g = tr.assign(year=pd.to_datetime(tr["entry"]).dt.year).groupby("year").agg(
            n=("logex", "size"), sum_logex=("logex", "sum"),
            win=("logex", lambda s: float((s > 0).mean())),
            amt_min=("amt_min", "min"))
        g["amt_min"] = g["amt_min"] / 1e8
        print(g.round(3).to_string())
        print("[最好 5 次]")
        print(tr.sort_values("logex", ascending=False)
              .head(5)[["signal", "entry", "hold_ret", "ex", "amt_med", "basket"]]
              .to_string(index=False))
        print("[最差 3 次]")
        print(tr.sort_values("logex").head(3)
              [["signal", "entry", "hold_ret", "ex", "basket"]].to_string(index=False))
        print("[成本敏感性] 同一篮子序列，单边成本 c → 净年化 / 超可投域")
        for c in (0.0006, 0.0015, 0.003, 0.006):
            n2, g2, s2 = EA.topk_rebalance(fac, m, days, k=K, hold=EA.HOLD, cost=c)
            ex = EA.excess_stats(n2, ew_uni, "u")["excess_u_ann"]
            print(f"    c={c:.4%}  净年化 {n2.mean()*252:+.2%}  超可投域 {ex:+.2%}  "
                  f"夏普 {portfolio_sharpe(n2):.2f}")


def portfolio_sharpe(x):
    p = pd.Series(x).dropna()
    return float(p.mean() * 252 / (p.std() * np.sqrt(252))) if p.std() > 0 else np.nan


if __name__ == "__main__":
    main()
