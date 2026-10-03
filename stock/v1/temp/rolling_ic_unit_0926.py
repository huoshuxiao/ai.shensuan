# -*- coding: utf-8 -*-
"""「稳不稳」秤的窗口统计单测（合成序列，手算答案；不碰面板、不写 results/）。

判据都是反证式的：同号/同 rulers 这类断言必须同时配一条**会失败**的构造，
否则「全过」可能只是恒真。tmp 只落在 stock/v1/temp/ 下。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src")
sys.path.insert(0, str(SRC))
import _bootstrap  # noqa: F401,E402
import run_ashare_rolling_ic as R  # noqa: E402

TMP = Path(__file__).resolve().parent
fails = []


def ck(mid, cond, msg):
    print(("✅ " if cond else "❌ ") + f"{mid} {msg}")
    if not cond:
        fails.append(mid)


def series(vals, start="2010-01-04", bday=True):
    idx = (pd.bdate_range(start=start, periods=len(vals)) if bday
           else pd.date_range(start=start, periods=len(vals)))
    return pd.Series(np.asarray(vals, dtype="float64"), index=idx)


def series_by_year(levels):
    """每个自然年一段常数值（{年: 值}）—— 让「分年读数」的答案能被手算，
    不必再折算「252 个交易日跨到几年」这种坑（工作日轴上按条数切段必然撞年头尾）。"""
    parts = [pd.Series(float(v), index=pd.bdate_range(start=f"{y}-01-01", end=f"{y}-12-31"))
             for y, v in sorted(levels.items())]
    return pd.concat(parts).sort_index()


# ---- U1 平稳正 IC：四个读数都该给「满稳」 ----
s1 = series([0.01] * (252 * 4))
p1 = series([0.005] * (252 * 4))
st = R.stability_stats("u1", s1, p1)
ck("U1a", abs(st["rank_ic_full"] - 0.01) < 1e-15, f"全样本={st['rank_ic_full']}")
ck("U1b", st["roll_same_pct"] == 1.0 and st["year_same_pct"] == 1.0
   and st["fold_same_pct"] == 1.0, "平稳序列三档同号占比都该是 1.0")
ck("U1c", abs(st["slope_per_yr"]) < 1e-15 and st["recent_sign_ok"],
   f"平的：斜率={st['slope_per_yr']}")
ck("U1d", abs(st["pearson_ic_full"] - 0.005) < 1e-15
   and abs(st["rank_icir_full"] - 0.01 / 1e-9) < 1.0,
   f"两个口径分开存：pearson={st['pearson_ic_full']}；常数序列 std=0 ⇒ ICIR 顶到"
   f" {st['rank_icir_full']:.1e} = 水平/1e-9 —— 沿用了环1 的 +1e-9 地板写法"
   f"（真逐日 IC 序列 std 在 0.05~0.15 量级，不会踩到这个退化值）")

# 正对照（同 U1）+ 反面对照：近 3 年翻负 ⇒ recent_sign_ok 必须变 False
# 2010~2019 每年 +0.02（10 年），2020~2022 每年 -0.02（3 年）
s2 = series_by_year({y: 0.02 for y in range(2010, 2020)}
                    | {y: -0.02 for y in range(2020, 2023)})
st2 = R.stability_stats("u2", s2, s2 * 0.5)
ck("U2a", st2["recent_sign_ok"] is False, "近 3 年翻负 ⇒ recent_sign_ok 必须 False")
ck("U2b", abs(st2["year_same_pct"] - 10.0 / 13.0) < 1e-12,
   f"13 个自然年里 10 年同号：{st2['year_same_pct']:.6f} vs {10/13:.6f}")
ck("U2c", st2["n_years"] == 13 and st2["hi_ic_year"] == 2010 and st2["lo_ic_year"] == 2020
   and abs(st2["lo_ic"] + 0.02) < 1e-15,
   f"极值年={st2['hi_ic_year']}/{st2['lo_ic_year']}（并列时取最早那年）")
ck("U2d", st2["fold_same_pct"] < 1.0, f"分段里有段打脸：{st2['fold_same_pct']}")
ck("U2e", abs(st2["rank_ic_full"] - 0.14 / 13.0) < 1e-3
   and -0.02001 < st2["recent_ic"] < -0.01985,
   f"全样本手算 ≈{0.14/13:.6f}（各年工作日数略有差，容 1e-3），实测 {st2['rank_ic_full']:.6f}"
   f"；近 3 年 {st2['recent_ic']:.6f}（比 -0.02 差一点是因为日历回拨 3 年正好多接到"
   f" {s2.index.max().year - 3}-12-31 那一天 +0.02）⇒ 一个数掩盖另一种形状")

# ---- U3 精确斜率：y = a ± 0.001 × 年，OLS 必须复原 ±0.001 ----
# 轴用 date_range（一天一格）：工作日轴上周末/节假日的间距不齐，「按条数滚的均值」
# 对**日历时间**就不再严格线性，斜率会带进 ~1e-9 的结构噪声（09-26 首跑实测踩到）。
idx = pd.date_range(start="2010-01-04", periods=252 * 12)   # 3024 天 ≈ 8.28 年
x = (idx - idx[0]).days.to_numpy() / 365.25
s3 = pd.Series(0.02 - 0.001 * x, index=idx)
st3 = R.stability_stats("u3", s3, s3 * 0.5)
roll = R._rolling(s3, 252).dropna()
x_span = (roll.index[-1] - roll.index[0]).days / 365.25
ck("U3a", abs(st3["slope_per_yr"] + 0.001) < 1e-12, f"斜率={st3['slope_per_yr']!r}")
ck("U3b", abs(st3["drift_ratio"] - (-0.001 * x_span / st3["rank_ic_full"])) < 1e-9
   and abs(st3["drift_ratio"]) < 1.0,
   f"漂移比={st3['drift_ratio']:.6f}（整段漂移 {-0.001*x_span:.5f} / 水平 "
   f"{st3['rank_ic_full']:.5f}，滚动窗跨度 {x_span:.2f} 年）⇒ 水平远大于漂移，趋势不算致命")
# 反面对照 + 「|比值|>1 ⇒ 末期符号已由趋势决定」：把水平压到和漂移同量级
s3dn = pd.Series(0.002 - 0.001 * x, index=idx)
s3up = pd.Series(0.002 + 0.001 * x, index=idx)
stdn, stup = (R.stability_stats("u3dn", s3dn, s3dn * 0.5),
              R.stability_stats("u3up", s3up, s3up * 0.5))
ck("U3c", stdn["slope_per_yr"] < 0 < stup["slope_per_yr"]
   and abs(stdn["drift_ratio"]) > 1.0 and abs(stup["drift_ratio"]) > 1.0,
   f"斜率符号跟着趋势翻（{stdn['slope_per_yr']:.6f} / {stup['slope_per_yr']:.6f}），"
   f"|drift_ratio| = {abs(stdn['drift_ratio']):.2f} / {abs(stup['drift_ratio']):.2f}"
   f" 都 > 1（漂移已超过自身水平）")

# ---- U4 底层函数逐位对齐手算 ----
r6 = R._rolling(series([1.0, 2.0, 3.0, 4.0]), 3)
ck("U4a", list(np.round(r6.to_numpy(), 12)) == [2.0, 3.0] and len(r6) == 2,
   f"不满窗不许造数：{list(r6)}")
f5 = R._folds(series(np.arange(1, 11, dtype="float64")), 5)
ck("U4b", f5 == [1.5, 3.5, 5.5, 7.5, 9.5], f"等分折={f5}")
# sign(0) 不白送 1：全样本 IC 恰为 0（前一半 +1、后一半 -1）时同号占比不该是 1.0
s4 = series([1.0] * 505 + [-1.0] * 505)
st4 = R.stability_stats("u4", s4, s4)
ck("U4c", R._sign(0.0) == 0.0 and R._sign(-0.5) == -1.0
   and st4["rank_ic_full"] == 0.0 and st4["roll_same_pct"] < 0.5,
   f"全样本=0 ⇒ 同号占比={st4['roll_same_pct']}（不许恒 1）")
# 样本不足 ⇒ 明确的一行，而不是编一组读数
st5 = R.stability_stats("u5", series([0.01] * 100), series([0.01] * 100))
ck("U4d", "样本不足" in st5["status"] and "rank_ic_full" not in st5, st5["status"])

# ---- U5 判据 A：同一次运行内「序列」与「环1 指标行」必须逐位同源 ----
s_ok = series([0.01, 0.03, -0.01] * 200)
mean_ok = float(s_ok.mean())
summary = pd.DataFrame([
    {"name": "a", "status": "ok", "rank_ic_full": mean_ok, "pearson_ic_full": mean_ok / 2,
     "roll_same_pct": 1.0},
    {"name": "b", "status": "样本不足（逐日 IC 天数 < 一个滚动窗）", "n_days": 10},
])
rows_ok = [{"name": "a", "cs_rank_ic_mean": mean_ok, "cs_ic_mean": mean_ok / 2,
            "n_days": len(s_ok)},
           {"name": "b", "cs_rank_ic_mean": float("nan"), "cs_ic_mean": float("nan"),
            "n_days": 0}]
sink_ok = {"a": {"rank": s_ok, "pearson": s_ok / 2}}
try:
    R.check_same_ruler(summary, rows_ok, sink_ok)
    ck("U5a", True, "两份输出逐位相同 ⇒ 放行")
except SystemExit as e:
    ck("U5a", False, f"自洽却被判不过：{e}")

# 反证三条：A 只可能因本秤弄坏序列而失败，所以每条都要真的能失败
bad_rank = summary.copy()
bad_rank.loc[0, "rank_ic_full"] += 1e-9          # 远小于 5e-4，但 > 1e-15 ⇒ A 必须抓
try:
    R.check_same_ruler(bad_rank, rows_ok, sink_ok)
    ck("U5b", False, "序列求平均挪 1e-9 竟然放行 ⇒ A 没牙")
except SystemExit as e:
    ck("U5b", "判据 A 不过" in str(e), f"挪 1e-9 被抓住：{str(e)[:70]}")
import copy
bad_days = copy.deepcopy(rows_ok)             # 深拷贝：浅拷贝会连着改掉 rows_ok 污染后面的用例
bad_days[0]["n_days"] = len(s_ok) - 1            # 少一天：均值可能没变，但天数对不上
try:
    R.check_same_ruler(summary, bad_days, sink_ok)
    ck("U5c", False, "天数少了 1 天竟然放行")
except SystemExit as e:
    ck("U5c", "序列天数" in str(e), f"天数被抓：{str(e)[:70]}")
swap = {"a": {"rank": s_ok / 2, "pearson": s_ok}}   # 两个口径串列
try:
    R.check_same_ruler(summary, rows_ok, swap)
    ck("U5d", False, "rank/pearson 串列竟然放行 ⇒ A 没看序列本身")
except SystemExit as e:
    ck("U5d", "序列本身" in str(e), f"串列也能抓：{str(e)[:70]}")

# ---- U6 读数 B：跨运行差值 = 归档过期读数，一天数据的位移必须放行、分叉必须拦 ----
_real, _real_h5 = R.ASHARE_EVAL_OUT, R.ASHARE_DAILY_H5
try:
    stale = TMP / "u6_stale_0926.csv"
    stale.write_text("name,cs_rank_ic_mean,cs_ic_mean,n_days\n"
                     f"a,{mean_ok + 6.4e-5},{mean_ok / 2 + 3.5e-5},{len(s_ok)}\nb,,,\n")
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        R.ASHARE_EVAL_OUT = str(stale)
        R.report_archive_drift(summary)          # 不许抛：6.4e-5 = 一天的量级
    out6 = buf.getvalue()
    ck("U6a", "数据往前走了" in out6 and "判据失败" not in out6,
       f"差 6.4e-5 ⇒ 只报数不拦（09-26 全市场跑撞到的就是这个量级）")

    # 归档比面板旧 ⇒ 必须念出 ⚠️「对应的是上一版面板」
    import os as _os
    h5_age = _os.path.getmtime(_real_h5)
    _os.utime(stale, (h5_age - 86400, h5_age - 86400))
    buf = io.StringIO()
    with redirect_stdout(buf):
        R.report_archive_drift(summary)
    ck("U6b", "上一版面板" in buf.getvalue(), "归档 mtime < 面板 mtime ⇒ ⚠️ 必须出现")

    forked = TMP / "u6_fork_0926.csv"
    forked.write_text("name,cs_rank_ic_mean,cs_ic_mean,n_days\n"
                      f"a,{mean_ok + 6e-4},{mean_ok / 2},{len(s_ok)}\n")
    R.ASHARE_EVAL_OUT = str(forked)
    try:
        R.report_archive_drift(summary)
        ck("U6c", False, "差 6e-4（一天的数据推不出）竟然放行")
    except SystemExit as e:
        ck("U6c", "判据失败" in str(e) and "别用这张表" in str(e),
           f"越线被拦：{str(e)[:70]}")

    R.ASHARE_EVAL_OUT = str(TMP / "u6_none_0926.csv")
    try:
        R.report_archive_drift(summary)
        ck("U6d", True, "没有归档 ⇒ 首次跑，跳过读数而不是拦路")
    except SystemExit as e:
        ck("U6d", False, f"没归档却被拦：{e}")

    nomatch = TMP / "u6_other_0926.csv"
    nomatch.write_text("name,cs_rank_ic_mean,cs_ic_mean\nzzz,0.9,0.9\n")
    R.ASHARE_EVAL_OUT = str(nomatch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        R.report_archive_drift(summary)
    ck("U6e", "一条花名都对不上" in buf.getvalue(), "花名全不匹配 ⇒ 报无法给出，不假装比过")
finally:
    R.ASHARE_EVAL_OUT = _real
    R.ASHARE_DAILY_H5 = _real_h5
    for p in (TMP / "u6_stale_0926.csv", TMP / "u6_fork_0926.csv",
              TMP / "u6_other_0926.csv"):
        p.unlink(missing_ok=True)

print("\n共 %d 条不过，%s" % (len(fails), "全过 ✅" if not fails else "未过 ❌ " + str(fails)))
sys.exit(1 if fails else 0)
