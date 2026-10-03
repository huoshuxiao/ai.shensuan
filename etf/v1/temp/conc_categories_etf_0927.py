# -*- coding: utf-8 -*-
"""#50 前置：给候选池 100 只 ETF 建「粗类」映射（集中度闸的维度）。

跑法：`/usr/bin/python3.10 etf/v1/temp/conc_categories_etf_0927.py`

为什么必须另建一层
------------------
盘上唯一的分类是 `etf_universe_cache.csv` 的 `index_group`，它是**从名字正则抠出来
的跟踪指数名**，而池本身就是"每个指数组只留一条代表"建出来的 ⇒ 100 只 ↔ 100 个
不同的组，**每组恰好一只**。拿它当"同一组最多 N 席"的维度，N≥1 时闸永远不咬、
N=0 时无仓可开，是个空判据。所以要往下合并一层粗类。

口径（写死在这里，改一行就要重跑档位表）
----------------------------------------
1. 一条 ETF 只归一个粗类：按下面的规则**从上到下第一个命中即止**（越具体越靠前）。
2. 维度取**主题/方向**而不是地域：用户在意的是"10 只里 8 只押在同一类资产上"，
   而纳指科技与科创芯片同涨同跌 ⇒ 归同一类；黄金/豆粕与股票不同资产 ⇒ 单列。
3. `direction` 是给报告用的更粗一层（成长系/价值/周期/医药/金融/消费/商品/
   海外宽基/债券货币），只做读数，不做档位维度。
4. 输出 `tmp_conc_0927/category_map.csv` 供人工逐行核：**这份映射没过人眼之前，
   任何"降集中度"的账单都不许进文档。**
"""
import os
import re

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(HERE, "tmp_conc_0927")
REPO = os.path.dirname(os.path.dirname(HERE))   # shell/etf -> shell -> 仓库根
POOL = os.path.join(REPO, "etf", "v1", "data", "cache",
                    "etf_universe_cache.csv")
os.makedirs(TMP, exist_ok=True)

# (粗类, 正则) —— 从上到下第一个命中即止：先具体主题、后地域/宽基
RULES = [
    ("黄金与商品", r"黄金|金ETF|豆粕|油气|粮食"),
    ("债券货币", r"债|货币|理财"),
    ("半导体电子", r"半导体|芯片|消费电子|集成电路"),
    ("人工智能软件通信", r"人工智能|AI|软件|通信"),
    ("军工航天", r"军工|卫星"),
    ("新能源与高端制造", r"电池|储能|新能源|电网|材料"),
    ("医药", r"创新药|医药|医疗|生物|疫苗"),
    ("金融", r"证券|券商|保险|非银|金融科技|银行"),
    ("周期资源", r"有色|煤炭|化工|石油|能源|电力|钢|建材|矿业"),
    ("消费传媒", r"酒|旅游|游戏|传媒|消费|食品|农业|养殖"),
    ("平台互联网", r"互联网|中概"),
    ("红利低波", r"红利|低波"),
    ("价值", r"价值"),
    ("科创创业成长风格", r"科创|创业板|成长"),
    ("科技综合", r"科技"),
    ("港股", r"港股|恒生|H股|中国企业"),
    ("海外宽基", r"纳指|纳斯达克|标普|道琼斯|日经|美国|法国|德国|亚太"),
    ("A股宽基", r"50|300|500|1000|2000|A5|100|上证|沪深|中证"),
]

DIRECTION = {
    "半导体电子": "成长系", "人工智能软件通信": "成长系", "军工航天": "成长系",
    "新能源与高端制造": "成长系", "科技综合": "成长系",
    "科创创业成长风格": "成长系", "平台互联网": "成长系",
    "医药": "医药", "金融": "金融", "周期资源": "周期",
    "消费传媒": "消费", "红利低波": "红利价值", "价值": "红利价值",
    "黄金与商品": "商品", "债券货币": "债券货币",
    "港股": "港股", "海外宽基": "海外宽基", "A股宽基": "A股宽基",
}


def classify(name: str, index_group: str) -> str:
    text = f"{name}|{index_group}"
    for cat, pat in RULES:
        if re.search(pat, text):
            return cat
    return "未分类"


def main():
    pool = pd.read_csv(POOL, encoding="utf-8-sig", dtype={"code": str})
    pool["code"] = pool["code"].str.zfill(6)
    pool["category"] = [classify(n, g) for n, g in zip(pool["name"],
                                                       pool["index_group"])]
    pool["direction"] = pool["category"].map(DIRECTION).fillna("未分类")

    unc = pool[pool["category"] == "未分类"]
    print(f"候选池 {len(pool)} 只 | 未分类 {len(unc)} 只")
    if len(unc):
        print(unc[["code", "name", "index_group"]].to_string(index=False))

    dist = pool["category"].value_counts()
    print("\n各粗类有多少只候选（=档位能不能咬到 8 席的前提）：")
    print(dist.to_string())
    print("\n更粗的方向：")
    print(pool["direction"].value_counts().to_string())

    out = os.path.join(TMP, "category_map.csv")
    pool[["code", "name", "index_group", "category", "direction"]] \
        .to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n映射已写 {out}（请人工逐行核过再引档位账单）")


if __name__ == "__main__":
    main()
