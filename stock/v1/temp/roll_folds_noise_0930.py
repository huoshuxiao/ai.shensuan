# -*- coding: utf-8 -*-
"""甲：「几折最优」不能靠一次打乱定 —— 补噪声底的**分布**与栅格**相位**两把尺（完全离线）。

为什么要有这一场
----------------
09-30「乙」量出折数梯子 [0,0,0,0,0,0,0,1,0,5,1,3,8]，随后问「几折最优」时我用了
`shuffle` 那一臂（**一次**打乱）当噪声底，读出「12 折净分辨力 5、30 折的 8 条里 7 条是噪声」。
那一次的账有两处没钉死，都不够格支撑一个定档：
  ① 一次打乱 = **一个抽样**，不是分布。真序 12 折打脸 5 条、那臂 0 条 —— 但我不知道
     「0 条」是噪声底的上界还是运气（换一枚种子会不会蹦出 2 条）。
  ② 折切法的切点由 `np.array_split(arange(n), k)` 决定 ⇒ 整段起点挪一天，**每一刀的落点
     全搬家**。12 折 5 条 / 15 折 1 条这种锯齿可能就是相位给的，而不是"12 折更会看事"。
这一场把两处各自量成一分布：B=200 次独立打乱给噪声底（含真序值的经验 p 值），
起点平移 j=0..9 给相位敏感面。产物只落 `stock/v1/temp/tmp_roll_folds_noise_0930/`。

**读的对象**：`ashare_ic_daily.csv`（稳不稳秤那次全市场跑落的原始逐日截面 Rank IC，
21 条 × 4,059 天，span 2010-01-08~2026-09-23、17 个自然年）。零面板、零回放。

尺子从哪来
----------
段切法调**入口自己的** `run_ashare_rolling_ic._folds()`（`:98`）、符号调**入口自己的**
`_sign()`（`:88`）、容差用**入口自己的** `SAME_RULER_TOL`（`:79`）；但「折腿三行组装」
（`fold_same_pct = mean(sign(段均值)==sign(全样本))` 等）是我**复刻**的 —— 因为 200×15×21
次调用如果都走完整 `stability_stats()`，会把与折数无关的 252 日滚动窗和分年重算 63,000 遍。
复刻的资格由 **N0 身份闸**决定：真序、不平移、生产档 k=`R.FOLDS`（本场跑时 5，
09-30 定档后为 **25**）的三列读数必须与权威归档
`ashare_rolling_ic.csv` 逐位对上（≤1e-15）。这一路的可传递性成立：
「入口重算 == 归档」由同批 `roll_folds_ladder_0930.py` 的 V0 钉过（max|Δ|=9.02e-17），
本场再钉「我的复刻 == 归档」⇒ 两把尺与归档同版；N0 不过则**全场作废**，一个读数都不引。
⚠️ 折数拨到 25 之后、归档**重跑之前**，N0 必红（归档还是 5 折那版）——那是如实读数不是尺子坏。
10-01 已重跑入口把 `fold_*` 追平 ⇒ N0 恢复该绿；那几天其余八格与折数无关（噪声底与相位各自扫
十五档，生产档只是其中一格标签），照常可跑。
另有两条**零复刻**独立构造兜住段切法本身：N3（k=1 时打乱也只能有一段、必须恰与全样本同号）、
N4（各段天数必须等于整除式子 `[n//k+1]*(n%k)+[n//k]*(k-n%k)`，不调 `array_split`）。

折档比「乙」多两档：**17 折**＝与同表里带判决口径的**自然年腿**（17 个自然年）同粒度，
拿它当「如果要把折数对齐到年，该拨在哪」的参照；**25 折**落在 15 与 30 中间，用来分辨
「净分辨力的峰」是 12 折一处还是 12~17 一段 —— 只有一处就是栅格巧合，成一段才敢说是粒度本身。

两把尺各自怎么念
----------------
- **噪声底**（B 次打乱）：把时间顺序拆掉，段就退化成随机抽样 ⇒ 该档「打脸条数」分布给出
  「**纯属切得碎**能造出几条」。真序值超过这条分布才叫时间结构。经验 p 值 =
  (1 + #{噪声打脸 ≥ 真序打脸}) / (B+1)，1/(B+1) 是下界（B=200 ⇒ 最小 0.00497）。
  ⚠️ 打乱**不改内容**（同一批数、只求和顺序变）⇒ 参照符号与全样本 IC 都不动，由 N2 钉。
- **相位**（j=0..9 平移，丢最前 j 天）：整段起点搬家 ⇒ 每刀落点跟着搬家。
  同一档跨 j 的打脸条数如果稳，那"几折"这件事不靠运气；如果锯齿搬家，
  那张梯子的档间差异就**不能当"谁更稳"** 读（只能当"这把栅格切在哪"）。
  ⚠️ 平移会同时改样本段（少 j 天）与切点相位，本场把它的**量级**读出来，
  不把它拆成两条腿 —— j≤9 天的样本位移对全样本 IC 的影响上界 ≈ 9×0.25/4054 ≈ 6e-4，
  由 N6（|full| ≥ 1e-4 且符号与原序一致）保证参照不搬家。

判据 9 格（N0~N8），读数 8 行，三张表：`noise_floor.csv`（每档噪声底分布 + 经验 p 值）、
`phase_shift.csv`（每档 × j=0..9 的合计）、`factor_phase.csv`（{8,12,17,25,30} 五档 × 21 条 × 10 个 j
的逐因子读数——只有合计会丢"掉的是谁"，而那一列才是能不能升成判据的依据）。任何一格红 ⇒ exit=1。
恒真防护（两臂负对照，红集**先声明后跑**、逐字对表）：
  · `RN_NEGCTL=deadrng`：每次打乱都拿同一枚种子 ⇒ 200 次塌成 1 个取值，噪声底这把尺
    当场没有分辨力 ⇒ **期望红集 {N0, N1}**（其余七格与随机化无关，必须照旧绿）。
  · `RN_NEGCTL=nocut`：平移做成不生效（恒 j=0）⇒ 相位表十列逐字相同 ⇒ **期望红集 {N0, N7}**
    （N5 零点锚仍该绿：j=0 那列本来就该等于真序，这条专门证明 N5 不是靠挪动才绿的）。
  · N1 写成「k=30 档取值集合 >1 **且** p95 ≥ 1」两条都要：只写「>1」会被"0 与 1 各半"
    这种极低底蒙过去；只写「p95≥1」会被常数 1 蒙过去。
  · N5/N7 分别锚住"平移臂的零点"与"平移臂真的在挪"——缺一条，另一条都可能恒真。
  · 最深档 k=30 与次深 k=25 是**噪声底已经咬上来**的区域（p50 打到 3~5 条），所以 N1 只在
    那里测"随机化在动"；浅档（k≤6）噪声恒 0 不能当牙用 —— 那是构造，不是尺子坏。
⚠️ 三处 pandas/numpy 写法坑（09-30 这一场实踩两次才走通，写在这里防下一次）：
  ① `not <DataFrame>` 当场 ValueError ⇒ 只能写 `.empty`；这个坑最狠的地方是它**在显示行**，
     两张梯子表都已经边算边打出来了、`checks.csv` 却整张没落（判据全绿的一次跑也会这样死）。
  ② `df.itertuples()` 给 namedtuple，按中文列名 `r['折数']` 取值是 TypeError ⇒ 明细串走
     `zip(df["折数"], df["跨j取值集合"])`。
  ③ 容差一律取入口 `SAME_RULER_TOL=1e-15`，**不取 1e-18 的"逐位相等"**：段均值是求和再除，
     打乱顺序就有 ~1e-17 的结合律噪声（本场 N2 实测 6.939e-18，正是这个量级）。
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
import run_ashare_rolling_ic as R                                # noqa: E402  复用 _folds/_sign/SAME_RULER_TOL 单点

RES = f"{ROOT}/stock/v1/data/results"
_BASE_OUT = f"{ROOT}/stock/v1/temp/tmp_roll_folds_noise_0930"
NEGCTL = os.environ.get("RN_NEGCTL", "")
# 每一臂各写各的目录（09-30「乙」实踩：两臂共用一个 OUT 时后跑的会把前跑的梯子表就地盖掉）
OUT = _BASE_OUT if not NEGCTL else f"{_BASE_OUT}_negctl_{NEGCTL}"
T_START = time.time()
os.makedirs(OUT, exist_ok=True)
CHECKS = []

DAILY = os.path.join(RES, "ashare_ic_daily.csv")
ARCHIVE = os.path.join(RES, "ashare_rolling_ic.csv")
YEARLY = os.path.join(RES, "ashare_ic_yearly.csv")
PROD_FOLDS = R.FOLDS
BARS = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 17, 20, 25, 30]
N_PERM = int(os.environ.get("RN_NPERM", "200"))
SHIFTS = list(range(0, 10))
SEED0 = 20260930
FULL_FLOOR = 1e-4          # N6：平移后全样本 IC 的绝对值下界（小于它则符号参照不可比）
EXPECT_RED = {"": set(), "deadrng": {"N1"}, "nocut": {"N7"}}
# 归档版本条件（**先声明、不随读数变**）：折数 09-30 拨到 25 后、归档**重跑之前**，权威归档
# 还是 5 折那一版 ⇒ 身份闸 N0 在任何一臂里都该红。10-01 已重跑 `run_ashare_rolling_ic.py`
# 把三列 `fold_*` 追平到 25 折版 ⇒ **这里清成空集**，红集回到上面那三档声明；
# 留着不清 = 以后真把 N0 弄坏了也不会被"多红"抓到。再拨这根旋钮时重新武装成 {"N0"}。
STALE_ARCHIVE_RED = set()
EXPECT_RED = {k: (v | STALE_ARCHIVE_RED if k else set(STALE_ARCHIVE_RED))
              for k, v in EXPECT_RED.items()}
if NEGCTL not in EXPECT_RED:
    raise SystemExit(f"[作废] RN_NEGCTL 只认 {sorted(k for k in EXPECT_RED if k)}，给的是 {NEGCTL!r}")


def check(cid, label, ok, detail):
    CHECKS.append((cid, label, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} [{cid}] {label}｜{detail}", flush=True)
    return ok


def readout(label, detail):
    print(f"  ·  [{label}·读数] {detail}", flush=True)


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


# ---------------------------------------------------------------- 折腿（复刻，由 N0 定资格）
def fold_leg(vals, k, sg=None):
    """入口 stability_stats 里折腿那三行的复刻：返回 (同号占比, 段最小, 段末, 段均值表)。

    段切法与符号函数都调入口自己的 `_folds` / `_sign`；只有「按段取均值再比符号」这三行是
    手写，因为 200×13×21 次调用都走完整 `stability_stats()` 会把折数无关的 252 日滚动窗
    重算四千多遍。资格由 N0 钉：真序 k=生产档 时三列必须与权威归档逐位对上。
    """
    s = pd.Series(vals)
    full = float(s.mean())
    if sg is None:
        sg = R._sign(full)
    fm = R._folds(s, k)
    same = float(np.mean([R._sign(v) == sg for v in fm]))
    return same, float(np.min(fm)), float(fm[-1]), full


def fold_leg_perm(vals, k, rng, sg):
    """噪声底用：切之前先打乱时间顺序（其余口径与 fold_leg 逐字相同）"""
    perm = rng.permutation(np.arange(len(vals)))
    v = vals[perm]
    s = pd.Series(v)
    fm = R._folds(s, k)
    return float(np.mean([R._sign(x) == sg for x in fm])), float(s.mean())


def n_piece_sizes(n, k):
    """零复刻：`np.array_split` 的段长度应当是「前 n%k 段各多 1」这个整除式子"""
    base, rem = divmod(n, k)
    sizes = [base + 1] * rem + [base] * (k - rem)
    return [x for x in sizes if x > 0]


def main():
    print(f"[配置] 进程实读 ASHARE_ROLL_FOLDS={PROD_FOLDS} WINDOW={R.WINDOW} "
          f"打乱次数 B={N_PERM} 折档 {BARS} 平移 j={SHIFTS[0]}..{SHIFTS[-1]}", flush=True)
    print(f"[负对照] RN_NEGCTL={NEGCTL!r} ⇒ 期望红集 {sorted(EXPECT_RED[NEGCTL])}", flush=True)
    if not (os.path.exists(DAILY) and os.path.exists(ARCHIVE)):
        print(f"[作废] 缺产物 daily={os.path.exists(DAILY)} archive={os.path.exists(ARCHIVE)}")
        return 1

    d = pd.read_csv(DAILY, parse_dates=["date"]).set_index("date")
    arc = pd.read_csv(ARCHIVE).set_index("name")
    names = [n for n in arc.index if f"rank|{n}" in d.columns]
    if not names:
        print("[作废] 归档花名在 daily 宽表一列都对不上")
        return 1
    stamp = lambda p: time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
    print(f"[输入] daily {d.shape[0]} 天 × {d.shape[1]} 列｜归档 {len(arc)} 行 "
          f"span={arc['span'].min()}~{arc['span'].max()}（{int(arc['n_years'].max())} 个自然年）｜"
          f"mtime daily={stamp(DAILY)} archive={stamp(ARCHIVE)}", flush=True)

    # 逐因子：只留 rank 口径（折腿主线），日期与值一起 dropna 后转 float64 ndarray
    arr, cal = {}, {}
    for n in names:
        s = d[f"rank|{n}"].dropna()
        arr[n] = s.to_numpy(dtype="float64")
        cal[n] = s.index
    n_live = {n: len(arr[n]) for n in names}
    print(f"[序列] {len(names)} 条，天数 {min(n_live.values())}~{max(n_live.values())}"
          f" ⇒ 折数 >{min(n_live.values())} 的档不会出现（本场折档最大 {max(BARS)}）", flush=True)
    t0 = time.time()

    # ---------- N0 身份闸：我的复刻 == 权威归档（真序、不平移、生产档） ----------
    max_d, worst = 0.0, ""
    for n in names:
        same, mn, last, full = fold_leg(arr[n], PROD_FOLDS)
        for col, got in (("fold_same_pct", same), ("fold_min", mn),
                         ("fold_last", last), ("rank_ic_full", full)):
            dd = abs(got - float(arc.loc[n, col]))
            if dd > max_d:
                max_d, worst = dd, f"{n[:26]}·{col}"
    n0_ok = max_d <= R.SAME_RULER_TOL
    check("N0", f"身份闸：折腿**复刻**在生产档 k={PROD_FOLDS} 的三列读数必须与权威归档逐位对上"
                f"（容差 {R.SAME_RULER_TOL:.0e}）⇒ 否则后面 200 次打乱与 10 档平移都不作数",
          n0_ok, f"{len(names)}/{len(names)} 条已比｜max|Δ|={max_d:.3e}（最大在 {worst}）")
    if not n0_ok and not NEGCTL:
        print("⇒ 复刻与归档不同版，噪声底与相位两把尺同时作废，不出一个读数")
        return finish()

    # ---------- N2 前置：打乱不改内容（参照符号在全场不动） ----------
    rng_probe = np.random.default_rng(SEED0)
    d_full, d_sign = 0.0, 0
    for n in names:
        sg = R._sign(float(pd.Series(arr[n]).mean()))
        _, pf = fold_leg_perm(arr[n], PROD_FOLDS, rng_probe, sg)
        d_full = max(d_full, abs(pf - float(pd.Series(arr[n]).mean())))
        d_sign += int(R._sign(pf) != sg)
    check("N2", "打乱只改顺序、不改内容：同一批数打乱后重算的全样本 IC 必须与原序对上"
                "（同一枚种子、每因子一次）⇒ 噪声底与真序用的是同一个符号参照",
          d_full <= R.SAME_RULER_TOL and d_sign == 0,
          f"max|Δ|={d_full:.3e}｜符号翻转 {d_sign}/{len(names)} 条（容差 {R.SAME_RULER_TOL:.0e}）")

    # ---------- N3 k=1 真值控制（打乱臂）：只有一段，必须恰与全样本同号 ----------
    rng1 = np.random.default_rng(SEED0 + 1)
    bad3, mx3 = [], 0.0
    for n in names:
        sg = R._sign(float(pd.Series(arr[n]).mean()))
        same, _ = fold_leg_perm(arr[n], 1, rng1, sg)
        mx3 = max(mx3, abs(same - 1.0))
        if same != 1.0:
            bad3.append(n)
    check("N3", "真值控制：k=1 时打乱也只能有一段 ⇒ 同号占比必须恰为 1.0（与顺序无关）",
          not bad3, f"翻脸 {len(bad3)} 条{bad3[:2]}｜max|Δ|={mx3:.3e}")

    # ---------- N4 段长度零复刻：不调 array_split 的那条整除式子 ----------
    bad4, cells4 = [], 0
    for k in BARS:
        for n in names:
            cells4 += 1
            got = [len(x) for x in np.array_split(np.arange(n_live[n]), k) if len(x)]
            if got != n_piece_sizes(n_live[n], k):
                bad4.append((k, n))
    check("N4", "分段形状**零复刻**：各段天数必须等于整除式子「前 n%k 段各多 1」，"
                "且非空段数 = min(k, n)（不靠 `array_split` 自证）",
          not bad4, f"错位 {len(bad4)}/{cells4} 格{bad4[:2]}｜式子来源=divmod(n, k)")

    # ---------- 尺一：噪声底分布（B 次打乱 × 每档） ----------
    print(f"\n===== 尺一·噪声底（B={N_PERM} 次独立打乱；「打脸」= 同号占比 <1.0 的条数）=====",
          flush=True)
    sg_by = {n: R._sign(float(pd.Series(arr[n]).mean())) for n in names}
    floor_rows, floor_store = [], {}
    for k in BARS:
        real_cnt = sum(abs(fold_leg(arr[n], k, sg_by[n])[0] - 1.0) >= 1e-15 for n in names)
        cnts = []
        for b in range(N_PERM):
            # deadrng 臂：每次取同一枚种子 ⇒ 200「次」其实是同一次，分布应当塌成一个点
            b_eff = 0 if NEGCTL == "deadrng" else b
            rng = np.random.default_rng(SEED0 + 7919 * b_eff)
            c = 0
            for n in names:
                same, _ = fold_leg_perm(arr[n], k, rng, sg_by[n])
                c += int(abs(same - 1.0) >= 1e-15)
            cnts.append(c)
        cnts = np.asarray(cnts)
        p_emp = (1 + int((cnts >= real_cnt).sum())) / (N_PERM + 1)
        floor_store[k] = (real_cnt, cnts)
        row = {"折数": k, "生产档": k == PROD_FOLDS,
               "段长中位(天)": int(np.median([round(n_live[n] / k) for n in names])),
               "真序打脸条数": real_cnt,
               "噪声均值": round(float(cnts.mean()), 3),
               "噪声p50": float(np.percentile(cnts, 50)),
               "噪声p90": float(np.percentile(cnts, 90)),
               "噪声p95": float(np.percentile(cnts, 95)),
               "噪声max": int(cnts.max()),
               "噪声=0场次比": round(float((cnts == 0).mean()), 3),
               "经验p值": round(p_emp, 5),
               "净分辨力(真序-p95)": int(real_cnt - np.percentile(cnts, 95))}
        floor_rows.append(row)
        print("  " + f"{k:>2}折 段长{row['段长中位(天)']:>4}天 真序{real_cnt:>2}条 "
              f"噪声[{cnts.min()}~{cnts.max()}] 均值{cnts.mean():.2f} "
              f"p95={row['噪声p95']:.1f} p={p_emp:.4f} 净{row['净分辨力(真序-p95)']:+d}",
              flush=True)
    fl = pd.DataFrame(floor_rows)
    fl.to_csv(f"{OUT}/noise_floor.csv", index=False)

    # ---------- N1 随机化在动（这把尺的牙） ----------
    k_deep = max(BARS)
    cnts_deep = floor_store[k_deep][1]
    check("N1", f"随机化在动（牙）：最深档 k={k_deep} 的 {N_PERM} 次噪声底必须**散开**"
                "（取值集合 >1 且 p95 ≥ 1）⇒ 两条件都要，缺一会被常数或极低底蒙过去",
          len(set(cnts_deep.tolist())) > 1 and float(np.percentile(cnts_deep, 95)) >= 1,
          f"取值 {sorted(set(cnts_deep.tolist()))}｜均值 {cnts_deep.mean():.2f} "
          f"p95 {np.percentile(cnts_deep, 95):.1f}（真序 {floor_store[k_deep][0]} 条）")

    # ---------- 尺二：相位（起点平移 j=0..9） ----------
    print(f"\n===== 尺二·相位（整段起点挪 j 天 ⇒ `array_split` 每一刀都搬家）=====", flush=True)
    # KEY_KS：把逐因子明细也存下来的那几档（其余档只存合计，否则 factor_phase.csv 白胖一圈）
    KEY_KS = {8, 12, 17, 25, 30}
    phase_rows, factor_phase = [], []
    for k in BARS:
        per_j = {}
        for j in SHIFTS:
            # nocut 臂：把平移做成空操作（恒 j=0）⇒ 相位表十列应当逐字相同，N7 必须翻红
            jj = 0 if NEGCTL == "nocut" else j
            cnt = 0
            for n in names:
                vv = arr[n][jj:]
                sg = R._sign(float(pd.Series(vv).mean()))
                same, _, _, _ = fold_leg(vv, k, sg)
                cnt += int(abs(same - 1.0) >= 1e-15)
                if k in KEY_KS:
                    factor_phase.append({"折数": k, "平移j": j, "name": n,
                                         "fold_same_pct": same, "打脸": int(abs(same - 1.0) >= 1e-15)})
            per_j[j] = (cnt, str(cal[names[0]][jj].date()),
                        int(np.median([round((n_live[n] - jj) / k) for n in names])))
        cs = [per_j[j][0] for j in SHIFTS]
        phase_rows.append({"折数": k, "生产档": k == PROD_FOLDS, "段长中位(天)": per_j[0][2],
                           "真序(j=0)打脸": per_j[0][0],
                           **{f"j={j}": per_j[j][0] for j in SHIFTS},
                           "跨j取值集合": "|".join(str(x) for x in sorted(set(cs))),
                           "跨j是否搬家": len(set(cs)) > 1,
                           "首段起始日 distinct": len({per_j[j][1] for j in SHIFTS})})
        print("  " + f"{k:>2}折 " + " ".join(f"j{j}={per_j[j][0]}" for j in SHIFTS)
              + f"  取值{sorted(set(cs))}", flush=True)
    ph = pd.DataFrame(phase_rows)
    ph.to_csv(f"{OUT}/phase_shift.csv", index=False)
    fp = pd.DataFrame(factor_phase)
    fp.to_csv(f"{OUT}/factor_phase.csv", index=False)

    # ---------- N5 零点锚：j=0 那一列必须逐位等于真序（否则相位账单是挪错轴给的） ----------
    bad5, mx5 = [], 0.0
    for n in names:
        sg = R._sign(float(pd.Series(arr[n]).mean()))
        a = fold_leg(arr[n], PROD_FOLDS, sg)
        b = fold_leg(arr[n][0:], PROD_FOLDS, sg)
        mx5 = max(mx5, abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
        if abs(a[0] - b[0]) > R.SAME_RULER_TOL:
            bad5.append(n)
    check("N5", "零点锚：相位臂 j=0 必须与真序逐位相等 ⇒ 平移的实现只在 j>0 上动，"
                "没有把整条序列挪错",
          not bad5, f"不等 {len(bad5)} 条{bad5[:2]}｜max|Δ|={mx5:.3e}"
          f"（容差 {R.SAME_RULER_TOL:.0e}）")

    # ---------- N6 符号参照可比：平移后 |full| 不塌、符号不翻 ----------
    bad6a, bad6b, mn6 = [], [], 9.0
    for n in names:
        sg = R._sign(float(pd.Series(arr[n]).mean()))
        for j in SHIFTS[1:]:
            f = float(pd.Series(arr[n][j:]).mean())
            mn6 = min(mn6, abs(f))
            if abs(f) < FULL_FLOOR:
                bad6a.append((n, j))
            if R._sign(f) != sg:
                bad6b.append((n, j))
    check("N6", f"相位臂的符号参照可比：j=1..9 重算的全样本 IC 必须 |值| ≥ {FULL_FLOOR:.0e}"
                " 且符号与原序一致 ⇒ 否则「同号」两臂不是同一个参照",
          not bad6a and not bad6b,
          f"|full| 最小 {mn6:.4f}｜低于地板 {len(bad6a)} 格｜符号翻转 {len(bad6b)} 格"
          f"（共 {len(names)}×{len(SHIFTS) - 1} 格）")

    # ---------- N7 相位构造自证：首段起始日必须十个各不同（这条只测我的尺子，不测世界） ----------
    bad7 = ph[ph["首段起始日 distinct"] != len(SHIFTS)]
    # ⚠️ 三处同类坑一起钉死：① `not <DataFrame>` 当场 ValueError（09-30 连踩两次，第二次已经
    #    把两张梯子表算完了才崩，checks.csv 整张丢失）⇒ 只能写 `.empty`；② 明细串不能从
    #    `itertuples()` 的 namedtuple 按中文列名 [] 取值；③ 表要边算边打，判据别攒到最后。
    mv7 = "、".join(f"{int(k)}折{v}" for k, v in zip(ph["折数"], ph["跨j取值集合"]))
    check("N7", f"相位构造自证：每档的 j=0..{SHIFTS[-1]} **首段起始日**必须有 "
                f"{len(SHIFTS)} 个不同取值 ⇒ 栅格真的搬过家（红=平移被做成空操作）",
          bad7.empty, f"塌成同一天 {len(bad7)}/{len(ph)} 档{list(bad7['折数'])[:3]}"
          f"｜跨 j 取值集合（只报不判）：{mv7}")

    # ---------- 读数（八行，都不判） ----------
    peak = fl.loc[fl["净分辨力(真序-p95)"].idxmax()]
    readout("分辨力峰", f"{int(peak['折数'])} 折（段长中位 {int(peak['段长中位(天)'])} 天）"
            f"：真序 {int(peak['真序打脸条数'])} 条 vs 噪声 p95 {peak['噪声p95']:.1f} 条"
            f"（max {int(peak['噪声max'])}）、经验 p={peak['经验p值']:.5f}")
    sig = fl[(fl["经验p值"] < 0.05)]
    readout("经验 p<0.05 的档", f"{[int(x) for x in sig['折数']]}" if len(sig) else
            "无（没有任何一档的真序打脸超过噪声底）")
    readout("噪声底什么时候开始咬", f"段长越短、纯属切碎的打脸越多：各档噪声 p50~max "
            + "、".join(f"{int(k)}折[{int(lo)}~{int(hi)}]"
                        for k, lo, hi in zip(fl["折数"], fl["噪声p50"], fl["噪声max"])))
    moved = ph[ph["跨j是否搬家"]]
    readout("相位搬家面", f"{len(moved)}/{len(ph)} 档的打脸条数会随起点挪动而变"
            f"（不搬家的档：{[int(x) for x in ph[~ph['跨j是否搬家']]['折数']]}）"
            "⇒ 搬家多说明「几折」读的是栅格位置而不是因子脾气")
    surv = (fp.groupby(["折数", "name"])["打脸"].agg(["sum", "size"]))
    rob = surv[(surv["sum"] == surv["size"])].reset_index().groupby("折数")["name"].apply(list)
    readout("相位幸存者（跨 j=0..9 全程打脸的因子）",
            "；".join(f"{int(k)} 折 {len(v)} 条 {v}" for k, v in rob.items())
            if len(rob) else "没有一档存在全程打脸的因子")
    grid_only = surv[(surv["sum"] > 0) & (surv["sum"] < surv["size"])].reset_index()
    readout("只在一些 j 上打脸（＝栅格给的）",
            "；".join(f"{int(k)} 折 {len(v)} 条" for k, v in
                      grid_only.groupby("折数")["name"].apply(list).items())
            if len(grid_only) else "无")
    y_ref = int(arc["n_years"].max())
    readout("与带判决那两根对齐的参考", f"自然年腿 = {y_ref} 段（≈{max(n_live.values()) // y_ref} 交易日/段）；"
            f"滚动一年腿 = {R.WINDOW} 天/段、窗心每天挪 ⇒ 那两根都不含人工折数")

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    readout("成本", f"墙钟 {time.time() - t0:.1f}s、峰值 RSS {rss:.0f}MB、零面板"
          f"（B={N_PERM} 次打乱 × {len(BARS)} 档 × {len(names)} 条 = "
          f"{N_PERM * len(BARS) * len(names):,} 次段均值）")

    # ---------- N8 边界自证：本场只写 temp ----------
    touched = [p for p in (DAILY, ARCHIVE, YEARLY) if os.path.getmtime(p) >= T_START]
    check("N8", "边界自证：三张生产产物 mtime 必须早于本进程启动"
            "（只读取样口径与「乙」一致）", not touched,
          f"被本场动过 {len(touched)} 个{touched}｜本场唯一落盘面 {OUT}")
    return finish()


if __name__ == "__main__":
    sys.exit(main())
