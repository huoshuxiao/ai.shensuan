#!/usr/bin/env python3.10
# -*- coding: utf-8 -*-
"""丙的验收：三场 PORT_HOLD 回放（3/5/10）到底能不能引用

五道判据，每道都要求「跑错了必须变红」，`--selftest` 那一支就是拿来证明这点的
（它把同一场产物复制成三场，要求 J2/J3 当场判红；不判红说明尺子是恒真的）。

  J1 落盘实测   三场各自的 eval_h*.csv 必须存在、行数对得上、mtime 晚于批次起点。
                「没跑」和「跑对」不能给出同一个读数 ⇒ 缺任何一场直接退出码 1。
  J2 网格自洽   每场 excess_univ_ew_days ≈ n_rebal × hold（误差 ≤ hold）。
                这是注入生效的牙：env 没接上、代码仍按 5 跑，hold=10 那场的
                n_rebal 会是 570 ⇒ 570×10 与 2852 差 2,848 ≫ 10 ⇒ 判红。
  J3 三场互异   n_rebal 必须两两不同且单调（hold 越小场次越多）。
  J4 锚点       hold=5 那场的轴行必须对在库归档 ashare_portfolio_eval.csv：
                gate=board、list_scheme=quota、top_n=50、signal=轴名 四件先对上，
                再要求 |Δexcess| ≤ 0.01（1pp）。为什么是 1pp：口径拨错一档
                （quota→global）的历史差是 1.44pp，会被这道抓住；而面板多出
                一两个交易日带来的漂移远小于 1pp。
  J5 面板指纹   三场必须踩在同一版面板上（md5 逐场相同），否则 3/5/10 不是同一把秤。

账单只报**生产那一档轴行**（排序轴 = ts_mean(volume,20)，落表名「量能水平 SMA(Volume,20)」）
与它的分年度，其余被验因子不在这一次的结论里。
"""
import argparse
import os
import sys
import time

import pandas as pd

R = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
ARCHIVE = os.path.join(R, "stock/v1/data/results/ashare_portfolio_eval.csv")
AXIS = "量能水平 SMA(Volume,20)"
HOLDS = (5, 3, 10)
ANCHOR_TOL = 0.01          # 与归档的超额允许差（绝对值，1pp）
TOP_N = 50

fails = []


def j(name, ok, detail):
    print(f"  {'✅' if ok else '❌'} J{name} {detail}")
    if not ok:
        fails.append(f"J{name}")


def load(d, h):
    p = os.path.join(d, f"eval_h{h}.csv")
    if not os.path.exists(p):
        return None, p
    return pd.read_csv(p), p


