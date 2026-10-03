"""真实数据下的看板复验：不带任何 STOCK_* 覆盖，读的就是生产路径。

0923 那份 apptest 是配着 shell/demo_*.csv 跑的，验的是「有持仓时长什么样」；
这份验的是另一件更要紧的事：**没有真实成交时，页面必须走生产级空态**，
既不能崩、也不能把 demo 账户的 1,009,301 之类的数字当成真实数据显示出去。

判据三条：8 个 tab 无异常 / 每张表都有数据（0 行=口径没对上）/
持仓空态文案在、demo 指纹数字不在。用 /usr/bin/python3.10。

NEEDLE 从「还没建账」换成「空仓」：09-24 把空仓提成一等状态之后，页面在
「没建账 + 流水 0 笔」下走的不再是缺失引导分支，而是「当前**空仓**」这条。
"""
import sys
from streamlit.testing.v1 import AppTest

APP = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src/app.py"
DEMO_FINGERPRINT = "1,009,301"     # shell/demo_account.csv 的总资产
NEEDLE = "空仓"

at = AppTest.from_file(APP, default_timeout=300)
at.run()
print("tabs:", [t.label for t in at.tabs] if hasattr(at, "tabs") else "n/a")
print("exceptions:", len(at.exception))
for e in at.exception:
    print("  !!", e.value)

# 页面上所有可见文本拼一份，用来同时判「空态在」与「demo 数字不在」
blob = []
for attr in ("markdown", "caption", "text", "info", "warning", "error", "metric"):
    for el in getattr(at, attr, []):
        v = getattr(el, "value", None)
        if isinstance(v, str):
            blob.append(v)
        elif v is not None:
            blob.append(str(v))
for d in at.dataframe:
    v = getattr(d, "value", None)
    if v is not None:
        blob.append(" ".join(map(str, list(v.columns))))
all_text = "\n".join(blob)

print(f"dataframes={len(at.dataframe)}  metrics={len(at.metric)}  "
      f"tables={len(at.table)}  captions={len(at.caption)}")
empty_df = [i for i, d in enumerate(at.dataframe)
            if getattr(d, "value", None) is not None and len(d.value) == 0]
print("空表 index:", empty_df or "无")
print("空态文案在:", NEEDLE in all_text)
print("demo 指纹泄漏:", DEMO_FINGERPRINT in all_text)

# 真实链路那几个数必须还在（与 meta_20260923.json 对齐），否则说明读的不是真数据
for must in ("5,225", "2,297", "2928", "50 只"):
    print(f"  真实信号数 {must}:", must in all_text)

bad = bool(at.exception) or bool(empty_df) or (NEEDLE not in all_text) \
    or (DEMO_FINGERPRINT in all_text)
sys.exit(1 if bad else 0)
