# -*- coding: utf-8 -*-
"""涨跌幅上限按板块拆：那道 0.095 的「贴涨停」闸今天误伤了多少只（09-24）

背景：用户 09-24 确认**个人账户科创板 / 创业板 / 北交所均有权限**，所以「段约束」只能
当分散度用，不能当排除用。顺着这条查生产判据，发现第三道执行闸（`buy_candidates`
里「当日收盘涨幅 ≥ ASHARE_PORT_LIMIT_UP 不追」）用的是**单一阈值 0.095**，那是主板
±10% 的近似。对另外三段它就是误伤：

    主板      ±10%  → 0.095 判「贴板」，对
    科创板    ±20%     ┐
    创业板    ±20%     ├ 涨 9.5%~19.9% 的名字被当成涨停剔掉，其实离板还远
    北交所    ±30%     ┘

这一跑只回答两个数，不动代码：
 1. 保留池（`signal_*.csv` 的 keep 列）里有多少票落在这段「被误伤」的区间；
 2. 三段票在保留池 / 生产 50 只名单里各占多少 —— B 路的分散约束将来就在这些票里做取舍。

涨幅取**外部快照**（`spot_YYYYMMDD.csv` 的「涨跌幅」列，百分数），不是面板复权 pct_change：
除权日两者会差一笔分红，但这里数的是「离自己的涨停板还差多少」，盘面涨跌幅才是那个量。
"""
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))

import pandas as pd

import _bootstrap  # noqa: F401
from config import ASHARE_SIGNAL_DIR, ASHARE_SNAPSHOT_DIR, ASHARE_PORT_LIMIT_UP

SIG_DIR = ASHARE_SIGNAL_DIR if os.path.isabs(ASHARE_SIGNAL_DIR) else os.path.join(
    os.path.join(ROOT, "stock/v1"), ASHARE_SIGNAL_DIR)
SNAP_DIR = ASHARE_SNAPSHOT_DIR if os.path.isabs(ASHARE_SNAPSHOT_DIR) else os.path.join(
    os.path.join(ROOT, "stock/v1"), ASHARE_SNAPSHOT_DIR)
sig = sorted(os.path.join(SIG_DIR, f) for f in os.listdir(SIG_DIR) if f.startswith("signal_"))[-1]
d = os.path.basename(sig)[7:15]
buy = os.path.join(SIG_DIR, f"buy_{d}.csv")
snap = os.path.join(SNAP_DIR, f"spot_{d}.csv")

LIMIT = {"主板": 0.10, "科创板": 0.20, "创业板": 0.20, "北交所": 0.30}


def seg(code):
    """面板 instrument（SZ/SH/BJ 前缀）-> 板块。科创板看 688/689，创业板看首位 3。"""
    s = str(code).upper()
    if s.startswith("BJ"):
        return "北交所"
    if s.startswith("SH"):
        return "科创板" if s[2:5] in ("688", "689") else "主板"
    return "创业板" if s[2] == "3" else "主板"


sp = pd.read_csv(snap, encoding="utf-8-sig")
sp["code"] = sp["代码"].astype(str).str[:2].str.upper() + sp["代码"].astype(str).str[2:]
# .to_numpy() 是必需的：直接喂 Series 会被 pandas 按旧索引（0..N）对齐到新索引，
# 结果整列静默变 NaN —— 第一版就是这么把「误伤 0 只」读出来的
chg = pd.Series(sp["涨跌幅"].astype(float).to_numpy() / 100.0,
                index=sp["code"].to_numpy()).sort_index()
sg = pd.Series([seg(c) for c in chg.index], index=chg.index)

s = pd.read_csv(sig, dtype={"code": str}).set_index("code")
keep = s[s["keep"].astype(bool)]
print(f"[输入] {os.path.basename(sig)}：可投池 {len(s)} 只、保留池 {len(keep)} 只；"
      f"快照 {os.path.basename(snap)} 涨跌幅 {len(chg)} 只")

# 1 误伤计数：保留池内、涨幅过了 0.095 那道线、但离自己板块的板还远
rows = []
for name in ("主板", "科创板", "创业板", "北交所"):
    m = keep[sg.reindex(keep.index).eq(name).to_numpy()]
    c = chg.reindex(m.index)
    past = c.ge(ASHARE_PORT_LIMIT_UP)                    # 现在会被第三道闸剔掉
    hurt = past & c.lt(LIMIT[name] * 0.95)               # 且其实没贴自己的板（留 5% 余量）
    rows.append({"段": name, "保留池只数": len(m), "闸内(≥0.095)": int(past.sum()),
                 "其中未贴板=误伤": int(hurt.sum()),
                 "池内涨幅中位": f"{c.median():.2%}", "池内涨幅最大": f"{c.max():.2%}"})
