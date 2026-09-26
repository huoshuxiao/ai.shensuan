# -*- coding: utf-8 -*-
"""bin 内部约定体检：假台阶是什么形状 + 停牌日怎么存 + change 怎么定义（09-24）

#77 的 k 对看被 sina 限流打死了（8/8 在 akshare 内部 KeyError: 'date'，同代码换
进程又取得到 ⇒ 按 IP 限流）。但口径其实不需要它：`probe_spot_vs_bin_0924.py` 用
一次 bulk 快照就把盘面价对齐做到了 **5553 只票、p95 相对差 8.8e-08**，比 8 只票的
外部因子对照强得多。剩下三个问题只在 bin 内部，本脚本自己回答：

    1) 那 90 个假台阶格子是「单日尖峰」还是「永久台阶」？
       单日尖峰 ⇒ 只有跨那天的收益脏；永久台阶 ⇒ 那天之后的整段复权价都被乘了
       一个常数，跨天收益全脏，但**修法是分段乘回常数**（不碰行情，只碰缩放）
    2) 活票的 bin 里有没有 NaN（停牌日怎么写）？append 遇到停牌该续 NaN 还是平盘
    3) change 字段到底等不等于 close.pct_change()（append 时要照写）
"""
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BIN = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
       "qlib/qlib_data/cn_data")
FIELDS = ["open", "high", "low", "close", "volume", "amount", "factor", "vwap",
          "adjclose", "change"]
cal = [ln.strip() for ln in open(f"{BIN}/calendars/day.txt") if ln.strip()]
cal_idx = {d: i for i, d in enumerate(cal)}


def series(code, field):
    a = np.fromfile(f"{BIN}/features/{code.lower()}/{field}.day.bin", dtype="<f4")
    s = int(a[0])
    return pd.Series(a[1:], index=pd.to_datetime(cal[s:s + len(a) - 1]))


print("=" * 78)
print("1) 假台阶的形状：2023-10-16 前后 6 天")
for code in ["BJ838227", "BJ836957", "BJ832662"]:
    c, f = series(code, "close"), series(code, "factor")
    raw = c / f
    d = pd.Timestamp("2023-10-16")
    if d not in c.index:
        print(f"  {code}: 该日不在其序列里"); continue
    sl = c.index.get_loc(d)
    w = slice(max(0, sl - 3), min(len(c), sl + 4))
    tab = pd.DataFrame({"close(复权)": c.iloc[w], "factor": f.iloc[w],
                        "raw(盘面)": raw.iloc[w],
                        "raw日收益": raw.pct_change().iloc[w]})
    print(f"  {code} 末日 {c.index[-1].date()} 票是否还活着：{'是' if c.index[-1] > pd.Timestamp('2026-01-01') else '否'}")
    print(tab.to_string(float_format=lambda x: f"{x:,.6g}"))
    # 台阶之后有没有回落：比较事件前后的 raw/factor 比值中枢
    pre = (c.iloc[:sl] / f.iloc[:sl]).median()
    post = (c.iloc[sl:] / f.iloc[sl:]).median()
    print(f"    事件前 median(close/factor 量级) {pre:,.4g} / 事件后 {post:,.4g}"
          f"；factor 事件前 {f.iloc[sl-1]:,.6g} → 事件后 {f.iloc[sl]:,.6g}"
          f"（×{f.iloc[sl]/f.iloc[sl-1]:,.4g}），序列末 {f.iloc[-1]:,.6g}")
    step_up = f.iloc[sl] / f.iloc[sl - 1]
    back = f.iloc[sl + 1] / f.iloc[sl] if sl + 1 < len(f) else np.nan
    print(f"    次日 factor 变化 ×{back:,.6g} ⇒ "
          f"{'单日尖峰（factor 当天弹回）' if abs(back - 1) < 0.05 else '永久台阶（factor 停在高位）'}")

print("=" * 78)
print("2) 活票 bin 里的 NaN（停牌怎么存）")
spans = {}
for ln in open(f"{BIN}/instruments/all.txt"):
    p = ln.rstrip("\n").split("\t")
    if len(p) == 3:
        spans[p[0]] = p[2]
live = [c for c, e in spans.items() if e >= cal[-1] and c[:2] in ("SH", "SZ")]
rng = np.random.default_rng(7)
pick = [live[i] for i in rng.choice(len(live), 300, replace=False)]
nan_cells, gap_runs, calendar_gaps = 0, [], 0
for c in pick:
    s = series(c, "close")
    n = int(s.isna().sum())
    nan_cells += n
    if n:
        gap_runs.append((c, n))
    # 序列天数 vs 日历跨度：不等说明中间有整日缺失（不是 NaN 占位）
    span_days = cal_idx[s.index[-1].strftime("%Y-%m-%d")] - int(
        np.fromfile(f"{BIN}/features/{c.lower()}/close.day.bin", dtype="<f4", count=1)[0]) + 1
    if span_days != len(s):
        calendar_gaps += 1
print(f"  抽样 {len(pick)} 只活票：NaN 格子 {nan_cells} 个（有 NaN 的票 "
      f"{len(gap_runs)} 只，样例 {gap_runs[:4]}）")
print(f"  序列长度 != 日历跨度的票数：{calendar_gaps}（>0 ⇒ 缺日是直接不留格子，而不是写 NaN）")

print("=" * 78)
print("3) change 的定义（拿茅台最后 300 天对三种候选）")
for code in ["SH600519", "SZ000001"]:
    c, ch, o, f = (series(code, x) for x in ("close", "change", "open", "factor"))
    cand = {
        "close.pct_change": c.pct_change(fill_method=None),
        "open.pct_change": o.pct_change(fill_method=None),
        "raw(pct)": (c / f).pct_change(fill_method=None),
    }
    out = [f"  {code}:"]
    for k, v in cand.items():
        d = (ch - v).abs()
        ok = int((d < 1e-4).sum())
        out.append(f"{k} 命中 {ok}/{len(d)}")
    # 前收盘基准：qlib 的 $change 常用 close.shift(1) 但含除权修正
    print("  ".join(out))
    print(f"     change 尾 3: {ch.tail(3).values}")
    print(f"     茅台 change 与 close.pct 最大差 {(ch - cand['close.pct_change']).abs().max():.3g}")
