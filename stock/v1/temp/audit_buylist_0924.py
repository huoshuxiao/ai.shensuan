# -*- coding: utf-8 -*-
"""实仓前数据体检：待买入名单 50 只逐票对账（09-24）

三环裁决把「买不买」这根轴判成只能当减法用，剩下要给真钱照着下单的就是那张
`buy_YYYYMMDD.csv`。所以这一跑**不测 alpha**，只查「名单上每一个数是不是真的、
能不能成交」——五查全是可证伪的等式，任何一条不成立就是链路有 bug，不是行情变了：

 1. **盘面价**：面板 `$close/$factor` vs akshare 收盘快照「最新价」逐票相对差。
    这条是账本估值、下单参考价共同的底盘；对不上说明日更写进去的格子有错。
 2. **排序键独立复算**：名单里的「安静度」vs 直接用 pandas 对 `$volume` 做
    20 日滚动 `mean`/`std` 重算（取哪一跟着 `BUY_EXPR` 走，09-24 换过轴）。
    走的是两条代码路径（主线 DSL safe_eval vs 裸 pandas），值必须逐票相同，
    否则整个排队顺序不可信。
 3. **窗口完整性**：每只票那 20 日窗里的 NaN 格与 `$volume==0` 假线格数。
    有 NaN 本该被 `v.notna()` 挡在门外；若名单里出现空窗票 ⇒ 闸失效。
 4. **假台阶落点**：近 20 日里有没有 `|复权日收益| > RET_LIMIT(0.30)` 的格子。
    落在名单票上意味着这只票的复权序列有脏格，它的安静度值与涨幅都不该信。
 5. **可执行性**：一手金额（盘面价×100）、仙股（<1 元）、快照名称里的 ST/退 字样。

出报告不改任何数据。判据全从已落库产物读，不联网。
"""
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1"
sys.path.insert(0, SRC)

import glob
import os
import re

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401  (bare-name imports need the sys.path guide)

from config import ASHARE_SIGNAL_DIR
from ashare_screen import BUY_EXPR, RET_LIMIT, build_matrices, load_panel

SIG_DIR = os.path.join(ROOT, ASHARE_SIGNAL_DIR) if not os.path.isabs(ASHARE_SIGNAL_DIR) else ASHARE_SIGNAL_DIR
SNAP_DIR = os.path.join(ROOT, "data/daily_snapshot")

b_files = sorted(glob.glob(os.path.join(SIG_DIR, "buy_*.csv")))
assert b_files, f"{SIG_DIR} 里没有 buy_*.csv"
buy = pd.read_csv(b_files[-1], dtype={"code": str}).set_index("code")
day = os.path.basename(b_files[-1])[4:12]
s = pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}").normalize()
print(f"[名单] {os.path.basename(b_files[-1])}：{len(buy)} 只，信号日 {s.date()}")

spot_f = sorted(glob.glob(os.path.join(SNAP_DIR, "spot_*.csv")))
assert spot_f, "没有收盘快照，第 1/5 查做不了"
sp = pd.read_csv(spot_f[-1], encoding="utf-8-sig")
sp["inst"] = sp["代码"].astype(str).str.upper()
sp = sp.set_index("inst")
print(f"[快照] {os.path.basename(spot_f[-1])}：{len(sp)} 行，列 {list(sp.columns)[:8]}…")

wide, _bench = load_panel()
mtx = build_matrices(wide)
raw, cl, vol = mtx["raw_price"], mtx["close"], mtx["volume"]
assert s in raw.index, f"面板没有 {s.date()} 这一格"
codes = list(buy.index)
missing_col = [c for c in codes if c not in raw.columns]
print(f"[面板] 末格 {raw.index[-1].date()}、{raw.shape[1]} 列；名单里缺列的 {missing_col}")

# ---------- 查 1：盘面价 ----------
px_panel = raw.loc[s].reindex(codes).astype("float64")
have = [c for c in codes if c in sp.index]
px_spot = sp.loc[have, "最新价"].astype("float64")
d1 = (px_panel.reindex(have) / px_spot - 1).abs()
print(f"\n[查1 盘面价] 名单 {len(codes)} 只里快照有 {len(have)} 只；"
      f"面板/快照相对差 中位 {d1.median():.2e}、p95 {d1.quantile(.95):.2e}、最大 {d1.max():.2e}"
      f"（{d1.gt(1e-3).sum()} 只 >1e-3）")
