# -*- coding: utf-8 -*-
"""乙：「等分折数」`ASHARE_ROLL_FOLDS = 5` 逐档梯子（**完全离线**：只读两张已有产物，
不读 0.8GB 面板、不跑回放、不写生产目录。产物只落 `stock/v1/temp/tmp_roll_folds_ladder_0930/`）。

⚠️ **本场所钉的「生产档」是读进程实值的**（`R.FOLDS`），09-30 本场跑完后用户把
`config.py:74` 的默认值从 5 拨到了 **25** ⇒ **现在再跑本场，V0 身份闸必红**
（归档 `ashare_rolling_ic.csv` 的 `fold_*` 三列还是 5 折那版，与 k=25 的重算逐位对不上）。
那不是尺子坏了，是「归档落后于旋钮」的如实读数，与 AGENT.md §8.4 那行 ⚠️ 同一件事。
本场的**历史**读数（梯子十三档、`5 折下 21 条全部满分`）都是拨档**之前**那场 5 折跑的，照旧有效。

要回答的问题
------------
config 注释写这根旋钮看的是「段段同号还是靠一年撑起来」，本账里从没量过：折数拨到
3 / 10 / 20，谁的判决会翻、翻多大。逐档 = [1,2,3,4,5,6,7,8,10,12,15,20,30]。

**先说最要紧的一件事：这根旋钮不坐在任何判据链上。**
`fold_same_pct / fold_min / fold_last` 的消费点只有三处：入口自己落盘、看板「稳不稳」页
只读展示（`app.py` 的 rolling 段）、temp 里的报告脚本。准入判定（环1 截面 IC、环2 组合层、
环3 判重）一处都不读它 ⇒「准入判决翻不翻」这一问的**答案是「没有判决可翻」**。
所以本表量的是**这把尺子的分辨力**：5 折下 21 条**全部给满分**（同号占比 1.0），
折数变细到底有几条开始打脸。

尺子只有一份
------------
逐档调的是**入口自己的** `stability_stats()`（`run_ashare_rolling_ic.py:137`），
折切法调**入口自己的** `_folds()`（`:98`），容差用**入口自己的** `SAME_RULER_TOL`（`:79`）。
逐日 IC 序列取自入口落盘的 `ashare_ic_daily.csv`（那次全市场跑的原始序列；float64 的
`to_csv` 往返末位噪声实测 1e-16 量级）⇒ 换折数**不需要**重扫面板，秒级、百来 MB。
能不能这么省，由身份闸 V0 决定：生产档重算的四列要与归档 `ashare_rolling_ic.csv`
逐位对上，对不上就说明我读的不是那次跑的那份序列，后面所有档位读数作废。

判据 9 格（V0/V1/V2/V2b/V3/V4/V5/V7/V9），读数 6 行（逐日锚 + V6 四行 + V8 成本）。
任何一格红 ⇒ exit=1。
恒真防护：
  · V1「补丁穿透」是**梯子的牙**：若 monkeypatch 没进 `stability_stats`，十三档会给出
    逐格相同的读数 ⇒ 这条必须红。负对照 `RF_NEGCTL=dead` 就是把旋钮做成不生效，
    它应当红（连同 V2/V2b/V3/V4，共五格）。
  · V2/V2b/V3 是**真值控制**：折数=1 时按构造只能与全样本同号、且段均值就是全样本 IC；
    折数=序列长度时每段一天，同号占比必须等于一条**零复刻**的独立构造
    `mean(sign(逐日 IC) == sign(全样本 IC))`。这两条把「折 = 等分段取均值再比符号」钉死。
  · V5 直接数**归档那一列**，不数我重算的 ⇒ 负对照把尺子弄坏时它照样绿，
    红集才可逐字预期（不被同一处坏重复计数）。
  · 负对照 `RF_NEGCTL=shuffle`（切之前打乱时间顺序）只应打红 V0 一格。
    ⚠️ 这里的容差一律取入口的 `SAME_RULER_TOL=1e-15`、**不取 1e-18 的"逐位相等"**：
    段均值是「求和再除」，只要求和顺序与 `.mean()` 不同就有 ~1e-17 的 float64 结合律噪声。
    09-30 第一版把 V2/V2b 钉在 1e-18 ⇒ shuffle 臂**多红一格**（数学上那格该绿），
    红集对不上期望；教训与「复刻臂要按同一顺序求和」是同一件事的另一半。
  · V7 是**订正**：09-29 那份 `measure_sample_gates_0929.py:205` 用
    `ROLL_WINDOW × FOLDS ≤ 天数` 判「铺得满 / 铺不满」，由此在总账里写下过
    「起点拨到 2022 ⇒ 1,146 天铺不满 5 折、需 ≥1,260 天」。**那句是错的**：`_folds`
    是把整段等分 F 份（`np.array_split`），与 252 日滚动窗无关，1,146 天照样铺得满 5 折；
    真正的下界在 `stability_stats()` 开头那条 `len(s) < WINDOW` ⇒ 序列不足 252 天才判
    「样本不足」。V7 用合成序列正反两格量这个界（1,146 天必须过、251 天必须不足）。
"""
import os
import resource
import sys
import time

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
import _bootstrap  # noqa: F401,E402  (必须先于 config：`src/config/` 是裸名导入目录，
#                                                   不引导就把它当命名空间包缓存掉，
#                                                   后面入口的 `from config import …` 当场 ImportError)
import config as C                                              # noqa: E402
import run_ashare_rolling_ic as R                                # noqa: E402  复用 stability_stats/_folds 单点

