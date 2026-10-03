# -*- coding: utf-8 -*-
"""看板「✍️ 录成交 / 出账」那一条路的端到端回归（09-24 #117）

为什么用 `streamlit.testing.AppTest` 而不是只点浏览器：它**真的把 app.py 跑完一遍**
（表单提交回调 → `append_fill` 追加流水 → 子进程 `run_ashare_position.py` 出账 →
`load_csv.clear()` → `st.rerun()` → 页面顶部回显），只是不经过 CDP。浏览器那条只验
渲染，这一条验的是「按下去到底写了什么、出账到底通不通」。

四条分支，缺一条就是回归：
  A 正常一笔（买入 100 股 @ 当日下单名单第 1 槽的 close）⇒ 退出码 0、持仓 CSV 落盘、
    页面回显出账账单；顺带核对**平均成本 = (成交价×100 + 补算费用)/100**（费率那套
    判据全在出账入口里，页面零重算，所以这个数对不上就是页面动了判据）
  B 坏的一笔（买入 150 股，不是 100 的整数倍）⇒ 退出码 1、**整轮不出账**（持仓 CSV
    字节不变）、**这一行留在流水里**（入口语义是等人按行号改，页面不许替它删账）
  B' 把那一行改对后点「只重新出账」⇒ 退出码 0、两只都在持仓里；并且报错里的**行号
    就是文件里数得着的那一行**（模板那行 # 注释会把 index+2 的推算带歪一行）
  C 逐字相同的一行连点两次（同一个 session）⇒ 第二次被挡下，流水不加行。挡人不靠
    秒数窗口：出账一步实测 36 秒，任何小于它的时间窗都在出账跑完之前就失效了，
    等于没挡（09-24 第一版就是这么写的，被这一条测出来）
  C' 把备注写开 ⇒ 同一笔的第二份成交照样录进去了（挡住的是「重复」不是「第二笔」）

账本全部写到本脚本旁边的 `tmp_fill_smoke_0924/`（`STOCK_FILLS_CSV` / `STOCK_POSITION_OUT` /
`STOCK_ACCOUNT_OUT` 三个覆写），**不碰 `data/live/` 那笔真钱账**；收尾再核一次真钱
文件的 mtime 早于冒烟，作为「没写歪」的证据而不是口号。
**成交日一律从产物派生**（取该场 `meta.panel_end`，即面板的数据尽头），不填「今天」：
今天完全可以晚于数据尽头，那时入口按判据拒绝出账（「成交日晚于数据尽头，账没法估」）
—— 那是产品对、夹具过期（09-27 回归就是被这一条咬到的）。
用 /usr/bin/python3.10。
"""
import datetime as dt
import os
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
# 冒烟账本落在脚本自己旁边的 stock/v1/temp/ 下（09-24 首版写的是 `/tmp/fill_smoke_0924/`）：
# 一是本机纪律「一次性产物不进 /tmp」，二是 /tmp 会被清，清了这份脚本就在半路
# FileNotFoundError 崩掉、把 A 分支真正的失败原因盖住（09-27 回归就是这样）。
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_fill_smoke_0924")
FILLS, POS, ACCT = (os.path.join(TMP, n) for n in
                    ("manual_fills.csv", "positions.csv", "account.csv"))
REAL = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/live/manual_fills.csv"
T0 = os.path.getmtime(REAL)
os.makedirs(TMP, exist_ok=True)
for p in (FILLS, POS, ACCT):
    os.path.exists(p) and os.remove(p)
sys.path.insert(0, SRC)
os.environ.update(STOCK_FILLS_CSV=FILLS, STOCK_POSITION_OUT=POS, STOCK_ACCOUNT_OUT=ACCT)

import glob
import json

import pandas as pd
from streamlit.testing.v1 import AppTest

SIG_DIR = os.path.abspath(os.path.join(SRC, "..", "data", "results", "daily_signal"))
_od = sorted(glob.glob(os.path.join(SIG_DIR, "order_*.csv")))
if not _od:
    raise SystemExit(f"[无产物] {SIG_DIR} 下没有 order_*.csv")
TAG = os.path.basename(_od[-1])[6:14]                    # order_YYYYMMDD.csv
_m = os.path.join(SIG_DIR, f"meta_{TAG}.json")
# 成交日必须**从产物派生**，不能用「今天」：09-27 回归时这条就是死在这里 ——
# 表单默认填今天，而数据尽头还停在 09-24，出账入口按判据拒绝（"成交日晚于数据尽头
# 没法估账，先补行情"）。那是**产品正确**，是夹具把「今天 == 面板最后一天」写死了。
DAY = json.load(open(_m))["panel_end"] if os.path.exists(_m) else \
    f"{TAG[:4]}-{TAG[4:6]}-{TAG[6:]}"
DAY_ISO = str(pd.Timestamp(DAY).date())
print(f"[这一场] 名单 {TAG}｜成交日记成 {DAY_ISO}（=面板数据尽头，不是今天）")
ORDER = pd.read_csv(_od[-1], dtype={"code": str})
CODE, PX = str(ORDER.iloc[0]["code"]), float(ORDER.iloc[0]["close"])
CODE2, PX2 = str(ORDER.iloc[1]["code"]), float(ORDER.iloc[1]["close"])
print(f"[照名单下单] 第 1 槽 {CODE} @ {PX}、第 2 槽 {CODE2} @ {PX2}")

fails = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))
    if not cond:
        fails.append(name)


def page():
    return AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=600).run()


