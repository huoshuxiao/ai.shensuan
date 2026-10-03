# -*- coding: utf-8 -*-
"""T3a 离线量**判据/名单类**人工阈值：只吃日更已经落盘的产物，不读面板、不覆写任何生产件。

能离线量的依据（先验证再用）：`daily_signal/signal_*.csv` 存了四条量能构造在**过闸池内**
的截面分位 pct（0~1），而 `SCREEN_QUANTILE` 只是这根 pct 上的一个切点、
`BUY_MIN_HITS` 只是「几条构造同时切过」的计数门槛 ⇒ **池本身不随这两个数变**
（池由容量/次新/涨停三道闸决定，与它们无关）。所以逐档重切 = 在已有 pct 列上比大小，
一行判据都不用重跑。

量的四根旋钮（全 B 类：拍过但没逐档量过）：
  ASHARE_SCREEN_QUANTILE=0.80   域那把刀的刀口（剔除并集）
  ASHARE_BUY_MIN_HITS=3         待买入名单入口的共识门槛
  ASHARE_ORDER_TOP_N=5          下单席位
  ASHARE_ORDER_MAX_PER_BOARD=3 / ASHARE_ORDER_MAX_PER_INDUSTRY=1   下单层两条分散约束

判据不恒真：
  ① 生产锚点：按 meta 里记的 quantile/min_hits 复算，dropped/keep/n_consensus 三个数
     必须与 meta **逐位相同**，对不上说明我复算的不是生产那把尺 ⇒ 当场红、读数作废。
  ② 有牙检查：刀口 1.0 必须把全池剔光、门槛 1 必须与并集口径等价、席位 0 必须凑不满。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SIG = os.path.join(REPO, "stock", "v1", "data", "results", "daily_signal")
sys.path.insert(0, os.path.join(REPO, "stock", "v1", "src"))
import _bootstrap  # noqa: E402,F401
import config as C  # noqa: E402
from strategy import ashare_screen as S  # noqa: E402

RULES = ["量能水平", "量能波动", "量能动量", "量能比"]
FAIL = []


def block(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


sessions = sorted(f[7:15] for f in os.listdir(SIG)
                  if f.startswith("signal_") and f.endswith(".csv")
                  and os.path.isfile(os.path.join(SIG, f"meta_{f[7:15]}.json")))
print(f"现读常量：SCREEN_QUANTILE={C.ASHARE_SCREEN_QUANTILE} BUY_MIN_HITS={C.ASHARE_BUY_MIN_HITS} "
      f"BUY_TOP_N={C.ASHARE_BUY_TOP_N} ORDER_TOP_N={C.ASHARE_ORDER_TOP_N} "
      f"PER_BOARD={C.ASHARE_ORDER_MAX_PER_BOARD} PER_INDUSTRY={C.ASHARE_ORDER_MAX_PER_INDUSTRY}", flush=True)
print(f"可用场次（signal_YYYYMMDD.csv）：{'、'.join(sessions)}　样本 n={len(sessions)}", flush=True)

block("① 生产锚点：按 meta 记的参数复算，三个数必须逐位相同")
data = {}
for d in sessions:
    df = pd.read_csv(os.path.join(SIG, f"signal_{d}.csv"))
    meta = json.load(open(os.path.join(SIG, f"meta_{d}.json")))
    P = df[RULES].to_numpy(dtype="float64", copy=True)   # NaN = 该条当日无值，不计入
    hits = np.where(np.isnan(P), False, P >= meta["quantile"])
    n_hit = hits.sum(axis=1)
    keep = n_hit < 1
    dropped = int((~keep).sum())
    keep_buy = n_hit < meta["buy_min_hits"]
    got = {"universe": int(len(df)), "dropped": dropped, "keep": int(keep.sum()),
           "n_consensus": int((~keep_buy).sum())}
    want = {"universe": meta["universe"], "dropped": meta["dropped"], "keep": meta["keep"],
            "n_consensus": meta["buy"]["n_consensus"]}
    ok = got == want
    # 逐条构造的命中数也一起对表：那是 4 个独立的数，比只看并集更能证明
    # 「我读的 pct 列就是生产判据吃的那一列」
    meta_fired = {r["name"]: int(r["fired"]) for r in meta["screen_rules"]}
    mine_fired = {nm: int(hits[:, k].sum()) for k, nm in enumerate(RULES)}
    ok_fired = mine_fired == meta_fired
    print(f"  {d}：复算 {got}")
    print(f"  {'  ' if ok else '  '}meta  {want}　{'✅ 逐位相同' if ok else '❌ 对不上'}")
    print(f"  逐条命中 复算 {mine_fired}")
    print(f"  {'  ' if ok_fired else '  '}meta  {meta_fired}　{'✅ 逐位相同' if ok_fired else '❌ 对不上'}")
    if not ok:
        FAIL.append(f"① {d} 复算与 meta 不一致 ⇒ 我量的不是生产那把尺，本场全部读数作废")
    if not ok_fired:
        FAIL.append(f"① {d} 逐条构造命中数与 meta 不一致 ⇒ pct 列与判据不同源，档位表作废")
    data[d] = (df, meta, n_hit, keep_buy)

block("② 有牙检查：极端档必须给出极端结果（不给就说明我在自欺）")
d = sessions[-1]
df, meta, n_hit, _ = data[d]
P = df[RULES].to_numpy(dtype="float64")
N_UNIV = len(df)
# 分位 pct = 池内名次/池内只数 ⇒ 刀口越高剔得越少：0.0 全剔、>1.0 一只不剩。
# 第一版把方向写反（以为 1.0 会剔光），当场被自己的读数打回来，已按语义改正。
for q, exp, want in ((0.0, "全员 ≥0 ⇒ 全池剔光", N_UNIV),
                     (1.01, "没有分位能超过 1 ⇒ 一只不剔", 0),
                     (1.0, "只有每条构造的最大值恰好 =1.0", None)):
    h = np.where(np.isnan(P), False, P >= q)
    n_drop = int((h.sum(axis=1) >= 1).sum())
    print(f"  刀口 {q}（{exp}）⇒ 剔 {n_drop:,} / {N_UNIV:,} 只")
    if want is not None and n_drop != want:
        FAIL.append(f"② 刀口 {q} 应剔 {want:,} 只，实际 {n_drop:,} ⇒ 尺子无牙")
    if want is None and not (0 < n_drop <= len(RULES)):
        FAIL.append(f"② 刀口 1.0 应该只剔到每条构造的最大值（1~{len(RULES)} 只），实际 {n_drop:,} ⇒ 尺子无牙")
kept_prev = None
for mh in (1, 2, 3, 4, 5):
    kept = int((n_hit < mh).sum())
    print(f"  门槛 {mh}（{'=并集口径' if mh == 1 else '关掉共识闸' if mh == 5 else f'命中≥{mh} 条才挡'}）"
          f"⇒ 入口留 {kept:,} 只")
    if kept_prev is not None and kept < kept_prev:
        FAIL.append(f"② 门槛从 {mh - 1} 抬到 {mh} 反而留更少 ⇒ 计数写反了")
    kept_prev = kept
if int((n_hit < 5).sum()) != N_UNIV:
    FAIL.append("② 门槛 5（没人能同时被 5 条判响）应该等于放过整池，"
                f"实际 {int((n_hit < 5).sum()):,} != {N_UNIV:,} ⇒ n_hit 的上界不是 4")
# 锚点有没有牙：故意拿错刀口（0.75）复算同一场，必须与 meta 对不上。
# 若错了也能对上，说明①是恒真读数，整张表没有证据力。
wrong = int((np.where(np.isnan(P), False, P >= 0.75).sum(axis=1) >= 1).sum())
print(f"  锚点的牙：故意用 0.75 复算 {d} ⇒ 剔 {wrong:,} 只 vs meta {meta['dropped']:,} 只"
      f"（必须不等）")
if wrong == meta["dropped"]:
    FAIL.append("② 反证失败：换刀口竟然还能对上 meta ⇒ ① 那三个锚点是恒真读数，不作证据")

block("③ SCREEN_QUANTILE 逐档：域被剔掉多少（n=%d 场，场平均）" % len(sessions))
print("  刀口    每场剔掉(只)   剔掉占比   域还剩(只)   每条构造各剔几只")
ALL_P = {dd: data[dd][0][RULES].to_numpy(dtype="float64") for dd in sessions}
NU = np.mean([len(ALL_P[dd]) for dd in sessions])
prev_drop = None
for q in (0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
    drops, per = [], []
    for dd in sessions:
        Pq = ALL_P[dd]
        h = np.where(np.isnan(Pq), False, Pq >= q)
        drops.append(int((h.sum(axis=1) >= 1).sum()))
        per.append(h.sum(axis=0))
    tag = "  ←生产" if abs(q - 0.80) < 1e-9 else ""
    print(f"  {q:.2f}   {np.mean(drops):>10,.0f}   {np.mean(drops) / NU:>8.1%}"
          f"   {NU - np.mean(drops):>10,.0f}   "
          + "/".join(f"{v:,.0f}" for v in np.mean(per, axis=0)) + tag)
    if prev_drop is not None and np.mean(drops) > prev_drop:
        FAIL.append(f"③ 刀口从上一档抬到 {q} 反而剔更多 ⇒ 方向反了")
    prev_drop = np.mean(drops)

block("④ BUY_MIN_HITS 逐档：待买入入口放行多少（门槛越高放得越多）")
for mh in (1, 2, 3, 4, 5):
    vals = [int((data[dd][2] < mh).sum()) for dd in sessions]
    tag = "  ←生产" if mh == 3 else ("  (=关掉共识闸)" if mh >= 5 else "")
    print(f"  ≥{mh} 条一致判响才挡：入口留 {np.mean(vals):,.0f} 只/场"
          f"（被挡 {NU - np.mean(vals):,.0f}）{tag}")

block("⑤ 两根旋钮的**交互**：刀口一变，n_hit 就变，共识门槛跟着松/紧")
print("  行=刀口 列=共识门槛（格子=入口留几只/场）")
print("   q\\mh " + "".join(f"{m:>8}" for m in (1, 2, 3, 4)))
for q in (0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
    row = []
    for mh in (1, 2, 3, 4):
        v = []
        for dd in sessions:
            Pq = ALL_P[dd]
            h = np.where(np.isnan(Pq), False, Pq >= q).sum(axis=1)
            v.append(int((h < mh).sum()))
        row.append(np.mean(v))
    tag = "  ←生产" if abs(q - 0.80) < 1e-9 else ""
    print(f"   {q:.2f} " + "".join(f"{x:>8,.0f}" for x in row) + tag)
print("  读法：刀口抬高一档 = 每条构造少剔一批 = 同时命中几条的票变少 = 共识闸自动变松，"
      "所以这两根旋钮**不是独立的**，拨一根会连带挪另一根的实际强度")

block("⑥ 板块配额（ASHARE_LIST_QUOTA）：吃 09-25 那份 570 场逐日盘点")
MIX = os.path.join(REPO, "stock", "v1", "data", "results", "list_board_mix_daily_0925b.csv")
mix = pd.read_csv(MIX)
BOARDS = ["主板", "创业板", "科创板", "北交所"]
print(f"  产物 {os.path.basename(MIX)}：{len(mix)} 个调仓日（{mix.date.iloc[0]} ~ {mix.date.iloc[-1]}）"
      f"　⚠️ 场次版本 = 09-25 那次回放，与今天生产同口径（gate=board / scheme=quota）但差 4 天，"
      f"读数是**形状**不是收益")
for b in BOARDS:
    sup, lst = mix[f"池_{b}"], mix[f"名单_{b}"]
    seat = C.ASHARE_LIST_QUOTA.get(b, 0)
    print(f"  {b}：席位 {seat:>3}｜池内日均 {sup.mean():>7,.0f} 只"
          f"｜名单实际占席 日均 {lst.mean():>5.2f} 只（最多 {lst.max()}、最少 {lst.min()}）"
          f"｜凑满席位的场次 {(lst >= seat).mean():.1%}"
          f"｜零席位场次 {(lst == 0).mean():.1%}")
print(f"  四段合计：名单日均 {mix[[f'名单_{b}' for b in BOARDS]].sum(axis=1).mean():.2f} 只"
      f"（BUY_TOP_N={C.ASHARE_BUY_TOP_N}）｜段间池占比 "
      + " ".join(f"{b}{mix[f'池占比_{b}'].mean():.0%}" for b in BOARDS))
print("  ⇒ 上面「凑满席位的场次」一列就是配额的牙：哪一段常年拿不满，席位就是写在纸上的")

block("⑦ 下单层三根旋钮：拿真实名单重切（`order_candidates` 生产函数本身）")
industry, imeta = S.load_industry_map()
buy = pd.read_csv(os.path.join(SIG, f"buy_{sessions[-1]}.csv"), index_col="code")
print(f"  名单 {sessions[-1]}：{len(buy)} 只，板块构成 "
      + "/".join(f"{k}{v}" for k, v in buy["板块"].value_counts().items())
      + f"｜行业映射在场 {imeta['n']} 只、已 {imeta['age_days']} 天没刷（stale={imeta['stale']}）")
for top_n in (3, 5, 10):
    for cap_b in (2, 3, 4):
        for cap_i in (1, 2):
            picked, st = S.order_candidates(buy, industry=industry, top_n=top_n,
                                           max_per_industry=cap_i, max_per_board=cap_b)
            n = st["n_picked"]
            tag = "  ←生产" if (top_n, cap_b, cap_i) == (5, 3, 1) else ""
            if tag:
                # 锚点：生产档位必须复现落盘的 order_20260928.csv 那 5 只、顺序一致
                ref = pd.read_csv(os.path.join(SIG, f"order_{sessions[-1]}.csv"), index_col="code")
                same = list(picked.index) == list(ref.index)
                print(f"  锚点（生产档 5/3/1）复现落盘 order_{sessions[-1]}.csv："
                      f"{'✅ 逐位相同' if same else '❌ 对不上 ' + str(list(picked.index)) + ' vs ' + str(list(ref.index))}")
                if not same:
                    FAIL.append("⑦ 生产档位复现不出落盘的下单表 ⇒ 我切的不是那一层")
            print(f"  席位{top_n:>2} 板块≤{cap_b} 行业≤{cap_i} ⇒ 实挑 {n:>2} 只"
                  f"（凑不满 {top_n - n}）｜满仓给权每只 {st['weight_each']:.0%}"
                  f"｜扫到第 {st['scan_depth']} 名才凑够｜未知行业 {st['n_unknown']} 只{tag}")

block("⑧ 名单长度（ASHARE_BUY_TOP_N）的扣费账单：直接引在库归档，不重跑")
EV = os.path.join(REPO, "stock", "v1", "data", "results", "ashare_portfolio_eval.csv")
ev = pd.read_csv(EV)
row = ev[(ev.signal.astype(str) == "量能水平 SMA(Volume,20)")][["top_n", "excess_univ_ew_ann",
                                                                "one_way_turnover", "max_drawdown"]]
print(f"  来源 {os.path.basename(EV)}（mtime 09-27 16:27，面板 09-28/09-29 重跑数据层逐格未变）")
for _n, ex, to, dd in row.itertuples(index=False):
    tag = "  ←生产" if _n == 50 else ""
    print(f"  名单长度 {_n:>4} 只：扣费后年化超额 {ex:+.2%}｜单边换手 {to:.3f}｜最深回撤 {dd:.1%}{tag}")
if len(row) != 3:
    FAIL.append(f"⑧ 归档里 SMA(Volume,20) 应该有 50/100/200 三档，实读 {len(row)} 行 ⇒ 档位表不完整")
print("  ⚠️ 这三档是**名单腿**（不掏钱的观察名单整体），不是 5 席下单层；"
      "把名单压到 ≤10 只的另一笔账在 `order_layer_5_to_10_0925.csv`（09-25 量过，本轮未重跑）")

block("汇总")
if FAIL:
    print("  ❌ 未过：")
    for f in FAIL:
        print("    -", f)
    sys.exit(1)
print("  ✅ 生产锚点逐位复现 + 极端档有牙 + 三根旋钮的档位表已出")