RES = f"{ROOT}/stock/v1/data/results"
# ⚠️ 每一臂各写各的目录：负对照与正跑共用一个 OUT 时，后跑的那臂会把前一臂的
# `fold_ladder.csv` 就地覆盖掉（09-30 实踩：正跑那份「8 折 1 条、12 折 5 条」的梯子表
# 被 dead 臂的「全 0」盖掉，读数只能靠日志复盘）。多场次输出必须先分格再落盘。
_BASE_OUT = f"{ROOT}/stock/v1/temp/tmp_roll_folds_ladder_0930"
OUT = _BASE_OUT if not os.environ.get("RF_NEGCTL", "") else \
    f"{_BASE_OUT}_negctl_{os.environ['RF_NEGCTL']}"
T_START = time.time()
os.makedirs(OUT, exist_ok=True)
CHECKS = []

DAILY = os.path.join(RES, "ashare_ic_daily.csv")
ARCHIVE = os.path.join(RES, "ashare_rolling_ic.csv")
YEARLY = os.path.join(RES, "ashare_ic_yearly.csv")
PROD_FOLDS = R.FOLDS                       # 进程实读（config.py:74，默认值 09-30 已从 5 拨到 25）
BARS = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 30]
FOLD_COLS = ("fold_same_pct", "fold_min", "fold_last", "rank_ic_full")
NEGCTL = os.environ.get("RF_NEGCTL", "")
# 负对照的**期望红集**（格数钉死；跑完逐字对表，漏红/多红都不许）
EXPECT_RED = {"": set(),
              "shuffle": {"V0"},
              "dead": {"V1", "V2", "V2b", "V3", "V4"}}
# 归档版本条件（**先声明、不随读数变**）：`config.py:74` 的折数 09-30 拨到 25 之后，
# 权威归档 `ashare_rolling_ic.csv` 曾还是 5 折那一版 ⇒ 那几天身份闸 V0 在任何一臂里都该红。
# 10-01 已重跑 `run_ashare_rolling_ic.py`（945s 扫面板、判据 A 绿、读数 B rank 最大差 1.2e-04），
# 归档三列 `fold_*` 与配置口径同版 ⇒ **这里清成空集**。留着不空 = 以后真把 V0 弄坏了也不会被"多红"抓到；
# 反过来若再拨这根旋钮，把下一档写成 {"V0"} 重新武装这条声明即可。
STALE_ARCHIVE_RED = set()
EXPECT_RED = {k: (v | STALE_ARCHIVE_RED if k else set(STALE_ARCHIVE_RED))
              for k, v in EXPECT_RED.items()}