def one(items, label):
    got = [w for w in items if str(getattr(w, "label", "")) == label]
    assert len(got) == 1, f"标签「{label}」匹配到 {len(got)} 个控件"
    return got[0]


def submit(at, code, qty, px, note=""):
    one(at.text_input, "代码 SH/SZ/BJ + 6 位").set_value(code)
    one(at.number_input, "数量（股）/ 现金金额").set_value(qty)
    one(at.number_input, "成交价（盘面真实价，非复权价）").set_value(px)
    one(at.date_input, "成交日期").set_value(dt.date.fromisoformat(DAY_ISO))
    one(at.text_input, "备注").set_value(note)
    one(at.button, "录入并出账").click()
    return at.run()


def echo(at):
    return "\n".join(str(getattr(e, "value", "")) for e in at.error) + "\n" + \
           "\n".join(str(getattr(w, "value", "")) for w in at.warning) + "\n" + \
           "\n".join(str(getattr(c, "value", "")) for c in at.code) + "\n" + \
           "\n".join(str(getattr(s, "value", "")) for s in at.success)


def nrows():
    return len([ln for ln in open(FILLS, encoding="utf-8-sig").read().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")])


print("\n===== A 正常一笔：买入 100 股 =====")
at = page()
submit(at, CODE, 100, PX)
a = echo(at)
print("  [页面回显]" + " | ".join(a.splitlines()[:12]))
check("流水文件被生成并落两行（表头 + 这一笔）", nrows() == 2, f"实测 {nrows()} 行")
check("出账退出码 0", "退出码 0" in a)
check("持仓 CSV 落盘且含这只", os.path.exists(POS) and CODE in open(POS, encoding="utf-8-sig").read())
if not os.path.exists(POS):
    # 下面每一条 check 都要读这张表；出账没落盘时先在这里停下，把出账原话整段贴出来。
    # 09-27 回归时它就是崩在第一个 read_csv 上，traceback 盖住了 A 分支真正报的错。
    print("\n[A 分支没出账 ⇒ 后面几条无从判起] 出账/页面原话：\n" + a)
    raise SystemExit(1)
p = pd.read_csv(POS, dtype={"代码": str}).set_index("代码")
check("平均成本 = (成交价×100 + 最低佣金 5 元)/100",
      abs(p.loc[CODE, "平均成本"] - (PX * 100 + 5) / 100) < 1e-6,
      f"实际 {p.loc[CODE, '平均成本']:.4f}")
check("笔数 1 / 持股 100 / 账户有现金欠款行", int(p.loc[CODE, "笔数"]) == 1
      and int(p.loc[CODE, "持股数"]) == 100 and os.path.exists(ACCT))

print("\n===== B 坏的一笔：买入 150 股（不是 100 的整数倍）=====")
before = open(POS, encoding="utf-8-sig").read()
b = echo(submit(at, CODE2, 150, PX2))
print("  [出账原话]" + " | ".join([ln for ln in b.splitlines() if "行" in ln][:3]))
check("报错点到这一笔（含 150 那句）", "150" in b and "不是 100 的整数倍" in b)
check("整轮不出账：持仓 CSV 一个字节没改", open(POS, encoding="utf-8-sig").read() == before)
check("坏行**留在**流水里（页面不替入口删账）", nrows() == 3)

print("\n===== B' 改对那一行后点「只重新出账」=====")
rows = open(FILLS, encoding="utf-8-sig").read().splitlines()
i = [k for k, ln in enumerate(rows) if ln.startswith(f"{DAY_ISO},{CODE2},")][-1]
rows[i] = rows[i].replace(",150,", ",100,")
open(FILLS, "w", encoding="utf-8").write("\n".join(rows) + "\n")
at2 = page()
one(at2.button, "只重新出账").click()
at2.run()
bb = echo(at2)
check("改完再出账：退出码 0", "退出码 0" in bb)
check(f"两只都在持仓里", {CODE, CODE2} <= set(pd.read_csv(POS, dtype={"代码": str})["代码"]))
# 行号必须是人在文件里数得着的那一行（模板自带一行 # 注释，早先用 index+2 推算会整体错一行）
check(f"报错点到第 {i + 1} 物理行（不是往前错一行）", f"第 {i + 1} 行" in b,
      f"坏行在物理第 {i + 1} 行，出账原话：{[ln for ln in b.splitlines() if '整数倍' in ln][:1]}")

print("\n===== C 同一会话逐字相同的一行连点两次 =====")
n0 = nrows()
submit(at2, CODE, 100, PX)                      # 与 A 那一行逐字相同 ⇒ 先落一行
submit(at2, CODE, 100, PX)                      # 再点一次 ⇒ 该被挡
n1 = nrows()
check("两次点击只多出一行（第二次挡下）", n1 - n0 == 1, f"流水 {n0} → {n1} 行")
check("挡下时页面说的是「逐字相同」", "逐字相同" in echo(at2))

print("\n===== C' 把备注写开，同一笔第二份该录进去 =====")
submit(at2, CODE, 100, PX, note="第二笔")
check("备注不同 ⇒ 落第二行", nrows() - n1 == 1, f"流水 {n1} → {nrows()} 行")

print("\n===== 收尾：真钱账本没被碰 =====")
check("data/live/manual_fills.csv mtime 早于本轮冒烟开始", os.path.getmtime(REAL) == T0,
      f"{dt.datetime.fromtimestamp(T0):%H:%M:%S} 未变")
print(f"\n{'全部通过' if not fails else '失败：' + '、'.join(fails)}　（{len(fails)} 项）")
raise SystemExit(1 if fails else 0)