t = pd.DataFrame(rows)
print(f"\n===== 1 第三道闸（单一阈值 {ASHARE_PORT_LIMIT_UP}，主板 ±10% 的近似）按段拆开 =====")
print(t.to_string(index=False))
tot_past = int(t["闸内(≥0.095)"].sum())
tot_hurt = int(t["其中未贴板=误伤"].sum())
print(f"合计：当日因涨幅过线被挡 {tot_past} 只，其中**按各自板块限幅算并未贴板** {tot_hurt} 只")

# 2 三段票在保留池与生产名单里的占比：B 路的分散约束要在这批票里做取舍
b50 = pd.read_csv(buy, dtype={"code": str})
print(f"\n===== 2 段构成（保留池 {len(keep)} 只 vs 生产名单 {os.path.basename(buy)} {len(b50)} 只）=====")
kp = sg.reindex(keep.index).value_counts()
bp = sg.reindex(b50["code"]).value_counts()
for name in ("主板", "科创板", "创业板", "北交所"):
    print(f"  {name:<5}：保留池 {int(kp.get(name, 0)):4d} 只 ({kp.get(name, 0) / len(keep):.1%})　"
          f"名单 {int(bp.get(name, 0)):2d} 只")
print(f"\n[口径注] 涨幅来自外部快照（盘面），与生产判据用的复权 pct_change 在除权日会差一笔分红；"
      f"「未贴板」留了 5% 余量（即 ≥ 板限×0.95 才算贴板），不做贴板边缘的精细判断。")

# ---------- 3 这一天没咬人，历史上咬不咬？全面板数「被误当涨停」的格子 ----------
# 第 1 节只看了信号日当天（保留池涨幅最高 7.25%，一个都没过线）⇒ 单一阈值的代价是
# **潜在**的：普涨日才显形。所以把整段历史的 (票,日) 格子都数一遍，按年给频次。
print("\n===== 3 历史上「涨幅过 0.095 但没贴自己的板」的格子（全面板，非池内口径）=====")
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src", "strategy"))
from ashare_screen import build_matrices, guard_ret, load_panel   # noqa: E402  同生产护栏

wide, _b = load_panel()
_mtx = build_matrices(wide)
del wide
cl, raw = _mtx["close"], _mtx["raw_price"]
ret = guard_ret(cl.pct_change(fill_method=None), (cl / raw).pct_change(fill_method=None))
segs = pd.Series([seg(c) for c in cl.columns], index=cl.columns)
yr = pd.Series(cl.index.year, index=cl.index)
rows3 = []
for name in ("科创板", "创业板", "北交所"):
    cols = segs[segs.eq(name)].index
    r = ret[cols]
    past = r.ge(ASHARE_PORT_LIMIT_UP)
    hurt = past & r.lt(LIMIT[name] * 0.95)
    tot = int(hurt.sum().sum())
    per_year = (pd.DataFrame(hurt.values, index=yr.values, columns=cols)
                .groupby(level=0).sum().sum(axis=1))
    days_hit = int((hurt.any(axis=1)).sum())
    rows3.append({"段": name, "票数": len(cols), "过线格子": int(past.sum().sum()),
                  "误伤格子": tot, "误伤/过线": f"{tot / max(int(past.sum().sum()), 1):.0%}",
                  "有此事的交易日": f"{days_hit}/{len(cl.index)}"})
    top3 = ", ".join(f"{y}：{int(v)}" for y, v in per_year.sort_values(ascending=False).head(3).items())
    print(f"  {name}（{LIMIT[name]:.0%}）：过线 {rows3[-1]['过线格子']} 格、其中未贴板 "
          f"{tot} 格（{rows3[-1]['误伤/过线']}）；集中在 {top3}")
print(f"\n  即：单一阈值把所有票都按主板 ±10% 判，落在 双创/北交所 的那批「过 9.5% 却离自己"
      f"涨停还远」的格子会在候选漏斗第三步被当涨停剔掉。")