bad1 = d1[d1 > 1e-3]
if len(bad1):
    for c, v in bad1.items():
        print(f"    ⚠️ {c} {sp.loc[c,'名称']} 面板 {px_panel[c]:.3f} vs 快照 {px_spot[c]:.3f}"
              f" 差 {v:.2%}")
not_in_spot = [c for c in codes if c not in sp.index]
print(f"    快照里查无此票：{not_in_spot or '无'}")

# ---------- 查 2：排序键独立复算 ----------
# 复算口径**从 BUY_EXPR 反解**，不写死 std：09-24 排序轴从 ts_std(volume,20) 换成
# ts_mean(volume,20)，写死就把新名单的排序键全判成错（差值大得像链路坏了）。
# 反解不出已知形态就直接停，别悄悄拿旧轴算
m = re.fullmatch(r"ts_(mean|std)\(volume,(\d+)\)", BUY_EXPR)
assert m, f"查 2 只会独立复算 ts_mean/ts_std(volume,N) 形态，当前轴 = {BUY_EXPR}"
win, v20_op = int(m.group(2)), m.group(1)
sub_v = vol.loc[:s].tail(max(40, win * 2))
v20 = getattr(sub_v.rolling(win), v20_op)().iloc[-1].reindex(codes).astype("float64")
q = buy["安静度"].astype("float64")
both = pd.concat([q.rename("csv"), v20.rename("pandas")], axis=1).dropna()
rel = (both["csv"] / both["pandas"] - 1).abs()
print(f"\n[查2 安静度复算] 轴 = {BUY_EXPR}（裸 pandas rolling({win}).{v20_op}()）；"
      f"可比 {len(both)} 只；|csv/pandas − 1| 中位 {rel.median():.2e}、"
      f"最大 {rel.max():.2e}（>1e-3 的 {(rel > 1e-3).sum()} 只）")
if (rel > 1e-3).any():
    print(both[rel > 1e-3].to_string())
# 顺序是否一致（复算重排一遍前 50）
rank_csv = list(both.index)
rank_new = list(both.sort_values("pandas").index)
print(f"    名单顺序 vs 复算顺序：完全一致 {rank_csv == rank_new}")

# ---------- 查 3：窗口完整性 ----------
w = sub_v.tail(20)[[c for c in codes if c in vol.columns]]
n_nan = int(w.isna().sum().sum())
n_zero = int((w == 0).sum().sum())
per_nan = w.isna().sum()
print(f"\n[查3 窗口完整性] 20 日窗 × {w.shape[1]} 只 = {w.size} 格："
      f"NaN {n_nan} 格、$volume==0 假线 {n_zero} 格"
      f"（含空窗的票 {int(per_nan.gt(0).sum())} 只）")
if n_nan:
    print(f"    ⚠️ 名单里有空窗票：{sorted(per_nan[per_nan > 0].index)}")

# ---------- 查 4：假台阶落点 ----------
sub_c = cl.loc[:s].tail(21)[[c for c in codes if c in cl.columns]]
ug = sub_c.pct_change(fill_method=None)
hits = (ug.abs() > RET_LIMIT)
print(f"\n[查4 假台阶] 近 20 日 |复权日收益| > {RET_LIMIT} 的格子：{int(hits.values.sum())} 个，"
      f"涉及名单票 {sorted(hits.columns[hits.any().values]) or '无'}")
big = ug.abs().max()
print(f"    名单票近 20 日最大单格涨幅：中位 {big.median():.2%}、最大 {big.max():.2%}"
      f"（{big.idxmax()}）")

# ---------- 查 5：可执行性 ----------
lot = (px_panel * 100).dropna()
name = sp.loc[sp.index.intersection(codes), "名称"]
risk = name[name.str.contains("ST|退", regex=True, na=False)]
print(f"\n[查5 可执行性] 一手金额：最小 {lot.min():,.0f} / 中位 {lot.median():,.0f} / "
      f"最大 {lot.max():,.0f} 元；价格 <1 元的 {int((px_panel < 1).sum())} 只；"
      f"20 日均成交额 最小 {mtx['amount20'].loc[s].reindex(codes).min():.3g} 元")
print(f"    快照名称含 ST/退：{len(risk)} 只 {dict(risk) if len(risk) else ''}")

amt = mtx["amount20"].loc[s].reindex(codes).astype("float64")
print(f"    名单里最贵的容量约束（20 日均额最小 5 只）："
      f"{[(c, f'{v/1e8:.2f}亿') for c, v in amt.nsmallest(5).items()]}")

print("\n[结论口径] 上面五查任何一条越界都算数据问题，不算行情变化。")
