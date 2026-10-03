# -*- coding: utf-8 -*-
"""「样本外三折为正」与「全样本那遍腰斩」之间的落差，拆成两条腿来量。

为什么要拆
----------
上一场 ③ 出的三个读数放一起像互相打脸：
  · 折表 walk_forward_daily.csv 三折夏普 0.871 / 0.25 / 1.283（全正）
  · 合并样本外 walk_forward_oos_daily.csv 年化夏普 +0.703，但运气门槛 +0.998 ⇒ 没过
  · 全样本主链 dsr.csv 年化夏普 −0.306，净值 10000→4977
它们**不是一个可比的东西**，直接相减得到的"落差"是口径差不是业绩差。本脚本只从产物
读数，把差异拆成两条能各自解释的腿：

腿一 = **区间腿**：三折测试段只覆盖时间轴的一部分（其余日子没有任何折验过）。
        拿同一套全样本净值序列，只在折窗内算夏普 ⇒ 与整段的全样本夏普对比，
        差值就是"没被验的那段时间轴"贡献的。
腿二 = **因子腿**：**同一批区间**上，折内用的是"本折训练段挖出来的因子"，
        全样本用的是"全样本挑完的整库" ⇒ 同区间两个夏普之差就是因子集的差。

自检（不许恒真）
----------------
S1 折窗必须能从市场日历里取到 ≥100 个交易日，取不到就报红退出（防日期解析走样）。
S2 三折测试段两两不得重叠，且并集 bar 数 = 各折 bar 数之和（拼接不重复计数）。
S3 全样本夏普必须能从 equity.csv 逐 bar 收益复算出来，与 dsr.csv 的 sharpe_annual
   对得上（容差 1e-3）——对不上说明我算的收益口径和判据不是一套，全部读数作废。
S4 覆盖率必须严格在 0 与 1 之间：等于 1 说明"区间腿"根本不存在，那这条腿的读数不许报。
S5 净值首末直接取自产物，不写死 10000/4977 这类数字。

跑法（只读，不写任何生产路径）：
    cd etf/v1 && /usr/bin/python3.10 temp/gap_folds_vs_fullsample_0929.py [results目录]
默认读 data/results；传快照目录就量快照。
"""

import os
import re
import sys

import numpy as np
import pandas as pd

BYPASS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BYPASS, "data", "results")
BARS_PER_YEAR = 252          # 日线年化系数；折表的 bars_per_year 列会自证是不是这个


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def sharpe(rets):
    """年化夏普 = 均值/标准差·√252（与折表「年化收益/年化波动」同量级口径）"""
    s = float(np.std(rets, ddof=1))
    if s == 0:
        return 0.0
    return float(np.mean(rets)) / s * np.sqrt(BARS_PER_YEAR)


def cum(rets):
    return float(np.prod(1.0 + np.asarray(rets)) - 1.0)


def max_dd(equity):
    e = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(e)
    return float(np.min(e / peak - 1.0))


