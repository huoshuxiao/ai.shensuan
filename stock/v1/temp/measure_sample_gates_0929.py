# -*- coding: utf-8 -*-
"""量股票线**样本类五道人工阈值**吃掉多少数据：不重跑任何判据，只流式扫 qlib bin 的 close。

五道阈值与它们的落点（口径都从生产代码现读，不在这里重抄）：
  ASHARE_MIN_OBS=250 → `run_ashare_factor_eval.py:93`（单票面板行数与非 NaN close 双双够数才入池）
  ASHARE_MIN_CS=100  → 同日截面有效票数下限（`daily_cross_ic` 的参数）
  ASHARE_ROLL_WINDOW=252 / ROLL_FOLDS=5 / ROLL_RECENT_YEARS=3 → 环1 滚动窗与折（这三个是**本场实跑的进程值**；`ROLL_FOLDS` 的默认值 09-30 已拨到 25，本场读数只讲「铺不铺得满」的算术）
  ASHARE_PORT_WARMUP_DAYS=120 → 组合回放起算前的预热丢弃

口径必须和生产一致（这是 09-29 第一版踩过的坑，写在 D 块）：
  面板 daily_pv.h5 的行集 = 每只票在 `instruments/all.txt` 里登记的起止区间
  ∩ 日历 ∩ 不早于 2008-12-29（`pregen_source_data.py` 的 START），
  **不是**该股 `.day.bin` 数组的物理长度。

内存口径：逐票读一个 close 数组、只留有效日的位置索引后丢弃；两趟计数（先定池、再数截面），
不把面板读进内存。HDF5 只读元数据里的 shape，不加载数据。

有牙检查（不通过就 exit 1）：
  D 逐票窗口行数求和必须**逐位等于**面板 shape[0]（差一行就说明我量的不是生产吃的那份数据）
  F 合成注入：把一只票截到 100 行 ⇒ MIN_OBS 必须把它判掉；把某天砍到 50 只 ⇒ MIN_CS 必须判掉
"""
import bisect
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
QLIB = os.path.join(REPO, "common", "data", "stock", "qlib", "qlib_data", "cn_data")
MOD_PATH = os.path.join(REPO, "common", "src", "data", "stock", "update_qlib_bin_daily.py")
PANEL_START = "2008-12-29"   # pregen_source_data.py 的 START，面板行集下界
FAIL = []


