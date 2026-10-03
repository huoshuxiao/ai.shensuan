"""无头复验股票线看板：7 个 tab 全跑一遍，只判「有没有异常」+「每张表有没有数据」。

列名靠猜会渲染出静默空图（框在、数没有，streamlit 不抛异常），所以这里额外数
st.dataframe 的条数，0 条即视为口径没对上。用 /usr/bin/python3.10（看板线的解释器）。
"""
import sys
from streamlit.testing.v1 import AppTest

APP = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src/app.py"

at = AppTest.from_file(APP, default_timeout=300)
at.run()
print("tabs:", [t.label for t in at.tabs] if hasattr(at, "tabs") else "n/a")
print("exceptions:", len(at.exception))
for e in at.exception:
    print("  !!", e.value)
print(f"dataframes={len(at.dataframe)}  metrics={len(at.metric)}  "
      f"tables={len(at.table)}  captions={len(at.caption)}")
empty_df = [i for i, d in enumerate(at.dataframe)
            if getattr(d, "value", None) is not None and len(d.value) == 0]
print("空表 index:", empty_df or "无")
sys.exit(1 if at.exception or empty_df else 0)