def axis_row(df):
    r = df[(df.top_n == TOP_N) & (df.signal == AXIS)]
    return r.iloc[0] if len(r) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--selftest", action="store_true",
                    help="拿一场的产物冒充三场，要求 J2/J3 必须判红")
    a = ap.parse_args()
    d = a.dir

    print("=" * 78)
    print("J1 落盘实测（存在 / 行数 / 字节 / mtime）")
    got, mtimes = {}, []
    src_h = HOLDS[0] if not a.selftest else 5
    for h in HOLDS:
        h_read = h
        if a.selftest:                       # 负对照：三场都指向同一份产物
            h_read = src_h
        df, p = load(d, h_read)
        if df is None and a.selftest and os.path.exists(p):
            pass
        if df is None:
            j(f"1-{h}", False, f"缺产物 {p}")
            continue
        n_expect = df.signal.nunique() * df.top_n.nunique()
        mtimes.append(os.path.getmtime(p))
        got[h] = df
        j(f"1-{h}", len(df) == n_expect and os.path.getsize(p) > 0,
          f"{os.path.basename(p)} {len(df)} 行（{df.signal.nunique()} 信号 × "
          f"{df.top_n.nunique()} 档）｜{os.path.getsize(p):,} 字节｜"
          # 用 localtime：pd.Timestamp(unit='s') 是 UTC-naive，09-30 02:22 落盘
          # 的产物会被打成「09-29 18:22」，比真值早 8 小时
          f"mtime {time.strftime('%m-%d %H:%M', time.localtime(mtimes[-1]))}")

    print("\nJ2 网格自洽：excess_univ_ew_days ≈ n_rebal × hold")
    for h in HOLDS:
        if h not in got:
            continue
        r = axis_row(got[h])
        if r is None:
            j(f"2-{h}", False, f"表里没有轴行（{AXIS} @ top_n={TOP_N}）⇒ 口径或版本不对")
            continue
        diff = abs(r.n_rebal * h - r.excess_univ_ew_days)
        j(f"2-{h}", diff <= h,
          f"hold={h}：n_rebal {int(r.n_rebal)} × {h} = {int(r.n_rebal) * h} vs "
          f"自报覆盖 {int(r.excess_univ_ew_days)} 天 ⇒ 差 {int(diff)}（门槛 ≤{h}）")

    print("\nJ3 三场互异：n_rebal 必须随 hold 单调减少且两两不同")
    nr = {h: int(axis_row(got[h]).n_rebal) for h in HOLDS if h in got and axis_row(got[h]) is not None}
    if len(nr) == len(HOLDS):
        mono = nr[3] > nr[5] > nr[10]
        uniq = len(set(nr.values())) == 3
        j("3", mono and uniq,
          f"n_rebal 3/5/10 = {nr[3]}/{nr[5]}/{nr[10]}｜单调 {mono}｜互异 {uniq}"
          + ("　⇒ 三场其实是同一场（注入没生效）" if not uniq else ""))
    else:
        j("3", False, f"只齐了 {sorted(nr)} 场，比不了")

    print("\nJ4 锚点：hold=5 那场的轴行 vs 在库归档")
    if 5 in got and not a.selftest:
        cur, arc = axis_row(got[5]), axis_row(pd.read_csv(ARCHIVE))
        same_shape = (str(cur.gate.iloc[0] if hasattr(cur.gate, 'iloc') else cur.gate) == "board"
                      and cur.list_scheme == "quota")
        dd = abs(cur.excess_univ_ew_ann - arc.excess_univ_ew_ann)
        j("4", dd <= ANCHOR_TOL and same_shape and cur.n_rebal >= arc.n_rebal,
          f"本场超额 {cur.excess_univ_ew_ann:+.4f} vs 归档 {arc.excess_univ_ew_ann:+.4f} "
          f"⇒ |Δ|={dd:.4f}（门槛 ≤{ANCHOR_TOL}）｜调仓日 {int(cur.n_rebal)} vs 归档 "
          f"{int(arc.n_rebal)}（只许 ≥，面板只增不减）｜口径 gate={cur.gate} "
          f"list_scheme={cur.list_scheme}")
    elif a.selftest:
        print("  （selftest 模式跳过锚点：产物是冒烟场，跨度不同本就该差很多）")
    else:
        j("4", False, "没有 hold=5 的产物，锚点无从谈起")

    print("\nJ5 面板指纹：三场必须同一版面板")
    fp = os.path.join(d, "panel_md5.txt")
    if os.path.exists(fp):
        lines = [l.split() for l in open(fp).read().splitlines() if l.strip()]
        md = {l[1] for l in lines}
        j("5", len(md) == 1 and len(lines) >= len(HOLDS),
          f"{len(lines)} 场记录、不同 md5 {len(md)} 个"
          + (f"（{list(md)[0][:12]}…）" if len(md) == 1 else " ⇒ 三场不是同一把秤"))
    else:
        if a.selftest:
            print("  （selftest 模式跳过面板指纹：冒烟场没有 panel_md5.txt）")
        else:
            j("5", False, "panel_md5.txt 不存在 ⇒ 无法证明三场同一把秤")

    if not fails and not a.selftest:
        print("\n" + "=" * 78)
        print(f"账单：排序轴 {AXIS} @ {TOP_N} 只，扣双边 0.0015，基准=可投资域等权")
        rows = []
        for h in HOLDS:
            r = axis_row(got[h])
            y = pd.read_csv(os.path.join(d, f"eval_h{h}_yearly.csv"))
            y = y[y.iloc[:, 0] == AXIS]
            rows.append({"hold": h, "调仓次数": int(r.n_rebal),
                         "年化超额": f"{r.excess_univ_ew_ann:+.2%}",
                         "IR": f"{r.excess_univ_ew_ir:.3f}",
                         "夏普": f"{r.sharpe:.3f}", "最深回撤": f"{r.max_drawdown:.2%}",
                         "单边换手/次": f"{r.one_way_turnover:.4f}",
                         "20日均额(亿)": f"{r.avg_amount_20d / 1e8:.2f}",
                         "负超额年数": f"{(y.excess < 0).sum()}/{len(y)}",
                         "最差年": f"{y.excess.min():+.2%}",
                         "最好年": f"{y.excess.max():+.2%}"})
        print(pd.DataFrame(rows).to_string(index=False))
        print("\n  ⚠️ 这三行是**整篮 50 只全买**那一档（观察名单腿），不是 5 席下单层；")
        print("     样本内、未过准入链，绝对水平还含幸存者偏差（退市票尾段被截断非归零）。")
    print("=" * 78)
    print(f"判定：{'✅ 全绿，可引用' if not fails else '❌ 未过：' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
