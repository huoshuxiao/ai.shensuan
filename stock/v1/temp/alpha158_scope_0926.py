# -*- coding: utf-8 -*-
"""选项I 的账单第一步：**不取数**，只把 qlib 的 158 条定义摊出来，按本线 DSL 的能力分档

为什么要这一步：158 条不是 158 个「可跑的表达式」。本线 DSL（`common/src/core/factor_dsl.py:16-45`）
只有 13 个算子，`ma/std/max/min` 还钉死作用于 close；面板只有 OHLCV+$factor，没有
`$vwap`/`$amount`。所以「Alpha158 全市场扫描」的真实工作量 =
（现有 DSL 直接可写）+（直译一行）+（要扩算子）+（**数据缺口，扩算子也写不出**），
逐条归类才有代价。

只 import qlib 的配置（不 init、不 fetch），几十 MB，可与其他作业并行。
"""
import re
import sys
from collections import Counter

sys.path.insert(0, "/home/sunwenkun/miniconda3/envs/rdagent/lib/python3.10/site-packages")

from qlib.contrib.data.loader import Alpha158DL   # noqa: E402

exprs, names = Alpha158DL.get_feature_config()     # (表达式列表, 列名列表)
print(f"[定义] Alpha158DL 给出 {len(names)} 列")

# 本线 DSL 现状（手抄 factor_dsl.py:16-45；改 DSL 要同步这里，否则账单会骗人）
OK_MAP = {"Ref": "delay", "Abs": "abs", "Log": "log", "Sign": "sign",
          "Mean": "ts_mean", "Std": "ts_std", "Sum": "ts_sum", "Delta": "delta",
          "Rank": "rank"}
DSL_COLS = {"close", "open", "high", "low", "volume"}
# 不用新语义、一行就翻译得出来的（逐点比较/乘方）
LITE = {"Greater": "np.maximum(a,b)", "Less": "np.minimum(a,b)", "Power": "a**b"}
# qlib 里 Max/Min 是**滚动**极值（`qlib/data/ops.py:951 class Max(Rolling)`、`:999 class Min(Rolling)`），
# 本线的 `max/min` 也是滚动，但**钉死作用于 close**（factor_dsl.py:30-31）
# ⇒ 这一档不是「缺算子」，是「把已有窗口算子放宽到任意列」，改法与新增语义不同档
GENERALIZE = {"Max": "rolling max 作用于非 close 列（本线 max 钉死 close）",
              "Min": "rolling min 作用于非 close 列（本线 min 钉死 close）"}
NEED_NEW = {
    "Corr": "两列 rolling 相关（CORR/CORD/CINV）", "Cov": "两列 rolling 协方差",
    "Beta": "rolling 回归系数", "Rsquare": "rolling 回归 R²",
    "Resi": "rolling 回归残差", "Slope": "rolling 回归斜率",
    "IdxMax": "窗口内 argmax 位置（IMAX/IMXI）", "IdxMin": "窗口内 argmin 位置（IMIN）",
    "Quantile": "rolling 分位阈值（QTLU/QTLD）", "EMA": "指数加权均值（LEMA）",
    "WMA": "线性加权均值", "SumIf": "窗口内条件求和（SUMP/SUMN/SUMZ）",
    "Count": "窗口内条件计数（CNTP/CNTN/CNTB）",
    "GMAX": "两列各自 rolling 后取大", "GMIN": "两列各自 rolling 后取小",
}

rows = []
for nm, ex in zip(names, exprs):
    funcs = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", ex))
    cols = {c.lower() for c in re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", ex)}
    miss_col = sorted(cols - DSL_COLS)
    heavy = sorted(funcs & set(NEED_NEW))
    gen = sorted(funcs & set(GENERALIZE))
    lite = sorted(funcs & set(LITE))
    odd = sorted(funcs - set(OK_MAP) - set(LITE) - set(NEED_NEW) - set(GENERALIZE))
    if miss_col:
        tier = "戊 数据缺口（面板没有这一列，扩算子也写不出）"
    elif heavy:
        tier = "丙 要新增语义（相关/回归/分位/位置族）"
    elif gen:
        tier = "乙 只要把窗口极值放宽到任意列（本线 max/min 钉死 close）"
    elif lite:
        tier = "甲′ 直译一行即可（逐点比较/乘方）"
    elif odd:
        tier = "己? 出现未登记函数，人工看一眼"
    else:
        tier = "甲 现有 DSL 直接可写"
    rows.append((tier, nm, ex, ",".join(miss_col), ",".join(heavy + gen), ",".join(odd)))

cnt = Counter(r[0] for r in rows)
print("\n===== 分档计数（合计 %d）=====" % sum(cnt.values()))
for t in sorted(cnt):
    print(f"  {t:<44} {cnt[t]:>4} 列")

for t in sorted(cnt):
    sub = [r for r in rows if r[0] == t]
    print(f"\n----- {t}：{len(sub)} 列 -----")
    print("  " + "  ".join(r[1] for r in sub[:70]))
    if len(sub) > 70:
        print(f"  …另有 {len(sub) - 70} 列")

print("\n----- 乙/丙档：用到的缺口算子各被多少列用到 -----")
op_cnt = Counter()
for tier, nm, ex, mc, heavy, odd in rows:
    for o in heavy.split(","):
        if o:
            op_cnt[o] += 1
for o, c in op_cnt.most_common():
    print(f"  {o:<10} {c:>3} 列　{NEED_NEW.get(o) or GENERALIZE.get(o)}")
print(f"  ⇒ 其中「真新语义」{len([o for o in op_cnt if o in NEED_NEW])} 个；"
      f"「放宽到任意列」{len([o for o in op_cnt if o in GENERALIZE])} 个")

print("\n----- 戊档：缺的列各被多少列用到 -----")
col_cnt = Counter()
for tier, nm, ex, mc, heavy, odd in rows:
    for o in mc.split(","):
        if o:
            col_cnt[o] += 1
print(f"  {dict(col_cnt)}")

print("\n----- 戊?（未登记函数，若非空说明分类器漏了）-----")
odd_cnt = Counter()
for tier, nm, ex, mc, heavy, odd in rows:
    for o in odd.split(","):
        if o:
            odd_cnt[o] += 1
print(f"  {dict(odd_cnt) or '无'}")
