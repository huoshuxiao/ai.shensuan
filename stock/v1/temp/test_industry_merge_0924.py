# -*- coding: utf-8 -*-
"""离线喂假表，验 merge_sources 的并集/回退/来源标注（09-24）

为什么单独测这个函数：上一版写成 left join，深市官方独有的票被整行丢掉，并集退化成
新浪单源；而这个错只有联网跑满两源（约 3 分钟）才看得见。合成表几毫秒就能判。
"""
import os
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, os.path.join(SRC, "data"))
sys.path.insert(0, SRC)

import pandas as pd  # noqa: E402
import fetch_industry_map as F  # noqa: E402

sina = pd.DataFrame([
    ("SH600000", "浦发银行", "银行"),      # 只有新浪有（沪市，证监会清单不覆盖）
    ("SZ000001", "平安银行", "银行"),      # 两源都有 -> 取新浪，标"新浪+深市官方"
    ("SZ300750", "宁德时代", "电气设备"),  # 新浪有它，深市官方也在，故意造 overlap
], columns=["inst", "简称", "行业"])

sz = pd.DataFrame([
    ("SZ000001", "平安银行", "J 金融业"),
    ("SZ002801", "金明精机", "C 制造业"),   # 只有深市官方有 -> 靠回退补上行业
    ("SZ300999", "未知票", None),           # 两源都缺行业 -> 应被整行剔掉
    ("SZ001234", "nan行业", "nan"),         # astype(str) 造出来的假字符串"nan"，同样要剔
], columns=["inst", "简称_sz", "证监会行业"])

m = F.merge_sources(sina, sz).set_index("inst").sort_index()
print(m.to_string())

# 并集 = 新浪 3 + 深市官方独有 1（另两条没行业的必须被剔，不能带出 "nan" 这种假行业）
assert len(m) == 4, f"并集应是 4 只，实得 {len(m)}"
assert "SZ300999" not in m.index and "SZ001234" not in m.index, "无行业的票不该留下"
assert "nan" not in set(m["行业"]), "astype(str) 的假 'nan' 漏进了行业列"
assert m.loc["SZ002801", "行业"] == "制造业", "证监会一级名应剥掉字母前缀"
assert m.loc["SZ002801", "简称"] == "金明精机", "简称应回退到深市官方清单"
assert m.loc["SZ002801", "行业源"] == "深市官方"
assert m.loc["SZ000001", "行业"] == "银行" and m.loc["SZ000001", "行业源"] == "新浪+深市官方"
assert m.loc["SH600000", "行业源"] == "新浪"
# 沪市票绝不能被标成深市官方（_inst 的 BJ/SH/SZ 前缀换算错了就会串）
assert set(m.index.str[:2]) == {"SH", "SZ"}
print("\n[通过] 并集 / 回退 / 行业源标注 / 空行业与假 nan 剔除 四条都对")

# 空深市清单（网络失败的兜底形状）不该把新浪那 3 只一起拖没
empty = pd.DataFrame(columns=["inst", "简称_sz", "证监会行业"])
m2 = F.merge_sources(sina, empty)
assert len(m2) == 3 and set(m2["行业源"]) == {"新浪"}, m2["行业源"].tolist()
print("[通过] 深市源取不到时，新浪 3 只照样落盘")
