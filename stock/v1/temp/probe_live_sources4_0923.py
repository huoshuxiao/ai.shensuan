# -*- coding: utf-8 -*-
"""实盘数据源实测 第四弹：$volume 的真实语义（这一条能翻掉一条已入库结论）

第二弹的观测：源成交量 / 面板 $volume 的比值**逐票是一个常数**，但在票间从 0.70 散布到 106.7，
不是「源给股、面板给手」那个整齐的 100。三个候选解释：
    H1  $volume = 真实手数 L                      → 比值应恒为 100
    H2  $volume = L / $factor（复权成交量）        → 比值 = 100×$factor
        茅台 24.32 vs factor 0.2432、寒武纪 0.70 vs 1/142=0.0070×100=0.70、
        中石化 106.7 vs factor 1.067 —— 三票全中，H2 目前领先
    H3  源与面板的日期错位/停牌 → 比值散乱无结构

为什么必须当场定性而不是"回头再看"：如果 H2 成立，
    真成交额 A = 盘面价 R × L × 100 = R × (V·$factor) × 100 = **C × V × 100**（C=复权价）
即**复权价乘 $volume 才是对的**，那 09-23 把 amount20 改成「盘面价 × $volume × 100」
是把闸门乘上了 1/$factor（老分红股 factor 0.24 → 成交额被抬 4 倍 → 闸门对它们放水），
方向正好反了。更狠的是量能族四条构造吃的是 $volume 本身：H2 下 $volume 内含 1/$factor，
「放量」里混着「分红除权史」，而 `ashare_screen` 的数据口径第 2 条写的是
「量能族不经过价格与 factor，所以不受失真影响」—— 那句话若错，需要连文档一起改。

判据设计成不依赖任何单位假设：拿新浪给的**成交额 A**（元，自报口径）分别除以两套价×V，
看哪一套在**每一只票上**都≈1，以及残差与 $factor 的相关。
"""
import socket
import time

socket.setdefaulttimeout(30)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

PANEL = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
         "rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5")
START, END = "20260901", "20260922"      # 只取面板也有的日子
N_SAMPLE = 60

print("读面板…")
p = pd.read_hdf(PANEL)
p = p[[c for c in p.columns if c.startswith("$")]]
p.columns = [c.lstrip("$") for c in p.columns]
insts = np.array(sorted(p.index.get_level_values("instrument").unique()))
rng = np.random.default_rng(20260923)
# 分层抽样：按 $factor 的十分位各取几只，否则随机 60 只几乎全是 factor≈1 的次新股，
# 那时候 H1 与 H2 给同样的数（factor=1 时两者不可分辨），这一跑就成了白跑
fac = p.reset_index("instrument").groupby("instrument")["factor"].last()
fac = fac.reindex(insts)
qs = pd.qcut(fac.rank(method="first"), 10, labels=False)
pool = []
for q in range(10):
    cand = np.array([i for i in insts[qs.reindex(insts).values == q]])
    if len(cand):
        pool += list(rng.choice(cand, size=min(6, len(cand)), replace=False))
pool = [str(i) for i in pool]
print(f"  抽样 {len(pool)} 只，factor 范围 {fac.reindex(pool).min():.4f}~"
      f"{fac.reindex(pool).max():.4f}（含 1 以上与极小的两头）")


def sym_of(inst):
    return {"SH": "sh", "SZ": "sz", "BJ": "bj"}[inst[:2]] + inst[2:]


rows = []
t0 = time.time()
for k, inst in enumerate(pool, 1):
    try:
        d = ak.stock_zh_a_daily(symbol=sym_of(inst), start_date=START, end_date=END,
                                adjust="")
    except Exception:
        continue
    if d is None or not len(d):
        continue
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index("date")[["close", "volume", "amount"]].astype("float64")
    pan = p.xs(inst, level="instrument")
    common = d.index.intersection(pan.index)
    if len(common) < 10:
        continue
    dd, pp = d.loc[common], pan.loc[common]
    C = pp["close"].astype("float64")            # 复权收盘
    f = pp["factor"].astype("float64")
    V = pp["volume"].astype("float64")           # 面板量（待定性）
    R = C / f.where(f > 0)                       # 盘面价
    A = dd["amount"]                             # 新浪自报成交额（元）
    L = dd["volume"] / 100.0                     # 若源给股 → 手数
    rows.append(pd.DataFrame({
        "inst": inst, "date": common, "factor": f.values,
        "价相对差": (dd["close"].values / R.values - 1),
        "A除C乘V乘100": A.values / (C.values * V.values * 100.0),
        "A除R乘V乘100": A.values / (R.values * V.values * 100.0),
        "V除L": V.values / L.values,
        "V乘f除L": (V * f / L).values,
    }))
m = pd.concat(rows, ignore_index=True)
print(f"  {time.time() - t0:.0f}s，得到 {len(m):,} 个 (票,日) 对，覆盖 {m.inst.nunique()} 只")

print("=" * 78)
print("① 价格口径复核（源不复权收盘 vs 面板 $close/$factor）")
print(f"   |相对差| max {m['价相对差'].abs().max():.2e}  p99 {m['价相对差'].abs().quantile(0.99):.2e}"
      f"  → {'盘面价公式确认' if m['价相对差'].abs().max() < 1e-4 else '价格对不上，后面不必看'}")

print("\n② 成交额自洽判据：哪一套价乘 $volume 能得到源成交额")
for c in ("A除C乘V乘100", "A除R乘V乘100"):
    g = m.groupby("inst")[c].median()
    print(f"   {c}: 逐票中位数的分布 p1~p99 {g.quantile(0.01):.4f}~{g.quantile(0.99):.4f}"
          f"  落在 1±2% 内的票 {int(((g - 1).abs() < 0.02).sum())}/{len(g)}"
          f"  逐票散布(最大/最小) {g.max() / g.min():,.0f}×")

print("\n③ $volume 与源手数之比：H1 预测恒=1/factor? 还是 H2 的常数 100?")
g = m.groupby("inst").agg(factor=("factor", "median"), VoverL=("V除L", "median"),
                          VfoverL=("V乘f除L", "median"))
print(f"   V/L      与 1/factor 的比值：逐票 p1~p99 "
      f"{(g['VoverL'] * g['factor']).quantile(0.01):.3f}~{(g['VoverL'] * g['factor']).quantile(0.99):.3f}"
      f"（H2 成立则恒≈1；中位 {(g['VoverL'] * g['factor']).median():.4f}）")
print(f"   V×f/L    逐票 p1~p99 {g['VfoverL'].quantile(0.01):.3f}~{g['VfoverL'].quantile(0.99):.3f}"
      f"  落 1±5% 内 {int(((g['VfoverL'] - 1).abs() < 0.05).sum())}/{len(g)}")
print(f"   若 H1（V=手数）则 V/L 应≈100 恒定：实测 V/L 逐票中位 "
      f"{g['VoverL'].min():.2f}~{g['VoverL'].max():.2f}，与 factor 的 corr "
      f"{np.corrcoef(np.log(g['VoverL']), np.log(g['factor']))[0, 1]:+.3f}")

print("\n④ 抽样明细（按 factor 排序，看两头）")
det = g.sort_values("factor")
print(pd.concat([det.head(6), det.tail(6)]).to_string(
    float_format=lambda v: f"{v:,.4f}"))

print("\n判读：'A除C乘V乘100' 每票≈1 而 'A除R乘V乘100' 逐票随 factor 漂移 ⇒ H2 成立"
      "（$volume 内含 1/factor，复权价才是配它的那套价）；反之 H1 成立。")
