# -*- coding: utf-8 -*-
"""#17 落地体检：规模面板能读多少、清盘线露出多少只、开规模闸要付多少代价。纯只读。

采集器（`etf/v1/src/data/fetch_etf_risk_panel.py`）把份额与净值攒下来之后，接着要
回答三个问题，全都不能用"看起来合理"来答：

  1. **可读性**：份额面板覆盖了多少 date×code 格子？沪市只有月末快照，靠 ffill 撑，
     撑多少天算诚实？覆盖不足的话规模闸就是个装饰。
  2. **清盘线暴露**：按合同条款（连续 60 个交易日资产净值 <5000 万）历史上有多少只
     真的越线、现在池子里还有几只压在线上。
  3. **开闸代价**：若在可投域上再加一道"规模 >= X 亿"，日均过闸只数掉多少、
     掉的那部分是不是成交额闸门已经剔过的（重复信息就不值得再设一道闸）。

判据一律调 `etf_admission` 里的那一份实现，本脚本不重写公式 —— 探针和裁判口径
分叉是股票线用一次返工换来的教训。

跑法：仓库根 `/usr/bin/python3.10 -u shell/probe_etf_scale_gate_0924.py`
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "etf", "v1", "src"))

import etf_admission as EA        # noqa: E402
import fetch_etf_risk_panel as RP  # noqa: E402


def _cells(b):
    """布尔宽表里 True 的格子数（DataFrame.sum() 默认按列加，忘了 .sum() 第二次就
    拿到一个 Series，格式化时才炸）。"""
    return int(b.to_numpy().sum())


def main():
    t0 = time.time()
    print("=" * 96)
    print("#17 规模面板体检 · 只读")
    print("=" * 96)
    for k, v in EA.run_params().items():
        print(f"  {k:14s} = {v}")

    pool = EA.load_pool()
    m = EA.take_window(EA.build_matrices(pool))
    del pool
    print(f"\n[1] 覆盖率：份额 ffill 给多少天，规模就读得开多少格子")
    for ff in (1, 5, 10, 22, 30, 60):
        EA.SCALE_COVERAGE["share_dates"] = 0        # 强制重算回执
        aum = EA.load_scale_matrix(m, ffill=ff, verbose=False)
        want = m["close"].notna()
        print(f"  ffill={ff:>3d} 交易日：可读格子 {_cells(aum.notna() & want) / _cells(want):5.1%}"
              f"、末日可读 {int(aum.iloc[-1].notna().sum()):>4d} 只"
              f"（其中过闸 {(aum.iloc[-1].notna() & EA.universe_mask(m).iloc[-1]).sum():>4d} 只）")
    aum = EA.load_scale_matrix(m, verbose=True)

    print(f"\n[1b] 最近 5 个日期的三表对齐（份额披露 vs 日线镜像，谁停在哪天）")
    sh_all = RP.load_shares_matrix()
    for d in m["close"].index[-5:]:
        n_sh = int(sh_all.loc[d].notna().sum()) if d in sh_all.index else 0
        print(f"   {d.date()}：有行情 {int(m['close'].loc[d].notna().sum()):>4d} 只"
              f"｜当日有份额披露 {n_sh:>4d} 只｜ffill 后规模可读 {int(aum.loc[d].notna().sum()):>4d} 只")
    last_close = m["close"].apply(lambda s: s.last_valid_index())
    for tag, pfx in (("沪市(5x/6x)", ("5", "6")), ("深市(1x/0x)", ("1", "0"))):
        sub = last_close[[c for c in last_close.index if c.startswith(pfx)]].dropna()
        print(f"   日线镜像最后有行情的日子 · {tag}：中位 "
              f"{sub.median().date()}、最早 {sub.min().date()}、最晚 {sub.max().date()}"
              f"（{len(sub)} 只有过行情）")
    print("   ⇒ 末日规模可读性差的主要是**市场间镜像落后一天**，不是面板失效；"
          "所以末行交集会明显小于全期占比，看闸门的代价要看 [5] 的全期日均")

    print(f"\n[2] 规模分布（份额×收盘代理，实测 P5~P95 与真净值差 <1.3%）")
    # 分布用"每只最近一次可读的规模"，不用末日截面：末日恰好撞上市场间落后一天，
    # 用末行会把整个深市读成"规模未知"（见 [1b]）
    cur = aum.apply(lambda s: s.dropna().iloc[-1] / 1e8 if s.notna().any() else np.nan).dropna()
    q = cur.quantile([0, .05, .1, .25, .5, .75, 1])
    print(f"   每只最近可读规模（{len(cur)} 只可读）：" + "  ".join(
        f"{p:.0%}:{v:.2f}亿" for p, v in q.items()))
    hist = aum.stack() / 1e8
    print("   全历史格子：" + "  ".join(f"{p:.0%}:{v:.2f}亿" for p, v in
                                    hist.quantile([0, .05, .25, .5]).items()))

    print(f"\n[3] 清盘线条款暴露（连续 {EA.CLEAR_DAYS} 日 规模 < {EA.CLEAR_LINE / 1e8:.1f} 亿）")
    st = EA.clearing_streak(m)
    hit = st >= EA.CLEAR_DAYS
    n_days = int((hit.any(axis=1)).sum())
    print(f"   越线（曾在某日已连续 60 日低于线）：{int(hit.values.any(axis=0).sum())} 只"
          f"、涉及 {n_days} 个日期")
    still = cur[cur < EA.CLEAR_LINE / 1e8]
    ever_hit = hit.any(axis=0).reindex(still.index, fill_value=False)
    print(f"   最近可读规模仍在线下的：{len(still)} 只"
          f"，其中已连续越线的 {int(ever_hit.sum())} 只（名单：{list(still.index[ever_hit.values])[:12]}）")
    print(f"   连续天数的分布（全历史所有格子）：中位 {st.stack().median():.0f} 日、"
          f"P90 {st.stack().quantile(.9):.0f} 日、最长 {int(st.values.max())} 日")
    ever = st.max()
    worst = ever.sort_values(ascending=False).head(12)
    last_valid = aum.apply(lambda s: s.dropna().iloc[-1] / 1e8 if s.notna().any() else np.nan)
    last_valid_date = aum.apply(lambda s: s.last_valid_index().date() if s.notna().any() else None)
    print("   连续低规模天数最长的 12 只（含 ffill 撑长的披露节奏效应）：")
    for c, v in worst.items():
        if v <= 0:
            continue
        print(f"     {c}  最长连续 {int(v):>3d} 日 <{EA.CLEAR_LINE / 1e8:.1f} 亿"
              f"、最近可读规模 {last_valid.get(c, np.nan):.2f} 亿"
              f"（读数日 {last_valid_date.get(c)}）"
              f"、20日均额 {m['amount20'].iloc[-1].get(c, np.nan) / 1e8:.2f} 亿")

    print(f"\n[4] 折溢价（需净值，只能从 {RP.read_long(RP.NAV_THS_OUT)['date'].min().date()}"
          f" 起读；本机无净值历史，这是攒出来的）")
    prem = EA.premium_matrix(m)
    for d in prem.index[-4:]:
        row = prem.loc[d].dropna()
        if len(row) < 10:
            continue
        print(f"   {d.date()}：可配 {len(row):>4d} 只 中位 {row.median():+.2%}"
              f" P5 {row.quantile(.05):+.2%} P95 {row.quantile(.95):+.2%}"
              f"、|·|>1% 占 {(row.abs() > .01).mean():.1%}")

    print(f"\n[5] 开规模闸的代价（判据：在既有四道闸之上再加 规模 >= X 亿）")
    base = EA.universe_mask(m)
    amt_all = m["amount20"][base].stack()
    print(f"   基线（四道闸）：末日 {int(base.iloc[-1].sum())} 只、全期日均 {base.sum(axis=1).mean():.1f} 只"
          f"｜过闸格子的 20 日均额中位 {amt_all.median() / 1e8:.2f} 亿")
    for x in (0.5e8, 1e8, 2e8, 5e8, 10e8):
        keep = base & (aum.isna() | (aum >= x))
        lost = base & ~(aum.isna() | (aum >= x))
        daily = keep.sum(axis=1)
        # 被剔格子按构造**全部已经通过**成交额两道闸（它们就在 base 里），所以这里
        # 问的不是"两道闸重复剔了吗"，而是"规模闸额外剔掉的这批有多薄"：中位越贴近
        # MIN_AMOUNT ⇒ 越像成交额闸的复读，越远离 ⇒ 越是独立信息
        cells = m["amount20"][lost].stack()
        print(f"   规模>={x / 1e8:>4.1f} 亿：末日 {int(keep.iloc[-1].sum()):>4d} 只"
              f"（剔 {int(lost.iloc[-1].sum()):>3d}）、全期日均 {daily.mean():>6.1f} 只"
              f"（-{base.sum(axis=1).mean() - daily.mean():.1f}）"
              f"｜被剔格子 {int(lost.values.sum()):>6d}（涉及 {int(lost.any(axis=0).sum())} 只标的）"
              f"、这批格子自己的 20 日均额中位 "
              + ("—" if cells.empty else
                 f"{cells.median() / 1e8:.2f} 亿、P90 {cells.quantile(.9) / 1e8:.2f} 亿"))
    P, S = EA.cs_corr_mean({"aum": aum, "amt20": m["amount20"]}, min_cs=EA.MIN_CS,
                           verbose=False)
    print(f"   规模 vs 20 日均成交额 的逐日截面相关均值："
          f"Pearson {P.loc['aum', 'amt20']:+.3f}｜Spearman {S.loc['aum', 'amt20']:+.3f}")
    print(f"   要看的是这两个数：越接近 1，规模闸就越只是成交额闸的复读")
    print(f"\n[耗时] {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
