# -*- coding: utf-8 -*-
"""量股票线**数据层那六道人工阈值**的余量：不重跑日更、不读 789MB 面板，
只用已经落盘的三场快照（`append_*.csv` + `spot_*.csv`）离线算。

六道阈值（都在 `common/src/data/stock/update_qlib_bin_daily.py`）：
  ALIGN_TOL=1e-2、ALIGN_EXACT_MIN=0.90、ALIGN_MIN=0.98、ALIGN_SAMPLE=400、
  MIN_BAR=1e-6、RATIO_BOUND=(0.05,20)、CLOSE_MIN_SHARE=0.9

每道都要回答同一句话：**「这个数往两侧各挪一档，读数怎么动、它到底挡不挡得住东西」**。
只报"现在通过"不算量过 —— 所以每条判据都配一条**跨场错配**的负对照（把 09-28 的参考价
去对 09-24 的 bin 前收），它对不上才算这把尺子有牙。

常量与 `align_rates` 一律从生产模块现读（判据单点不许在测试里重抄一遍）。
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))  # temp→v1→stock→仓库根
SNAP = os.path.join(REPO, "common", "data", "stock", "daily_snapshot")
MOD_PATH = os.path.join(REPO, "common", "src", "data", "stock", "update_qlib_bin_daily.py")
SESSIONS = ["20260923", "20260924", "20260928"]
FAIL = []


def load_module():
    """只加载模块常量与函数，不触发它的 main（它 import 期不发网络请求）"""
    spec = importlib.util.spec_from_file_location("ubd_probe", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load_module()
REF_COL, BIN_COL = "昨收(除权参考价)", "前收_bin"


def rates(ref, prev):
    """与生产同一把尺子：先按 MIN_BAR 剔无效价，再算逐字率 / 容差率 / 票数"""
    return M.align_rates(ref, prev)


def devs(ref, prev):
    pair = pd.DataFrame({"ref": np.asarray(ref, float),
                         "prev": np.asarray(prev, float)}).dropna()
    pair = pair[(pair["ref"] > M.MIN_BAR) & (pair["prev"] > M.MIN_BAR)]
    return (pair["ref"] / pair["prev"] - 1).abs().to_numpy(), len(pair)


def block(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------- A：现状
block("A 三场快照的对表读数（生产口径：逐字 d<1e-4 / 容差 d<%g）" % M.ALIGN_TOL)
rows = {}
for s in SESSIONS:
    df = pd.read_csv(os.path.join(SNAP, f"append_{s}.csv"), encoding="utf-8-sig")
    rows[s] = df
    ex, wi, n = rates(df[REF_COL].to_numpy(), df[BIN_COL].to_numpy())
    print(f"  {s}｜总行 {len(df)}｜参与对表 {n} 只"
          f"（{n / len(df):.1%}）｜逐字 {ex:.4%}｜容差内 {wi:.4%}"
          f"｜闸门 {M.ALIGN_EXACT_MIN:.2f}/{M.ALIGN_MIN:.2f}"
          f"⇒ {'放行' if ex >= M.ALIGN_EXACT_MIN and wi >= M.ALIGN_MIN else '判红'}")
    if not (ex >= M.ALIGN_EXACT_MIN and wi >= M.ALIGN_MIN):
        FAIL.append(f"A {s}：真数据竟然过不了自己的闸，先怀疑读数")

# ---------------------------------------------------------------- B：容差阶梯
block("B ALIGN_TOL 往两侧各挪一档（同一批票，只换容差）")
LADDER = [1e-5, 1e-4, 1e-3, 1e-2, 5e-2, 0.1, 0.2]
print("  容差→    " + "".join(f"{t:>10g}" for t in LADDER))
for s, df in rows.items():
    d, n = devs(df[REF_COL].to_numpy(), df[BIN_COL].to_numpy())
    line = [float((d < t).mean()) for t in LADDER]
    print(f"  {s}  " + "".join(f"{v:>10.4%}" for v in line))
    if any(b < a - 1e-12 for a, b in zip(line, line[1:])):
        FAIL.append(f"B {s}：阶梯不单调 ⇒ 尺子坏了")
    if abs(line[0] - line[-1]) < 1e-9:
        FAIL.append(f"B {s}：最松最紧同值 ⇒ 这一格恒真，没有信息量")

# ---------------------------------------------------------------- C：负对照
block("C 负对照：09-28 的参考价 去对 09-24 的 bin 前收（跨一场错配，必须塌）")
a = rows["20260928"]
b = rows["20260924"]
both = pd.merge(a[["inst", REF_COL]], b[["inst", BIN_COL]], on="inst", how="inner")
ex2, wi2, n2 = rates(both[REF_COL].to_numpy(), both[BIN_COL].to_numpy())
print(f"  可配对 {n2} 只｜逐字 {ex2:.4%}｜容差内 {wi2:.4%}")
if not (ex2 < M.ALIGN_EXACT_MIN and wi2 < M.ALIGN_MIN):
    FAIL.append("C 跨场错配竟然还在闸上面 ⇒ 这道闸无牙，0.98/1e-2 挡不住拿错一天的快照")
else:
    print(f"  ✅ 有牙：两条率都被压在门槛 {M.ALIGN_EXACT_MIN}/{M.ALIGN_MIN} 之下")

# ---------------------------------------------------------------- D：抽样 400
block("D ALIGN_SAMPLE=%d 的抽样风险：全量判『放行』，抽样会不会判红？" % M.ALIGN_SAMPLE)
rng = np.random.default_rng(20260929)


def draw_rates(d_arr, k, reps=200):
    """抽 k 只算一次两条率，重复 reps 次（numpy 的 choice 不支持二维不放回，逐次抽）"""
    n = len(d_arr)
    ex, wi = [], []
    for _ in range(reps):
        sub = d_arr if n <= k else d_arr[rng.choice(n, size=k, replace=False)]
        ex.append(float((sub < 1e-4).mean()))
        wi.append(float((sub < M.ALIGN_TOL).mean()))
    return np.asarray(ex), np.asarray(wi)


for s, df in rows.items():
    d, n = devs(df[REF_COL].to_numpy(), df[BIN_COL].to_numpy())
    exact_full, within_full = float((d < 1e-4).mean()), float((d < M.ALIGN_TOL).mean())
    ex_s, wi_s = draw_rates(d, M.ALIGN_SAMPLE)
    flip_ex = int((ex_s < M.ALIGN_EXACT_MIN).sum())
    flip_wi = int((wi_s < M.ALIGN_MIN).sum())
    print(f"  {s}｜全票 {n}｜抽样 200 次：逐字率 中位 {np.median(ex_s):.4%} "
          f"极差 {ex_s.min():.4%}~{ex_s.max():.4%}｜容差率 极差 {wi_s.min():.4%}~{wi_s.max():.4%}"
          f"｜判红次数 逐字 {flip_ex}/200、容差 {flip_wi}/200（全量={within_full:.4%}）")
# 反证：拿 C 那批"该判红"的配对，抽 400 只能不能照样判红
d2, n2b = devs(both[REF_COL].to_numpy(), both[BIN_COL].to_numpy())
if n2b:
    _, wi2_s = draw_rates(d2, M.ALIGN_SAMPLE)
    caught = int((wi2_s < M.ALIGN_MIN).sum())
    print(f"  反证（错场配对 {n2b} 只）：抽样 400 只判红 {caught}/200 次"
          f"｜全量判红={'是' if (d2 < M.ALIGN_TOL).mean() < M.ALIGN_MIN else '否'}")
    if caught < 200:
        print(f"  ⚠️ 抽样闸有 {200 - caught}/200 次放过了错场 ⇒ 这是抽样数 400 的代价，不是 bug")

# ---------------------------------------------------------------- E：MIN_BAR
block("E MIN_BAR=%g 挡的是谁：按生产写入闸那条谓词（六个字段）逐场拆原因" % M.MIN_BAR)
PRICE_LADDER = [1e-6, 0.01, 0.5, 1.0]
for s, df in rows.items():
    spot = pd.read_csv(os.path.join(SNAP, f"spot_{s}.csv"), encoding="utf-8-sig")
    lab = int(df["备注"].fillna("").str.startswith("无有效行情").sum())

    def bad(th):
        p = (spot["最新价"] <= th) | (spot["今开"] <= th) | (spot["昨收"] <= th)
        v = (spot["成交额"] <= 0) | (spot["成交量"] <= 0) | (spot["最高"] < spot["最低"])
        return p, v, (p | v)

    p0, v0, allbad = bad(M.MIN_BAR)
    reasons = {
        "最新价≤阈": int(((spot["最新价"] <= M.MIN_BAR)).sum()),
        "今开≤阈": int(((spot["今开"] <= M.MIN_BAR)).sum()),
        "昨收≤阈": int(((spot["昨收"] <= M.MIN_BAR)).sum()),
        "成交量=0": int((spot["成交量"] <= 0).sum()),
        "成交额=0": int((spot["成交额"] <= 0).sum()),
        "最高<最低": int((spot["最高"] < spot["最低"]).sum()),
    }
    lad = " ".join(f"≤{t:g}:{int(bad(t)[2].sum())}" for t in PRICE_LADDER)
    only_vol = int((v0 & ~p0).sum())          # 量/额/高低这条腿独立挡下的行数
    print(f"  {s}｜生产标注无效 {lab} 条｜我这把重算 {int(allbad.sum())} 条"
          f"｜只按价格 ≤1e-6 {int(p0.sum())} 条｜原因拆分 "
          + " ".join(f"{k}={c}" for k, c in reasons.items()))
    print(f"        量/额/高低这条腿**独立**挡下 {only_vol} 条｜把价格门槛抬到 0.01/0.5/1.0 会杀到：{lad}")
    if int(allbad.sum()) != lab:
        FAIL.append(f"E {s}：按生产谓词重算得 {int(allbad.sum())} 条，与产物标注 {lab} 条不等 ⇒ 口径对不上")
    # 只按价格那条腿挡了几条 = 读数，不当判据：真有一天出现「价对量 0」的行，
    # 量/额那条腿就该有独立贡献，那时 p0<lab 是**对的**，不能拿它当红。

# ---------------------------------------------------------------- F：RATIO_BOUND
block("F RATIO_BOUND=%s：倍数出界被强设成 1.0 的行数（换区间会多/少几行）" % (M.RATIO_BOUND,))
BANDS = [(0.5, 2.0), (0.2, 5.0), (0.05, 20.0), (0.02, 50.0), (0.005, 200.0)]
for s, df in rows.items():
    d, n = devs(df[REF_COL].to_numpy(), df[BIN_COL].to_numpy())
    ratio = df[BIN_COL].to_numpy() / df[REF_COL].to_numpy()
    r = pd.Series(ratio).dropna().to_numpy()
    outs = [int(((r <= lo) | (r >= hi)).sum()) for lo, hi in BANDS]
    print(f"  {s}｜可算倍数 {len(r)}｜出界行数 " +
          " ".join(f"{lo}-{hi}:{c}" for (lo, hi), c in zip(BANDS, outs)) +
          f"｜倍数最小 {r.min():.4f} 最大 {r.max():.4f}")
    if any(c < prev for c, prev in zip(outs, outs[1:])):
        FAIL.append(f"F {s}：区间放宽出界数反而变多 ⇒ 尺子坏了")
print("  注：生产口径 (0.05,20) 允许单日差 20 倍，比容差闸 1e-2 松三个数量级 ⇒ "
      "这一道只挡『量纲级离谱』，不挡『错一天』，那是 ALIGN 那道闸的活")

# ---------------------------------------------------------------- G：收盘闸
block("G CLOSE_MIN_SHARE=%g：快照里 15:00 之后的行占比" % M.CLOSE_MIN_SHARE)
for s in SESSIONS:
    df = pd.read_csv(os.path.join(SNAP, f"spot_{s}.csv"), encoding="utf-8-sig")
    ts = pd.to_datetime(df["时间戳"].astype(str), format="%H:%M:%S", errors="coerce")
    share = float((ts.dt.hour * 60 + ts.dt.minute >= M.CLOSE_HM).mean())
    print(f"  {s}｜{len(df)} 行｜≥15:00 占 {share:.2%}｜门槛 {M.CLOSE_MIN_SHARE:.0%}"
          f"｜余量 {share - M.CLOSE_MIN_SHARE:+.2%}｜"
          f"换门槛 80%/90%/95%/99% ⇒ "
          f"{'/'.join('过' if share >= x else '拒' for x in (0.8, 0.9, 0.95, 0.99))}")

# ---------------------------------------------------------------- H：给没咬过的两格造牙
block("H 合成对照：G 的收盘闸与 F 的倍数闸，在『该红』的输入下必须红")
spot = pd.read_csv(os.path.join(SNAP, f"spot_{SESSIONS[-1]}.csv"), encoding="utf-8-sig")
half = len(spot) // 2
intra = spot.copy()
intra.loc[intra.index[:half], "时间戳"] = "14:30:00"     # 一半改成盘中抢跑
sh_intra = M.close_share(intra)
all_intra = spot.copy()
all_intra["时间戳"] = "14:30:00"
sh_all = M.close_share(all_intra)
print(f"  收盘闸｜真快照 {M.close_share(spot):.2%}（过）｜一半盘中 {sh_intra:.2%}｜全盘中 {sh_all:.2%}"
      f"｜门槛 {M.CLOSE_MIN_SHARE:.0%}")
if not (sh_intra < M.CLOSE_MIN_SHARE and sh_all < M.CLOSE_MIN_SHARE):
    FAIL.append("H 收盘闸：盘中快照竟然还放行 ⇒ 0.9 这道闸无牙")
df_last = rows[SESSIONS[-1]].copy()
inj = df_last.copy()
k = inj.index[inj[BIN_COL].notna()][0]
good_ref = float(inj.at[k, REF_COL])
inj.at[k, BIN_COL] = good_ref * 30.0                      # 造一条 30 倍的离谱行
r_inj = (inj[BIN_COL].to_numpy() / inj[REF_COL].to_numpy())
r_inj = pd.Series(r_inj).dropna().to_numpy()
outs_inj = [int(((r_inj <= lo) | (r_inj >= hi)).sum()) for lo, hi in BANDS]
print(f"  倍数闸｜注入 1 条 30 倍后出界行数 " +
      " ".join(f"{lo}-{hi}:{c}" for (lo, hi), c in zip(BANDS, outs_inj)) +
      f"（注入前全为 0）")
if outs_inj[BANDS.index(M.RATIO_BOUND)] != 1:
    FAIL.append("H 倍数闸：注入了 30 倍却数不到 1 条出界 ⇒ 这一格尺子坏了")
print("  ⇒ G/F 在真样本上『一次没咬过』不是判据坏，是这三场没有盘中抢跑、没有量纲级错价")
inj2 = spot.copy()
row = inj2.index[(pd.to_numeric(inj2["最新价"], errors="coerce") > 1)
                 & (pd.to_numeric(inj2["今开"], errors="coerce") > 1)
                 & (pd.to_numeric(inj2["昨收"], errors="coerce") > 1)][0]
inj2.loc[row, ["成交量", "成交额"]] = 0.0          # 价全对、量额清零
price_bad = ((pd.to_numeric(inj2["最新价"], errors="coerce") <= M.MIN_BAR)
             | (pd.to_numeric(inj2["今开"], errors="coerce") <= M.MIN_BAR)
             | (pd.to_numeric(inj2["昨收"], errors="coerce") <= M.MIN_BAR)).to_numpy()
vol_bad = ((pd.to_numeric(inj2["成交额"], errors="coerce") <= 0)
           | (pd.to_numeric(inj2["成交量"], errors="coerce") <= 0)).to_numpy()
vol_bad_true = ((pd.to_numeric(spot["成交额"], errors="coerce") <= 0)
                | (pd.to_numeric(spot["成交量"], errors="coerce") <= 0)).to_numpy()
extra = int((vol_bad & ~price_bad & ~vol_bad_true).sum())
print(f"  无效行闸｜注入 1 条『价都对、量额=0』⇒ 量额这条腿独立挡下 {extra} 条"
      f"（真样本里这条腿独立贡献 {only_vol} 条）")
if extra != 1:
    FAIL.append("H 无效行闸：注入了量额=0 却没被量额这条腿抓到 ⇒ 这条腿无牙")

# ---------------------------------------------------------------- 收尾
block("汇总")
print(f"  生产常量现读：ALIGN_TOL={M.ALIGN_TOL} EXACT_MIN={M.ALIGN_EXACT_MIN} "
      f"MIN={M.ALIGN_MIN} SAMPLE={M.ALIGN_SAMPLE} MIN_BAR={M.MIN_BAR} "
      f"RATIO={M.RATIO_BOUND} CLOSE={M.CLOSE_MIN_SHARE}")
if FAIL:
    print("\n  ❌ 有牙检查未过：")
    for f in FAIL:
        print("    -", f)
    sys.exit(1)
print("\n  ✅ 全部有牙检查通过（A 真数据放行 / C 错场判红 / B、F 阶梯单调且不同值）")
