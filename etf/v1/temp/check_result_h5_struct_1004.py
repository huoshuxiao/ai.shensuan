# -*- coding: utf-8 -*-
"""读最近几枚 result.h5 的结构读数（只读）：键/形状/列/索引/日期范围/非空数。"""
import os
import pandas as pd

B = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/data/results/"
     "rdagent_output/git_ignore_folder/RD-Agent_workspace")
TAGS = [
    ("9bf3f731af724f1d971ebb918b663729", "最新 22:13:11"),
    ("b3ef5c104a7d4d86b553b8be16d7ce48", "22:06:29"),
    ("3325ad184eae492292eeb06fd42d3b66", "20:26:09 大件"),
]
for tag, note in TAGS:
    p = os.path.join(B, tag, "result.h5")
    try:
        with pd.HDFStore(p, mode="r") as s:
            keys = s.keys()
            df = s[keys[0]] if len(keys) == 1 else None
    except Exception as e:
        print("=" * 15, tag, note, "READ_FAIL", type(e).__name__, str(e)[:140])
        continue
    print("=" * 15, tag, note)
    print("  keys:", keys)
    if df is None:
        continue
    print("  shape:", df.shape, "cols:", [str(c) for c in list(df.columns)[:6]])
    print("  index names:", list(df.index.names))
    for lv in range(min(2, df.index.nlevels)):
        try:
            v = df.index.get_level_values(lv)
            print("  lv%d: n=%d nuniq=%d min=%s max=%s" % (lv, len(v), v.nunique(), v.min(), v.max()))
        except Exception as e:
            print("  lv%d err: %s" % (lv, e))
    print("  non-na total:", int(df.notna().sum().sum()))
    try:
        print(df.head(2))
    except Exception as e:
        print("  head err:", e)