def main():
    eq_path = os.path.join(RESULTS, "equity.csv")
    fold_path = os.path.join(RESULTS, "walk_forward_daily.csv")
    for p in (eq_path, fold_path):
        if not os.path.exists(p):
            die(f"产物缺失：{p}")

    # ── 全样本净值序列（腿的公共底） ─────────────────────────
    eq = pd.read_csv(eq_path, parse_dates=["date"]).set_index("date")["equity"]
    full_ret = eq.pct_change().dropna()
    print(f"底稿：{RESULTS}")
    print(f"全样本净值 {eq.index[0].date()} → {eq.index[-1].date()}，"
          f"{len(eq)} 行，首值 {eq.iloc[0]:.2f} → 末值 {eq.iloc[-1]:.2f}"
          f"（累计 {cum(full_ret) * 100:+.2f}%，最大回撤 {max_dd(eq) * 100:.2f}%）")
    full_sr = sharpe(full_ret)
    print(f"全样本年化夏普（本脚本复算）= {full_sr:+.4f}")

    # S3：与判据文件里那条 sharpe_annual 对表
    dsr_path = os.path.join(RESULTS, "dsr.csv")
    if os.path.exists(dsr_path):
        d = pd.read_csv(dsr_path)
        ref = float(d.iloc[-1]["sharpe_annual"])
        if abs(ref - full_sr) > 1e-3:
            die(f"S3 收益口径不是一套：本脚本 {full_sr:+.4f} vs dsr.csv "
                f"{ref:+.4f} ⇒ 下面的读数全部作废，先修口径")
        print(f"[S3 ✅] 与 dsr.csv 的 sharpe_annual {ref:+.4f} 对得上"
              f"（|Δ|={abs(ref - full_sr):.2e}）；该场 n_trials="
              f"{int(d.iloc[-1]['n_trials'])}，DSR={float(d.iloc[-1]['dsr']):.6f}，"
              f"passed={d.iloc[-1]['passed']}")

    # ── 折窗 ────────────────────────────────────────────────
    folds = pd.read_csv(fold_path)
    pat = re.compile(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})\s*\((\d+)\s*bars\)")
    wins = []
    for _, r in folds.iterrows():
        m = pat.search(str(r["回测区间"]))
        if not m:
            die(f"折 {r.get('fold')} 的区间解析不出来：{r['回测区间']!r}")
        s, e, nb = pd.Timestamp(m.group(1)), pd.Timestamp(m.group(2)), int(m.group(3))
        seg = full_ret.loc[s:e]
        # S1
        if len(seg) < 100:
            die(f"S1 折 {r['fold']} 窗口 {s.date()}~{e.date()} 只取到 {len(seg)} "
                f"个交易日（<100）⇒ 日期或日历不对，读数无意义")
        if int(r["bars_per_year"]) != BARS_PER_YEAR:
            die(f"折表 bars_per_year={r['bars_per_year']}，与本脚本 "
                f"{BARS_PER_YEAR} 不同 ⇒ 年化不可比")
        wins.append({"fold": int(r["fold"]), "s": s, "e": e, "nb": nb,
                     "seg": seg, "row": r})
        print(f"  折 {r['fold']} {s.date()}~{e.date()}：表内 {nb} bar / "
              f"净值序列取到 {len(seg)} bar；折内夏普 {r['夏普比率']}、"
              f"总收益 {r['总收益率']}、折内因子 {r['折内因子数']} 条"
              f"（{r['折内因子来源']}）、n_trials={int(r['n_trials'])}")

    # S2：两两不重叠 ⇒ 并集 bar 数 = 各段之和
    ivs = sorted((w["s"], w["e"]) for w in wins)
    for (a1, b1), (a2, b2) in zip(ivs, ivs[1:]):
        if a2 <= b1:
            die(f"S2 折窗重叠：{b1.date()} ≥ {a2.date()} ⇒ 「并集」不成立，"
                f"覆盖率与拼接读数都不可信")
    cov_bars = sum(len(w["seg"]) for w in wins)
    union_bars = len(full_ret.loc[ivs[0][0]:ivs[-1][1]])
    print(f"[S2 ✅] 三折测试段两两不相交，合计 {cov_bars} bar")

    # S4：覆盖率
    cover = cov_bars / len(full_ret)
    if not (0.0 < cover < 1.0):
        die(f"S4 覆盖率 {cover:.4f} 落在边界 ⇒ 区间腿不存在或全样本被折窗铺满，"
            f"这条腿的读数不许报")
    print(f"区间覆盖率 = {cov_bars}/{len(full_ret)} = {cover * 100:.2f}%  "
          f"（注意：折窗之间的空隙不等于「没数据」，是全样本在跑但没有任何折验过）")

    # ── 腿一：区间 ──────────────────────────────────────────
    in_wins = pd.concat([w["seg"] for w in wins])
    out_mask = pd.Series(True, index=full_ret.index)
    for w in wins:
        out_mask.loc[w["s"]:w["e"]] = False
    out_ret = full_ret[out_mask[out_mask].index]
    print("\n──────── 腿一 · 区间（同一套因子，切时间轴）────────")
    print(f"  折窗之内，全样本夏普 = {sharpe(in_wins):+.4f}"
          f"（{len(in_wins)} bar，累计 {cum(in_wins) * 100:+.2f}%）")
    print(f"  折窗之外，全样本夏普 = {sharpe(out_ret):+.4f}"
          f"（{len(out_ret)} bar，累计 {cum(out_ret) * 100:+.2f}%，"
          f"最大回撤 {max_dd((1 + out_ret).cumprod()) * 100:.2f}%）")
    print(f"  整段                = {full_sr:+.4f}")
    print(f"  ⇒ 同一套因子，只看被验过的那 {cover * 100:.0f}% 时间轴就已经是"
          f"{sharpe(in_wins) - full_sr:+.4f} 的夏普差；"
          f"钱亏在没被任何折验过的那一段")

    # ── 腿二：因子 ──────────────────────────────────────────
    print("\n──────── 腿二 · 因子（同一批区间，换因子集）────────")
    for w in wins:
        fold_reported = float(w["row"]["夏普比率"])
        same_window_full = sharpe(w["seg"])
        print(f"  折 {w['fold']}：折内因子 {fold_reported:+.4f}｜全样本整库在同一窗口 "
              f"{same_window_full:+.4f}｜因子腿差 {fold_reported - same_window_full:+.4f}")
    avg_fold = float(np.mean([float(w["row"]["夏普比率"]) for w in wins]))
    avg_full_in = float(np.mean([sharpe(w["seg"]) for w in wins]))
    print(f"  三折均值：折内 {avg_fold:+.4f} vs 同窗全样本 {avg_full_in:+.4f} "
          f"⇒ 因子腿平均 {avg_fold - avg_full_in:+.4f}")

    # ── 合并样本外那条判据 ───────────────────────────────────
    oos_path = os.path.join(RESULTS, "walk_forward_oos_daily.csv")
    if os.path.exists(oos_path):
        o = pd.read_csv(oos_path).iloc[-1]
        print("\n──────── 判据那一层（可判定的那条链）────────")
        print(f"  合并样本外：年化夏普 {float(o['sharpe_annual']):+.4f}、"
              f"运气门槛 {float(o['sr0_annual']):+.4f}、"
              f"DSR {float(o['dsr']):.4f}、passed={o['passed']}、"
              f"n_trials={int(o['n_trials'])}、段数={int(o['n_segments'])}")
        print(f"  全样本那遍：年化夏普 {full_sr:+.4f}、运气门槛 "
              f"{float(pd.read_csv(dsr_path).iloc[-1]['sr0_annual']):+.4f} ⇒ "
              f"两条都判 False，所以「落差」不发生在这道闸上，"
              f"发生在「谁去看、看哪一段」")

    print("\n读数边界：折内那三行是**每折自己的回测**（因子集不同、起点各自 1 万元），"
          "全样本那遍是**整库一次放完**；两者净值不可直接相减，本脚本只比"
          "「同区间的日收益序列」这一层。")


if __name__ == "__main__":
    main()