def block(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


spec = importlib.util.spec_from_file_location("ubd_t2", MOD_PATH)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
read_field = M.read_field

sys.path.insert(0, os.path.join(REPO, "stock", "v1", "src"))
import _bootstrap  # noqa: E402,F401
import config as C  # noqa: E402

MIN_OBS, MIN_CS = C.ASHARE_MIN_OBS, C.ASHARE_MIN_CS
WIN, FOLDS, RECENT, WARMUP = (C.ASHARE_ROLL_WINDOW, C.ASHARE_ROLL_FOLDS,
                              C.ASHARE_ROLL_RECENT_YEARS, C.ASHARE_PORT_WARMUP_DAYS)
PANEL = C.ASHARE_DAILY_H5
print(f"现读常量：MIN_OBS={MIN_OBS} MIN_CS={MIN_CS} ROLL_WINDOW={WIN} "
      f"FOLDS={FOLDS} RECENT_YEARS={RECENT} WARMUP={WARMUP}", flush=True)
print(f"面板（只读元数据）：{PANEL}", flush=True)

cal = [ln.strip() for ln in open(os.path.join(QLIB, "calendars", "day.txt")) if ln.strip()]
NC = len(cal)
ci = {d: i for i, d in enumerate(cal)}
START_IDX = ci[PANEL_START]


def clip(date_str, upper):
    """all.txt 的日期落到日历上的下标；日历里没有这一天就往里找，找不到返回 None。"""
    if date_str in ci:
        return ci[date_str]
    j = bisect.bisect_left(cal, date_str)
    return j if j <= upper and j < NC else None


windows = {}
for ln in open(os.path.join(QLIB, "instruments", "all.txt")):
    p = ln.rstrip("\n").split("\t")
    if len(p) < 3:
        continue
    s, e = clip(p[1], NC - 1), clip(p[2], NC - 1)
    if s is None or e is None:
        continue
    s, e = max(s, START_IDX), min(e, NC - 1)
    if e >= s:
        windows[p[0]] = (s, e)
stocks = sorted(windows)
block(f"A 按面板口径取行集：all.txt 留下 {len(stocks)} 只"
      f"（features/ 目录 {len(os.listdir(os.path.join(QLIB, 'features')))} 个）"
      f"｜日历 {NC} 天｜行集下界 {PANEL_START}")

nrows_bin = np.zeros(len(stocks), dtype=np.int64)   # 该行在面板里的总行数（含停牌 NaN）
notna_cnt = np.zeros(len(stocks), dtype=np.int64)   # 其中 close 非 NaN（= 生产 .notna() 那把尺）
valid_cnt = np.zeros(len(stocks), dtype=np.int64)   # 非 NaN 且 > MIN_BAR
valid_pos = {}                                      # 入池票的有效日下标，第二趟数截面用
miss = 0
for i, code in enumerate(stocks):
    s, e = windows[code]
    nrows_bin[i] = e - s + 1
    got = read_field(QLIB, code, "close")
    if got is None:
        miss += 1
        continue
    soff, arr = got
    lo, hi = max(s, soff), min(e, soff + len(arr) - 1)
    if hi < lo:
        continue
    seg = arr[lo - soff: hi - soff + 1]
    v = ~np.isnan(seg)
    notna_cnt[i] = int(v.sum())
    vv = v & (seg > M.MIN_BAR)
    valid_cnt[i] = int(vv.sum())
    valid_pos[i] = np.arange(lo, hi + 1)[vv]
print(f"  扫完：读不到 close 的 {miss} 只｜面板口径行集求和 {int(nrows_bin.sum()):,} 行"
      f"｜close 非 NaN {int(notna_cnt.sum()):,}（{notna_cnt.sum() / nrows_bin.sum():.1%}）"
      f"｜再过 MIN_BAR {int(valid_cnt.sum()):,}（比非 NaN 少 {int(notna_cnt.sum() - valid_cnt.sum()):,} 行）",
      flush=True)

block("D 口径对表：我从 bin 数出来的行集 vs 面板真实行数（只读 HDF5 元数据）")
try:
    with pd.HDFStore(PANEL, mode="r") as st:
        keys = list(st.keys())
        shape = tuple(st.get_storer(keys[0]).shape)
    got_rows, diff = int(nrows_bin.sum()), abs(shape[0] - int(nrows_bin.sum()))
    print(f"  面板 key={keys} shape={shape}｜bin 侧行集求和={got_rows:,}｜差 {diff:,} 行")
    if shape[0] != got_rows:
        FAIL.append(f"D 面板 {shape[0]:,} 行 vs bin 行集 {got_rows:,}：差 {diff:,} 行 ⇒ "
                    f"我量的不是生产吃的那份数据，B/C 的数只能当参考")
except Exception as e:
    FAIL.append(f"D 读不到面板元数据（{type(e).__name__}: {e}）⇒ 口径没对上表，B/C 的数不能当结论")

block("E1 顺便把第一版的坑钉住：同一批票按「bin 物理长度」数出来是多少，并把差额拆干净")
# 第一版拿 min(len(arr), NC-soff) 当行数（从上市首格数到日历末），比面板多出一截；
# 这里把多出来的那截归因成两条腿，并**硬对表**：两条腿之和必须正好等于总差。
phys = pre_start = post_end = 0
for code, (s, e) in windows.items():
    got = read_field(QLIB, code, "close")
    if got is None:
        continue
    soff, arr = got
    hi = min(soff + len(arr) - 1, NC - 1)
    if hi < soff:
        continue
    phys += hi - soff + 1
    pre_start += max(0, min(hi, START_IDX - 1) - soff + 1)
    # 退市日 e 本身已经在窗口里数过一遍，这条腿只能从 e+1 起算（第一版写 hi-e+1 每只多 1 格）
    post_end += max(0, hi - e)
gap = phys - int(nrows_bin.sum())
print(f"  面板口径（夹 all.txt + {PANEL_START}）{int(nrows_bin.sum()):,} 行 vs "
      f"物理长度口径 {phys:,} 行 ⇒ 差 {gap:,} 行（{phys / max(int(nrows_bin.sum()), 1) - 1:+.1%}）")
print(f"  差额归因：{PANEL_START} 之前 {pre_start:,} 行 + 退市日之后 {post_end:,} 行 = {pre_start + post_end:,} 行")
if pre_start + post_end != gap:
    FAIL.append(f"E1 差额拆不开：物理 {phys:,} − 面板 {int(nrows_bin.sum()):,} = {gap:,}，"
                f"两条腿只归到 {pre_start + post_end:,}（差 {gap - pre_start - post_end:,}）⇒ 归因是猜的")

block("B MIN_OBS 往两侧各挪一档：入池票数与票日数（两把尺都要够）")
LAD = [100, 150, 200, MIN_OBS, 400, 600, 800, 1200]
prev = None
for t in LAD:
    keep = (nrows_bin >= t) & (notna_cnt >= t)
    nd = int(keep.sum())
    tag = "←生产" if t == MIN_OBS else ""
    print(f"  ≥{t:>5} 行：留 {nd:>5} 只（{nd / len(stocks):.1%}）"
          f"｜覆盖非 NaN 票日 {int(notna_cnt[keep].sum()):,}（{notna_cnt[keep].sum() / notna_cnt.sum():.1%}） {tag}")
    if prev is not None and nd > prev:
        FAIL.append("B 门槛抬高反而留更多票 ⇒ 尺子坏了")
    prev = nd
q = np.percentile(notna_cnt, [1, 5, 25, 50, 75, 99])
print(f"  单票非 NaN 行数分位：p1={q[0]:.0f} p5={q[1]:.0f} p25={q[2]:.0f} 中位={q[3]:.0f} "
      f"p75={q[4]:.0f} p99={q[5]:.0f}｜最短 {notna_cnt.min()} 最长 {notna_cnt.max()}")
pool_idx = np.where((nrows_bin >= MIN_OBS) & (notna_cnt >= MIN_OBS))[0]
print(f"  生产入池：{len(pool_idx)} 只（= run_ashare_factor_eval 那句 `有效个股 N 只` 应该报的数）")

block("C MIN_CS 往两侧各挪一档：只在**入池票**上数每日截面宽度（和 daily_cross_ic 同口径）")
day_cnt = np.zeros(NC, dtype=np.int32)
for i in pool_idx:
    p = valid_pos.get(i)
    if p is not None and len(p):
        day_cnt[p] += 1
live = day_cnt > 0
print(f"  有票的日子 {int(live.sum())}/{NC} 天｜每日截面：中位 {np.median(day_cnt[live]):.0f}"
      f" p5={np.percentile(day_cnt[live], 5):.0f} p95={np.percentile(day_cnt[live], 95):.0f}"
      f" 最低 {day_cnt[live].min()} 最高 {day_cnt.max()}")
prev = None
for t in [25, 50, MIN_CS, 200, 500, 1000]:
    keep = day_cnt >= t
    nd = int(keep.sum())
    tag = "←生产" if t == MIN_CS else ""
    print(f"  ≥{t:>5} 只：留 {nd:>5} 天（占有票日 {nd / max(int(live.sum()), 1):.1%}）"
          f"｜覆盖票日 {int(day_cnt[keep].sum()):,}（{day_cnt[keep].sum() / max(day_cnt.sum(), 1):.1%}） {tag}")
    if prev is not None and nd > prev:
        FAIL.append("C 门槛抬高反而留更多天 ⇒ 尺子坏了")
    prev = nd
first_ok = cal[int(np.argmax(day_cnt >= MIN_CS))]
print(f"  第一个够 {MIN_CS} 只的日子：{first_ok}")

block("E 滚动窗那三个数在日历上是什么")
n_days = int(live.sum())
print(f"  有票日 {n_days} 天（{cal[0]} ~ {cal[-1]}）")
print(f"  ROLL_WINDOW={WIN} 格 = {WIN / 252:.2f} 年一窗；ROLL_FOLDS={FOLDS} ⇒ 等分折**不要求** "
      f"WIN×FOLDS 天（见下），RECENT_YEARS={RECENT} ⇒ 只看最近 {RECENT * 252} 格")
print(f"  WARMUP={WARMUP} 格 = 组合回放起算日再往前丢 {WARMUP / 252:.2f} 年；"
      f"2015-01-01 起算 ⇒ 预热要从约 {cal[max(0, int(np.argmax(np.asarray(cal) >= '2015-01-01')) - WARMUP)]} 开始")
# ⚠️ 09-30 订正（本行旧版写的「每折 WIN 格 ⇒ 总长 WIN×f 年 ⇒ 铺不满」是**错的**，
#    由此在总账 §十一 留下过「起点拨到 2022 需 ≥1,260 天」那句假结论）。
#    `_folds()`（`run_ashare_rolling_ic.py:98`）是把**整段**等分 f 份，每份 = 天数/f，
#    与 252 日滚动窗无关；真下界只有两条：逐日序列 ≥ WINDOW 才出读数
#    （`stability_stats()` 开头那条 `len(s) < WINDOW` ⇒ 「样本不足」）、以及 f ≤ 天数才不空段。
#    逐档实测与牙见 `stock/v1/temp/roll_folds_ladder_0930.py` 的 V7（1,146 天铺得满 5 折、
#    251 天判不足），账单见 `stock/v1/temp/人工阈值总账_0929.md` §十三。
for f in (3, 5, 7):
    print(f"    折数={f}：整段等分 ⇒ 每折 {n_days / f:.1f} 天 = {n_days / f / 252:.2f} 年"
          f"｜现在可用的 {n_days} 天 = {n_days / 252:.1f} 年 ⇒ "
          f"{'铺得满' if f <= n_days and n_days >= WIN else '铺不满'}"
          f"（出读数前置：{n_days} ≥ WINDOW={WIN} ⇒ {'够' if n_days >= WIN else '不够'}）")

block("F 有牙检查：合成注入必须被各自闸判掉")
i_victim = int(np.argmax(notna_cnt))
old = (int(nrows_bin[i_victim]), int(notna_cnt[i_victim]))
nrows_bin[i_victim], notna_cnt[i_victim] = 100, 100
kept_after = int(((nrows_bin >= MIN_OBS) & (notna_cnt >= MIN_OBS)).sum())
nrows_bin[i_victim], notna_cnt[i_victim] = old
kept_base = int(((nrows_bin >= MIN_OBS) & (notna_cnt >= MIN_OBS)).sum())
print(f"  把最厚那只（{old[0]} 行 / 非 NaN {old[1]}）截成 100 行 ⇒ 入池数从 {kept_base} 掉到 {kept_after}"
      f"（掉 {kept_base - kept_after} 只）")
if kept_after >= kept_base:
    FAIL.append("F MIN_OBS：把一只票截到 100 行竟然没被踢出池 ⇒ 这道闸无牙")
j = int(np.argmax(day_cnt))
keep_base = int((day_cnt >= MIN_CS).sum())
saved = int(day_cnt[j])
day_cnt[j] = 50
keep_after = int((day_cnt >= MIN_CS).sum())
day_cnt[j] = saved
print(f"  把最厚那天（{saved} 只）砍成 50 只 ⇒ 可用天数从 {keep_base} 掉到 {keep_after}")
if keep_after >= keep_base:
    FAIL.append("F MIN_CS：把一天砍到 50 只竟然还在闸上面 ⇒ 这道闸无牙")

block("汇总")
if FAIL:
    print("  ❌ 未过：")
    for f in FAIL:
        print("    -", f)
    sys.exit(1)
print("  ✅ 行集与面板逐位对上 + 两道样本闸的合成注入都判掉了")
