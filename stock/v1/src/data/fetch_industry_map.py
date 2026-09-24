# -*- coding: utf-8 -*-
"""股票线行业分类落盘：新浪行业（主源）+ 深市官方所属行业（第二标签）（09-24）

为什么要这一步
--------------
本线的面板只有 OHLCV + `$factor`，**没有任何行业/板块字段**。于是名单集中度这类问题
——「待买入前 5 名会不会全是同一个板块」「50 只摊得开吗」——原来根本判不了，
只能写成一句"已知盲区"。用户 09-24 定：「数据加入行业，分析后再决定如何选择」，
这份映射就是那句分析的前提。

两个源，本机实测可用（东财全线不可用；申万官方文件 `www.swsresearch.com` 证书链在本机
SSL 校验失败，不在这里绕过证书）：

    新浪行业    `ak.stock_sector_spot(indicator='新浪行业')` → 49 个板块（一次请求），
                再逐板块 `ak.stock_sector_detail(sector=<label>)` → 成分股。
                成分的 `symbol` 就是 `sh600176` 这种前缀码，upper 后即面板 instrument，
                **不需要任何代码表换算**。这是主源，覆盖沪深两市。
    证监会行业  `ak.stock_info_sz_name_code(symbol='A股列表')` 的「所属行业」列
                （形如 `J 金融业`）。一次请求 2902 行，但**只有深市**（沪市官方清单
                `stock_info_sh_name_code` 没有行业列）⇒ 只当第二标签与交叉核对用。

落盘口径与边界（写给下游，别让一句"有行业了"被当成"有逐日历史行业"）
--------------------------------------------------------------------
* 这是**当前截面**的分类，不是历史逐日映射。拿它去解释 2016 年的分年度回测会引入
  重分类与前视偏差 ⇒ 本线的用法限定在「当日名单的集中度体检」。
* 一票在新浪口径只进一个板块；万一重复，按板块名排序保留第一个，其余记进 `行业源`。
* **覆盖是偏的，而且偏在哪些代码段已经实测过**（09-24，`shell/probe_industry_source_0924.py`
  对着两源并集 vs 当日面板全集 5225 只逐段量）：
      深市        缺   0.0%     （新浪 + 深市官方两条都覆盖到）
      沪市主板    缺  43.4%     （只有新浪一条，它漏的那半没有第二源补）
      科创板      缺  96.4%     （688xxxx 基本不在这 49 个板块里）
      北交所      缺  85.1%     （BJ 票压根不在这 49 个板块里）
  整体 可投池 73.0% / 当日保留池 75.6%，但**当日待买入 50 只命中 49 只 = 98%** ——
  安静度那根轴偏爱低波动老票，恰好落在覆盖率最高的一段，所以"今日名单能做集中度体检"
  与"历史全库能做"是两回事，别把前者当成后者。
* 下游必须把缺行业显式当**「未知」这一类**参与集中度统计与作图，不能悄悄丢行或当 0 ——
  丢行正好会让"5 只很分散"这种结论凭空变好（分母小了，最大行业占比自然降）。
* 抓回来的条数明显偏少（<3500 只，实测两源并集正常值 3812）就**拒绝覆盖**已有好表，
  与日更那条"半截不写"同一套纪律。

用法
----
    cd stock/v1/src && /usr/bin/python3.10 data/fetch_industry_map.py            # 新鲜就跳过
    cd stock/v1/src && /usr/bin/python3.10 data/fetch_industry_map.py --force     # 重抓
    # 落 stock/v1/data/cache/industry_map.csv（路径走 STOCK_INDUSTRY_CSV 覆写）
"""

import os
import sys

# 本文件在 src/data/ 下，而 _bootstrap 与 config 都在上一级 src/：先递一级目录再引导
# （日更那个 data/ 脚本不 import 本地模块所以没碰到这件事）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _bootstrap  # noqa: E402,F401  (裸模块名导入的完整 sys.path 引导)

import argparse
import socket
import time
from datetime import datetime

import numpy as np
import pandas as pd

from config import ASHARE_INDUSTRY_CSV, ASHARE_INDUSTRY_MAX_AGE

