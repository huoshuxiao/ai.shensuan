# -*- coding: utf-8 -*-
"""一次性校验：用 Streamlit 官方 headless 测试器把看板整页跑一遍。

为什么不用浏览器截图：streamlit 的 tab 是客户端挂载，未选中的 tab 在 DOM 里根本
不存在，visibility 也为 0x0（本机 in-app browser 无 viewport），指针事件点不动。
AppTest 走的是 script runner，`with tabs[i]:` 里的代码**全部执行**，所以能真正
验证「因子库页的盘前接点列」和「剔除页的 meta 驱动构造行」这两处改动是否报错、
渲染出的文案对不对。只读 CSV，不落任何盘。
"""

import os
import sys

from streamlit.testing.v1 import AppTest

# 一次性脚本的家在本线 temp/（09-29 前叫 shell/stock/），脚本一挪位置「src 在哪」就不能
# 再按 __file__ 的兄弟目录拼 —— 09-27 回归时它正是死在拼出了 `<脚本所在目录>/v1/src`
# 这种不存在的路径上。改成从脚本所在目录往上逐层找真正装着 stock/v1/src 的那一层。
_here = os.path.dirname(os.path.abspath(__file__))
for _up in range(1, 6):
    _cand = os.path.abspath(os.path.join(_here, *([".."] * _up), "stock", "v1", "src"))
    if os.path.isdir(_cand):
        SRC = _cand
        break
else:
    raise SystemExit(f"[路径] 从 {_here} 往上 5 层里找不到 stock/v1/src")
sys.path.insert(0, SRC)
os.chdir(SRC)
from streamlit.testing.v1 import AppTest
# AppTest.from_file 的相对路径是按**本文件**所在目录解析的，不是 cwd，故给绝对路径
at = AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=300).run()

print(f"退出码级异常 {len(at.exception)} 个")
for e in at.exception:
    print("  ❌", e.value, "\n", (e.stack_trace or "")[-1500:])

print(f"\ntab 数 {len(at.tabs)}")
for i, t in enumerate(at.tabs):
    print(f"  [{i}] {t.label}")

# 因子库页（tabs[3]）：三块 metric + 表里的「盘前接点」列
print("\n===== 因子库页 metric =====")
for m in at.tabs[3].metric:
    print(f"  {m.label} = {m.value}　help={m.help!r}")

df = at.tabs[3].dataframe[0].value
print(f"\n表 {df.shape}，列 = {list(df.columns)}")
assert "盘前接点" in df.columns, "盘前接点列没渲染出来"
j = df[df["盘前接点"] != "未进判据"][["name", "盘前接点"]]
print("\n已进判据：")
print(j.to_string(index=False))
print(f"\n未进判据 {int((df['盘前接点'] == '未进判据').sum())} 条")
caps = [c.value for c in at.tabs[3].caption]
print("\ncaption：")
for c in caps:
    print("  •", c.replace("\n", " ")[:230])

# 剔除页（tabs[2]）：meta 驱动的「本日启用构造」行
print("\n===== 剔除页 =====")
for c in at.tabs[2].caption:
    if "本日启用构造" in c.value:
        print("  " + c.value)
for m in at.tabs[2].metric:
    print(f"  metric {m.label} = {m.value}")
print("\nmarkdown:", at.tabs[2].markdown[0].value.replace("\n", " ")[:200])

print("\n结果：", "有异常" if at.exception else "全页无异常")
sys.exit(1 if at.exception else 0)
