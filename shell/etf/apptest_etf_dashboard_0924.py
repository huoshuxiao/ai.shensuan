# -*- coding: utf-8 -*-
"""一次性校验：用 Streamlit 官方 headless 测试器把 ETF 线看板整页跑一遍。

不用浏览器截图的原因同股票线那份（`check_dashboard_render_0924.py`）：tab 是客户端
挂载，未选中的 tab 在 DOM 里不存在。AppTest 走 script runner，`with tabs[i]:` 里的
代码全部执行 ⇒ 能真验到本轮新加的「合并样本外 DSR」段（在统计验证 tab 内）。
只读产物、不落盘、不碰 8502 上那份股票线看板。
"""

import os
import sys

from streamlit.testing.v1 import AppTest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir,
                                   "etf", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

APP = os.path.join(SRC, "app.py")
at = AppTest.from_file(APP, default_timeout=600).run()

print(f"异常 {len(at.exception)} 个")
for e in at.exception:
    print("  ❌", e.value)

metrics = [(m.label, str(m.value)) for m in at.metric]
print(f"\n指标卡 {len(metrics)} 张")
for label, value in metrics:
    print(f"  {label:<28} {value}")

want = ("拼接段数 / bar", "年化夏普", "DSR", "运气门槛年化", "累计试验 N")
oos = [(l, v) for l, v in metrics if l in want]
print(f"\n合并样本外段命中 {len(oos)} 张：{oos}")
assert oos, "统计验证 tab 里没找到合并样本外 DSR 的指标卡"

texts = [str(getattr(t, "value", "")) for t in at.markdown]
hit = [t for t in texts if "合并样本外 DSR" in t]
print(f"小标题命中 {len(hit)} 处：{hit[:1]}")
assert hit, "缺「合并样本外 DSR」小标题"
captions = [str(getattr(c, "value", "")) for c in at.caption]
skew = [c for c in captions if "偏度" in c]
print(f"高阶矩脚注 {skew}")
print("\nOK" if not at.exception else "\nFAILED")
sys.exit(1 if at.exception else 0)