MIN_CODES = int(os.environ.get("STOCK_INDUSTRY_MIN_CODES", "3500"))
# 少于这个数就是源出问题了，别拿它覆盖已有映射。3500 不是拍的：09-24 实测两源并集
# 给到 3812/5225 只（73%），留出「哪天新浪少给一截」的余量。
SLEEP = 0.4               # 新浪逐板块请求之间的间隔（同 IP 连打会返回空 dict）
TRIES = 3


def _inst(sym):
    """`sh600176` / `600176` -> 面板 instrument `SH600176`"""
    s = str(sym).strip().upper()
    if s[:2] in ("SH", "SZ", "BJ") and len(s) == 8:
        return s
    d = "".join(c for c in s if c.isdigit())
    if len(d) != 6:
        return ""
    pfx = "BJ" if d[0] in ("4", "8", "9") else ("SH" if d[0] == "6" else "SZ")
    return pfx + d


def age_days(path):
    if not os.path.exists(path):
        return 1e9
    return (time.time() - os.path.getmtime(path)) / 86400.0


def _seg(inst):
    """代码段标签，与文件头「口径边界」那四段一一对应（覆盖率按段差得极多）"""
    s = str(inst).upper()
    if s.startswith("BJ"):
        return "北交所"
    if s.startswith("SH"):
        return "科创板" if s[2:5] in ("688", "689") else "沪市主板"
    return "深市"


def fetch_sina():
    """49 个行业板块 → (代码→行业) 明细表"""
    import akshare as ak
    boards = ak.stock_sector_spot(indicator="新浪行业")
    boards = boards[["label", "板块", "公司家数"]].dropna(subset=["label"])
    print(f"[新浪] 行业板块 {len(boards)} 个，逐板块取成分（每跳间隔 {SLEEP}s）")
    rows, failed = [], []
    for _, b in boards.iterrows():
        label, name, want = str(b["label"]), str(b["板块"]), float(b["公司家数"])
        got = None
        for t in range(TRIES):
            try:
                d = ak.stock_sector_detail(sector=label)
                if d is not None and len(d):
                    got = d
                    break
            except Exception as e:
                if t == TRIES - 1:
                    print(f"  ⚠️ {name}({label}) 取失败 {type(e).__name__}: {str(e)[:80]}")
            time.sleep(SLEEP * (t + 2))
        if got is None:
            failed.append(name)
            continue
        got = got[["symbol", "name"]].copy()
        got["行业"] = name
        rows.append(got)
        n = len(got)
        if want and abs(n - want) / want > 0.35:
            print(f"  ⚠️ {name}：成分 {n} 只，板块表标称 {want:.0f} 只，差得多（源可能截断）")
        time.sleep(SLEEP)
    det = pd.concat(rows, ignore_index=True)
    det["inst"] = det["symbol"].map(_inst)
    det = det[det["inst"] != ""].rename(columns={"name": "简称"})
    print(f"[新浪] 抓到 {len(det)} 行、唯一代码 {det['inst'].nunique()} 只、"
          f"行业 {det['行业'].nunique()} 个；整板块失败 {failed or '无'}")
    # 同票多板块时按板块名保留第一个（新浪口径本身互斥，重复只可能来自源的边角）
    det = det.sort_values(["行业", "inst"]).drop_duplicates("inst", keep="first")
    return det[["inst", "简称", "行业"]]


def fetch_sz():
    """深交所官方清单的「所属行业」：只覆盖深市，当第二标签 + 补新浪漏掉的那批深市票"""
    import akshare as ak
    try:
        d = ak.stock_info_sz_name_code(symbol="A股列表")
    except Exception as e:
        print(f"  ⚠️ 深市官方清单取不到 {type(e).__name__}: {str(e)[:100]}")
        return pd.DataFrame(columns=["inst", "简称_sz", "证监会行业"])
    d = d[["A股代码", "A股简称", "所属行业"]].copy()
    d["inst"] = d["A股代码"].map(_inst)
    d = d[d["inst"].astype(str).str.len() == 8]
    d = d.drop_duplicates("inst", keep="first")
    d = d.rename(columns={"所属行业": "证监会行业", "A股简称": "简称_sz"})
    d["证监会行业"] = d["证监会行业"].astype(str).str.strip()
    print(f"[深市官方] 带证监会行业的 {len(d)} 只")
    return d[["inst", "简称_sz", "证监会行业"]]


