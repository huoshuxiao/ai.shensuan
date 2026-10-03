# -*- coding: utf-8 -*-
"""㉘ 复验：配额档的措辞真的被 f-string 求值并渲染出来，且看板上那串统计量是**现算**的。

三类断言，各挡一类真出过的错：
  ① 静态针：配额那一行的开关名与席位（挡「f-string 没求值就贴上去」）；
  ② **派生断言**：看板「多头腿结论」那行里的中位数/负行数，脚本自己从
     `ashare_portfolio_eval.csv` 按行内 `gate`+`list_scheme` 现算再与屏幕文本比 ——
     这条**不许写成常量**，否则 ㉘ 的派生改造等于没验；
  ③ 反向针：旧口径（全局档 569 天那批）的两个死数字不许还挂在活着的句子旁边
     （-0.98% 是当时的全表中位；它现在是 -1.25%，屏幕上再出现就说明还有一处写死）。

只读渲染，不落任何生产文件。用 /usr/bin/python3.10。
"""
import os
import sys

import pandas as pd
from streamlit.testing.v1 import AppTest

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401  裸模块名导入的前提
from config import ASHARE_LIST_QUOTA, ASHARE_PORT_OUT  # noqa: E402
from ashare_screen import BUY_EXPR  # noqa: E402

at = AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=300)
at.run()

blob = []
for attr in ("markdown", "caption", "text", "info", "warning", "error",
             "title", "header", "subheader"):
    for el in getattr(at, attr, []):
        blob.append(str(getattr(el, "value", "")))
all_text = "\n".join(blob)

fails = []


def chk(name, ok, got=""):
    print(f"{'✅' if ok else '❌'} {name}" + (f"　got={got}" if got else ""))
    if not ok:
        fails.append(name)


chk("看板整页无异常", len(at.exception) == 0,
    "；".join(str(e.value)[:120] for e in at.exception))

# ① 静态针：口径表「观察名单形状」那一行
seats = " / ".join(f"{k} {v}" for k, v in ASHARE_LIST_QUOTA.items())
chk("口径表有「观察名单形状」这一行", "| 观察名单形状 |" in all_text)
chk("该行念出开关名 quota", "STOCK_LIST_SCHEME`=`quota" in all_text)
chk("该行念出席位表", seats in all_text, seats)
chk("该行两边账单都在（+13.04% 与 +0.11%）",
    "+13.04%" in all_text and "+1.55% → **+0.11%" in all_text)

# ② 派生断言：屏幕上的中位数/负行数 == 脚本自己从产物算出来的
p = pd.read_csv(ASHARE_PORT_OUT)
sub = p[(p["gate"] == "board") & (p["list_scheme"] == "quota")]
ex = sub["excess_univ_ew_ann"]
med, nneg = f"{ex.median():+.2%}", int((ex < 0).sum())
line = [t for t in blob if "多头腿结论" in t]
chk("「多头腿结论」那行渲染出来了", bool(line), f"{len(line)} 处")
if line:
    chk(f"那行的中位数是现算的 {med}", med in line[0], line[0][:200])
    chk(f"那行的「{len(sub)} 行里 {nneg} 行为负」也是现算的",
        f"{len(sub)} 行里 {nneg} 行为负" in line[0])
    chk("那行自报归档档位", "`board`" in line[0] and "quota" in line[0])
    px = sub[sub["signal"].str.contains("Price")]
    ax = sub[sub["expr"] == BUY_EXPR]
    want = (f"换手 {px['one_way_turnover'].min():.2f}~{px['one_way_turnover'].max():.2f}",
            f"（本轴 {ax['one_way_turnover'].min():.2f}~{ax['one_way_turnover'].max():.2f}")
    chk("那行对照/本轴的换手段落与产物一致（不是写死的 0.08~0.10 / 0.162~0.186）",
        all(w in line[0] for w in want), " ｜ ".join(want))

# ③ 反向针：旧口径死数字不该还挂在活句子上
chk("全表中位旧读数 -0.98% 已不在屏上", "-0.98%" not in all_text)
chk("名单腿旧读数 -3.98% 已不在屏上", "-3.98%" not in all_text)

print(f"\n共 {len(fails)} 条不过" + ("，全过 ✅" if not fails else "：" + "、".join(fails)))
sys.exit(1 if fails else 0)
