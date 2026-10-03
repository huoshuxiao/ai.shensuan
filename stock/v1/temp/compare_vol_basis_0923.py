"""量能族口径对照：面板 $volume（复权成交量）vs 真实手数（$volume×$factor）

$volume 的语义已在 shell/probe_live_sources4_0923.py 定案 = 真实手数 / $factor，
所以「量能族只用 $volume，不经过 factor」这句原话是错的：票级 1/factor 中位 8.4、
p99 143、最大 1152 —— 复权基准会混进截面分位。这一跑判的就是那条载荷结论
「量能族只做剔除、剔除增益 +4%/年」到底站在真成交量上，还是站在 1/$factor 上。

三份产物对照（列名一律从真实 CSV 表头读，不猜）：
  已入库 ashare_portfolio_eval.csv   = 闸门 bug 版（成交额按盘面价算，虚高 25 倍）
  /tmp/port_basis_adj.csv            = 闸门修正 + 面板口径 volume
  /tmp/port_basis_real.csv           = 闸门修正 + 真手数 volume
"""
import pandas as pd

COMMITTED = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
             "results/ashare_portfolio_eval.csv")
ADJ = "/tmp/port_basis_adj.csv"
REAL = "/tmp/port_basis_real.csv"
KEY = ["signal", "kind", "top_n"]
EXCL = "excl_worst_vs_pool_ann"      # 剔除增益：不买最差五分位 vs 全池
LONG = "excess_univ_ew_ann"          # 多头腿超额（低分侧做多）
COLS = [EXCL, LONG, "excess_univ_ew_ir", "q5_ann", "one_way_turnover",
        "avg_amount_20d", "universe", "factor_coverage"]

d = {}
for tag, path in (("bug闸门", COMMITTED), ("面板量", ADJ), ("真手数", REAL)):
    x = pd.read_csv(path)
    d[tag] = x
    print(f"[{tag:5s}] {path}  {len(x)} 行  可投池均值 {x.universe.mean():,.0f}  "
          f"篮子均额 {x.avg_amount_20d.mean() / 1e8:,.2f} 亿")

m = d["面板量"].set_index(KEY)[[c for c in COLS if c in d["面板量"]]]
r = d["真手数"].set_index(KEY)[[c for c in COLS if c in d["真手数"]]]
j = m.join(r, lsuffix="_面板量", rsuffix="_真手数").reset_index()

print("\n" + "=" * 100)
print("① 载荷结论：剔除增益（相对全池，年化）逐档对照")
print(j[["signal", "top_n", f"{EXCL}_面板量", f"{EXCL}_真手数",
         f"q5_ann_面板量", f"q5_ann_真手数"]].to_string(
    index=False, float_format=lambda v: f"{v:+.4f}"))

print("\n② 按因子聚合（三档 top_n 平均）")
g = j.groupby(["kind", "signal"]).agg(
    剔除_面板量=(f"{EXCL}_面板量", "mean"), 剔除_真手数=(f"{EXCL}_真手数", "mean"),
    多头_面板量=(f"{LONG}_面板量", "mean"), 多头_真手数=(f"{LONG}_真手数", "mean"),
    换手_面板量=("one_way_turnover_面板量", "mean"),
    换手_真手数=("one_way_turnover_真手数", "mean"))
g["剔除增益差"] = g["剔除_真手数"] - g["剔除_面板量"]
g["多头超额差"] = g["多头_真手数"] - g["多头_面板量"]
print(g.to_string(float_format=lambda v: f"{v:+.4f}"))

print("\n③ 闸门修正本身（bug 闸门版 vs 修正版，同为面板量口径，逐列）")
c = d["bug闸门"].set_index(KEY)[[EXCL, LONG, "universe", "avg_amount_20d"]].join(
    d["面板量"].set_index(KEY), lsuffix="_bug", rsuffix="_fix", how="inner")
for col in (EXCL, LONG, "universe", "avg_amount_20d"):
    a, b = c[f"{col}_bug"], c[f"{col}_fix"]
    print(f"  {col:26s} 中位位移 {(b - a).median():+,.5f}  绝对最大 {(b - a).abs().max():,.5f}"
          f"  符号翻转 {int((a * b < 0).sum())}/{len(c)}  "
          f"（bug 版中位 {a.median():,.4f} → 修正 {b.median():,.4f}）")

print("\n④ 名字集合是否一致（三条跑的因子清单必须相同，否则对照无意义）")
s = {k: set(map(tuple, d[k][KEY].values)) for k in d}
for a in s:
    for b in s:
        if a < b and s[a] != s[b]:
            print(f"  !! {a} 与 {b} 不同：仅前者 {sorted(s[a] - s[b])[:3]} "
                  f"仅后者 {sorted(s[b] - s[a])[:3]}")
print("  （无输出 = 三份因子×档位集合完全一致）")