def _blank(s):
    """空串与 `astype(str)` 造出来的 'nan'/'None' 一律折回 NaN

    不这么做的话，缺行业的票会带出一个名叫 `nan` 的行业 —— 下游数集中度时它凭空变成
    一个真板块，而它恰好是覆盖率最差的科创板/北交所那批的合集，最容易被误读成
    「有个板块叫 nan 占了三成」。"""
    t = s.astype(str).str.strip()
    return t.mask(t.isin(("nan", "None", "none", "", "<NA>")))


def merge_sources(sina, sz):
    """两源合并成 (inst, 简称, 行业, 证监会行业, 行业源)

    独立成函数是为了能离线喂假表验一次：上一版用 left join，深市官方独有的那 820 只
    被整行丢掉、并集退化成新浪单源，而这个错只有联网跑满两源才看得见（守卫拦下了，
    但来回一趟是 3 分钟网络）。假表测试 = shell/test_industry_merge_0924.py。
    """
    m = sina.assign(_新浪=True).merge(sz.assign(_深市官方=True), on="inst", how="outer")
    # **outer** 是要点：新浪 49 板块只覆盖 2990 只，深市官方补回深市缺的那一截。
    # 补不动的是沪市——沪市官方清单没有行业列，所以沪市主板/科创板缺的仍然缺。
    m["行业"] = _blank(m["行业"])
    m["证监会行业"] = _blank(m["证监会行业"])
    m["简称_sz"] = _blank(m["简称_sz"])
    # 有新浪行业用新浪；缺的才退到证监会一级名后半段（`J 金融业` -> `金融业`）
    m["行业"] = m["行业"].fillna(m["证监会行业"].str.split(" ").str[-1])
    m["简称"] = m["简称"].fillna(m["简称_sz"])
    m["行业源"] = np.where(m["_新浪"].notna() & m["_深市官方"].notna(), "新浪+深市官方",
                          np.where(m["_新浪"].notna(), "新浪", "深市官方"))
    m = m[m["行业"].notna()]
    return m[["inst", "简称", "行业", "证监会行业", "行业源"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略新鲜度重抓")
    ap.add_argument("--out", default=ASHARE_INDUSTRY_CSV)
    a = ap.parse_args()

    if not a.force and age_days(a.out) <= ASHARE_INDUSTRY_MAX_AGE:
        d = pd.read_csv(a.out, dtype=str)
        print(f"[缓存] {a.out} 距今 {age_days(a.out):.1f} 天（≤{ASHARE_INDUSTRY_MAX_AGE}），"
              f"{len(d)} 只，直接用；要重抓加 --force")
        return 0

    socket.setdefaulttimeout(90)
    m = merge_sources(fetch_sina(), fetch_sz())
    if m["inst"].nunique() < MIN_CODES:
        raise SystemExit(f"[拒绝落盘] 只有 {m['inst'].nunique()} 只有行业，少于 {MIN_CODES} —— "
                         f"源没抓全，不覆盖 {a.out}。")

    m = m.sort_values("inst").rename(columns={"inst": "代码"})
    m["抓取日"] = datetime.now().strftime("%Y-%m-%d")
    out = m[["代码", "简称", "行业", "证监会行业", "行业源", "抓取日"]]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    out.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, a.out)

    n = out["行业"].nunique()
    print(f"\n[落盘] {a.out}：{len(out)} 只 / {n} 个行业"
          f"（{dict(out['行业源'].value_counts())}）")
    print(out["行业"].value_counts().head(8).to_string())
    # 逐段计数自己报一遍：口径边界那段说"偏"，这里给出今天到底偏成什么样
    print("落盘表按代码段计数：")
    print(out["代码"].map(_seg).value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