if NEGCTL not in EXPECT_RED:
    raise SystemExit(f"[作废] RF_NEGCTL 只认 {sorted(k for k in EXPECT_RED if k)}，给的是 {NEGCTL!r}")

_orig_folds = R._folds


def folds_shuffle(s, n_folds):
    """负对照·坏尺子①：切之前先打乱时间顺序 ⇒「按时间等分」这条口径当场失效"""
    rng = np.random.default_rng(20260930)
    idx = rng.permutation(np.arange(len(s)))
    return [float(s.to_numpy()[i].mean()) for i in np.array_split(idx, n_folds) if len(i)]


def folds_dead(s, n_folds):
    """负对照·坏尺子②：旋钮不生效，永远按生产折数切 ⇒ 整张梯子该塌成一条线"""
    return _orig_folds(s, PROD_FOLDS)


def check(cid, label, ok, detail):
    CHECKS.append((cid, label, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} [{cid}] {label}｜{detail}")
    return ok


def readout(label, detail):
    print(f"  ·  [{label}·读数] {detail}")


def finish():
    red = {cid for cid, _, ok, _ in CHECKS if not ok}
    print(f"\n===== 判据 {len(CHECKS)} 格：红 {len(red)} 格 {sorted(red) if red else '（无）'} =====")
    pd.DataFrame(CHECKS, columns=["id", "label", "ok", "detail"]).to_csv(
        f"{OUT}/checks.csv", index=False)
    if NEGCTL:
        want = EXPECT_RED[NEGCTL]
        ok = red == want
        print(f"[NEGCTL={NEGCTL}] 实得红集 {sorted(red)}｜期望 {sorted(want)} ⇒ "
              f"{'✅ 逐字对上' if ok else '❌ 对不上'}"
              f"（漏红 {sorted(want - red)}｜多红 {sorted(red - want)}）")
        return 0 if ok else 1
    return 1 if red else 0


def stats_at(k, series, pseries, names):
    """把入口的 FOLDS 拨到 k，逐条取一次 stability_stats（只回 status=ok 的那些）"""
    R.FOLDS = k
    out = {}
    for n in names:
        st = R.stability_stats(n, series[n], pseries[n])
        if st.get("status") == "ok" and "fold_same_pct" in st:
            out[n] = st
    return out


def main():
    print(f"[配置] 进程实读 ASHARE_ROLL_FOLDS={PROD_FOLDS} ASHARE_ROLL_WINDOW={R.WINDOW} "
          f"ASHARE_ROLL_RECENT_YEARS={R.RECENT_YEARS}")
    print(f"[负对照] RF_NEGCTL={NEGCTL!r} ⇒ 期望红集 {sorted(EXPECT_RED[NEGCTL])}")
    if NEGCTL == "shuffle":
        R._folds = folds_shuffle
    elif NEGCTL == "dead":
        R._folds = folds_dead

    if not (os.path.exists(DAILY) and os.path.exists(ARCHIVE)):
        print(f"[作废] 缺产物 daily={os.path.exists(DAILY)} archive={os.path.exists(ARCHIVE)}"
              "⇒ 先跑 run_ashare_rolling_ic.py")
        return 1

    d = pd.read_csv(DAILY, parse_dates=["date"]).set_index("date")
    arc = pd.read_csv(ARCHIVE)
    names = [n for n in arc.name if f"rank|{n}" in d.columns]
    miss = sorted(set(arc.name) - set(names))
    if miss:
        print(f"[作废] 归档有 {len(miss)} 条花名在 daily 宽表取不到列：{miss[:3]}…")
        return 1
    stamp = lambda p: time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
    print(f"[输入] daily {d.shape[0]} 天 × {d.shape[1]} 列（{len(names)} 条 × 两口径）、"
          f"归档 {arc.shape[0]} 行｜daily mtime={stamp(DAILY)}　archive mtime={stamp(ARCHIVE)}")
    series = {n: d[f"rank|{n}"] for n in names}
    pseries = {n: d[f"pearson|{n}"] for n in names}
    n_live = {n: int(series[n].notna().sum()) for n in names}
    ac = arc.set_index("name")
    t0 = time.time()

    # ---------- V0 身份闸：生产档重算必须与归档逐位对上 ----------
    prod = stats_at(PROD_FOLDS, series, pseries, names)
    max_d, worst_col = 0.0, ""
    for n, st in prod.items():
        for col in FOLD_COLS:
            dd = abs(float(st[col]) - float(ac.loc[n, col]))
            if dd > max_d:
                max_d, worst_col = dd, f"{n[:24]}·{col}"
    v0_ok = len(prod) == len(names) and max_d <= R.SAME_RULER_TOL
    check("V0", "身份闸：生产档重算的 fold_same_pct/fold_min/fold_last/rank_ic_full "
                f"必须与归档逐位对上（容差 {R.SAME_RULER_TOL:.0e}）",
          v0_ok, f"{len(prod)}/{len(names)} 条 status=ok｜max|Δ|={max_d:.3e}（最大在 {worst_col}）")
    if not v0_ok and not NEGCTL:
        print("⇒ 我读的不是那次跑出来的那份序列，梯子作废")
        return finish()

    # ---------- 逐档梯子：每档调入口现算一次 ----------
    ladder, detail = {}, []
    for k in BARS:
        per = stats_at(k, series, pseries, names)
        ladder[k] = per
        for n, st in per.items():
            detail.append({"fold": k, "name": n, "fold_same_pct": st["fold_same_pct"],
                           "fold_min": st["fold_min"], "fold_last": st["fold_last"],
                           "n_pieces": len(R._folds(series[n].dropna(), k)),
                           "piece_days": round(n_live[n] / k, 1)})
    rows = []
    for k in BARS:
        per = ladder[k]
        same = [st["fold_same_pct"] for st in per.values()]
        under = int(sum(abs(v - 1.0) >= 1e-15 for v in same))
        disp = (max(abs(per[n]["fold_last"] - prod[n]["fold_last"]) for n in per if n in prod)
                if (k != PROD_FOLDS and prod) else np.nan)
        rows.append({"折数": k, "生产档": k == PROD_FOLDS, "出读数条数": len(per),
                     "同号=1.0条数": int(sum(abs(v - 1.0) < 1e-15 for v in same)),
                     "同号<1.0条数": under,
                     "同号最低": round(min(same), 4), "同号均值": round(float(np.mean(same)), 4),
                     "段长中位(天)": int(np.median([round(n_live[n] / k) for n in per])),
                     "fold_last相对生产档最大位移": (None if pd.isna(disp) else round(disp, 8))})
    lad = pd.DataFrame(rows)
    det = pd.DataFrame(detail)
    lad.to_csv(f"{OUT}/fold_ladder.csv", index=False)
    det.to_csv(f"{OUT}/factor_x_fold.csv", index=False)
    print("\n===== 等分折数逐档（21 条在库因子；「同号<1.0」= 至少一段与全样本反号）=====")
    print(lad.to_string(index=False))

    # ---------- V1 梯子的牙：补丁必须穿透到读数 ----------
    disp = pd.to_numeric(lad["fold_last相对生产档最大位移"], errors="coerce").dropna()
    dm = f"{disp.max():.3e}" if len(disp) else "无位移读数"
    check("V1", "补丁穿透（牙）：折数一变读数必须真跟着动——各档「同号<1.0 条数」取值集合 >1，"
                "且 fold_last 相对生产档至少一条位移 >0",
          lad["同号<1.0条数"].nunique() > 1 and len(disp) > 0 and disp.max() > 0,
          f"「同号<1.0 条数」跨档取值 {sorted(lad['同号<1.0条数'].unique())}｜fold_last 最大位移 {dm}")

    # ---------- V2 / V2b 真值控制：折数=1 ----------
    # 容差用入口自己的 SAME_RULER_TOL，不用 1e-18 的"逐位相等"：段均值是**求和后再除**，
    # 一旦求和顺序与 `.mean()` 不同（负对照 shuffle 就是打乱顺序），float64 结合律末位
    # 就有 ~1e-17 的噪声 —— 09-30 shuffle 臂就是这么多红了一格 V2b（数学上它该绿）。
    one = stats_at(1, series, pseries, names)
    d_minlast = max(abs(st["fold_min"] - st["fold_last"]) for st in one.values())
    bad2 = [n for n, st in one.items()
            if abs(st["fold_same_pct"] - 1.0) > 0 or d_minlast > R.SAME_RULER_TOL]
    check("V2", "真值控制：折数=1 ⇒ 同号占比必须恰为 1.0 且 |fold_min-fold_last| ≤ 入口容差",
          len(bad2) == 0 and len(one) == len(names),
          f"翻脸 {len(bad2)} 条{bad2[:2]}｜max|fold_min-fold_last|={d_minlast:.3e}"
          f"（容差 {R.SAME_RULER_TOL:.0e}）")
    d_full = max(abs(st["fold_last"] - st["rank_ic_full"]) for st in one.values())
    bad2b = [n for n, st in one.items() if abs(st["fold_last"] - st["rank_ic_full"]) > R.SAME_RULER_TOL]
    check("V2b", "真值控制另一半：折数=1 时 fold_last 必须等于 rank_ic_full（同一段均值）",
          len(bad2b) == 0, f"不等 {len(bad2b)} 条{bad2b[:2]}｜max|Δ|={d_full:.3e}"
          f"（容差 {R.SAME_RULER_TOL:.0e}）")

    # ---------- V3 逐日锚：折数=序列长度 ⇒ 每段一天，对表一条零复刻构造 ----------
    worst3, anchor = 0.0, {}
    for n in names:
        s = series[n].dropna()
        st = stats_at(len(s), series, pseries, [n])[n]
        indep = float((np.sign(s.to_numpy()) == np.sign(float(s.mean()))).mean())   # 不碰 _folds
        anchor[n] = indep
        worst3 = max(worst3, abs(st["fold_same_pct"] - indep))
    readout("逐日同号占比（与折数无关的第二把尺）",
            f"21 条区间 {min(anchor.values()):.3f}~{max(anchor.values()):.3f}｜"
            f"中位 {float(np.median(list(anchor.values()))):.3f}")
    check("V3", "逐日锚：折数=序列长度时每段一天，入口的同号占比必须等于一条**零复刻**的独立构造"
                " mean(sign(日IC)==sign(全样本IC))",
          worst3 <= R.SAME_RULER_TOL, f"max|Δ|={worst3:.3e}（容差 {R.SAME_RULER_TOL:.0e}）")

    # ---------- V4 分段形状：np.array_split 语义 ----------
    want_pieces = det.fold.map(lambda k: min(k, min(n_live.values())))
    bad4 = det[det.n_pieces != want_pieces]
    check("V4", "分段形状：非空段数必须 = min(折数, 最短序列天数)（`np.array_split` 的语义）",
          len(bad4) == 0, f"段数错位 {len(bad4)}/{len(det)} 格"
          f"｜折数>最短序列({min(n_live.values())})的档才会出现段数封顶")

    # ---------- V5 分辨力：只数归档那一列，不数我重算的 ----------
    # （故意不拿本表的 cnt 当判据：负对照把尺子弄坏时这一格必须还绿，
    #   否则同一处坏会被 V0 与 V5 重复计成两格红，红集就没法逐字预期了）
    under5 = int((ac["fold_same_pct"] < 1.0 - 1e-15).sum())
    cnt = lad.set_index("折数")["同号<1.0条数"]
    ynder = int((ac["year_same_pct"] < 1.0 - 1e-15).sum())
    rndr = int((ac["roll_same_pct"] < 1.0 - 1e-15).sum())
    same_set = set(ac.index[ac["fold_same_pct"] < 1.0 - 1e-15]) == \
        set(ac.index[ac["year_same_pct"] < 1.0 - 1e-15])
    check("V5", f"分辨力：生产档 {PROD_FOLDS} 折下「同号占比 <1.0」的条数（**直接数归档列**）"
                "必须 > 0 ⇒ 这把尺子在该档确实量得出打脸（5 折那版恒满分，量不出）",
          under5 > 0,
          f"归档 <1.0 的 {under5}/{len(ac)} 条（10-01 刷新归档时实测 7 条）｜"
          f"同版本自然年腿 {ynder} 条、滚动一年腿 {rndr} 条｜折腿名单==年腿名单：{same_set}"
          f"｜本表重算各档 <1.0 条数（只报不判）："
          + "、".join(f"{int(k)}折{int(v)}条" for k, v in cnt.items()))

    # ---------- V6 读数：打脸的是谁、有多少条压根不动 ----------
    top = det[det.fold == 20].sort_values("fold_same_pct").head(6)
    readout("20 折同号占比最低的前六名",
            "\n" + top[["name", "fold_same_pct", "fold_min", "piece_days"]].to_string(index=False))
    flips = det.groupby("name")["fold_same_pct"].nunique()
    readout("跨十三档一动不动", f"{int((flips == 1).sum())}/{len(flips)} 条"
                              f"（随折数变判词的 {int((flips > 1).sum())} 条）")
    first_bad = det[det.fold_same_pct < 1.0 - 1e-15].groupby("name")["fold"].min()
    readout("最早在哪一档开始打脸", "、".join(
        f"{int(k)}折 {int(v)} 条" for k, v in first_bad.value_counts().sort_index().items())
        if len(first_bad) else "十三档内无一条打脸")
    readout("打脸条数跨档不单调", f"各档 <1.0 条数序列 "
            f"{[int(x) for x in lad['同号<1.0条数']]}（折档 {[int(x) for x in lad['折数']]}）"
            "⇒ 换折数是**换一套等分栅格**、不是「越细越严」的同一把刀，跨档读数不可比大小")

    # ---------- V7 订正：折数与 252 日窗无关；真下界是 len(s) ≥ WINDOW ----------
    s1146 = pd.Series(np.linspace(-0.030, -0.020, 1146),
                      index=pd.date_range("2022-01-04", periods=1146, freq="B"))
    st1146 = stats_at(PROD_FOLDS, {"合成1146天": s1146}, {"合成1146天": s1146}, ["合成1146天"])
    n_short = R.WINDOW - 1
    s_short = pd.Series(np.linspace(-0.030, -0.020, n_short),
                        index=pd.date_range("2025-01-02", periods=n_short, freq="B"))
    st_short = R.stability_stats("合成不足窗", s_short, s_short)
    pieces1146 = len(_orig_folds(s1146, PROD_FOLDS))
    check("V7", "订正：1,146 天（09-29 那份脚本判「铺不满 5 折」的那一档）**必须**铺得满"
                f"⇒「需 ≥{R.WINDOW}×{PROD_FOLDS}={R.WINDOW * PROD_FOLDS} 天」那句作废；"
                f"真下界是序列 ≥ WINDOW={R.WINDOW} 天",
          len(st1146) == 1 and pieces1146 == PROD_FOLDS and st_short.get("status") != "ok",
          f"1,146 天：段数={pieces1146}、出读数={len(st1146)}条｜{n_short} 天："
          f"status=「{st_short.get('status')}」（必须判不足）")

    # ---------- V9 边界自证：本场只写 temp，三张生产产物一个字节没动 ----------
    touched = [p for p in (DAILY, ARCHIVE, YEARLY) if os.path.getmtime(p) >= T_START]
    check("V9", "边界自证：三张「稳不稳」生产产物的 mtime 必须早于本进程启动"
                "⇒ 本场是只读的，档位读数不改任何落盘",
          not touched, f"被本场动过的生产产物 {len(touched)} 个{touched}"
          f"｜本场唯一落盘面 {OUT}")

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    readout("成本", f"墙钟 {time.time() - t0:.1f}s、峰值 RSS {rss:.0f}MB、零面板"
          f"（对照：重扫一次全市场面板 ≈11 分钟）")
    if NEGCTL == "dead":
        readout("负对照预期", "dead 臂下整张梯子应塌成一条线："
                f"「同号<1.0 条数」跨档取值 {sorted(lad['同号<1.0条数'].unique())}")
    return finish()


if __name__ == "__main__":
    sys.exit(main())
