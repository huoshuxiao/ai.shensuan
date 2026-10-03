#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""从新浪批量取 A 股「代码→名称」表，用来补 09-29 那一场缺的 ST 闸（用户裁「甲二」）

为什么不是拿它冒充 spot_20260929.csv
    `spot_labels()`（run_ashare_daily_signal.py:136）只认 `spot_<场次>.csv`，而那份 CSV
    由 ① 在 append 当天落盘、**只有 ① 会创建**。09-29 的批量快照已经取不回来（东财不可用，
    新浪这个接口只给当天价），所以**绝不往快照目录写一份挂假日期的文件**：④ 审计里有
    `glob(spot_*.csv)`，一旦造出假日期文件，将来重跑 09-28 那场会把它当 09-29 的真实 T+1
    快照吃进去 ⇒ 造出假对账。
    本脚本只落一张**独立命名的名称表**进 cache/，由 `STOCK_SPOT_NAME_FALLBACK` 显式指名
    才生效（默认空 = 日更链行为一个字节都不变）。

名称表能不能代表 09-29 那一天
    名称/ST 标记是**公告驱动**的慢变量，不是行情。本脚本因此必须把风险量出来：与最近一份
    真快照（spot_20260928.csv，5568 只、其中 203 只带 ST）逐码对拉，报
    ① 两表同码但名称不同 的只数；② ST 标记「28 有 / 今没有」与「28 没有 / 今有」各自的名单
    ⇒ 只有这两格里出现了 09-29 当天翻牌的票，这张表才可能误放或误杀。

只读行情、只写一张 CSV 进 cache/，不碰 results/、不碰 daily_snapshot/。
"""
import argparse
import os
import time

import pandas as pd
import requests

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
SNAP = os.path.join(ROOT, "common/data/stock/daily_snapshot")
CACHE = os.path.join(ROOT, "common/data/stock/cache")
URL = "https://hq.sinajs.cn/list={}"
HDR = {"Referer": "https://finance.sina.com.cn"}


def norm(inst):
    """all.txt 的 SH600000 → 新浪的 sh600000（北交所 sina 用 bj 前缀，实测可取回名称）"""
    return inst.lower()


def denorm(sym):
    """sh600000 → SH600000（与 spot_labels 里 codes 的归一化同一口径：只把前两位转大写）"""
    return sym[:2].upper() + sym[2:]


def fetch_batch(symbols, retries=3):
    q = ",".join(symbols)
    for k in range(retries):
        try:
            r = requests.get(URL.format(q), headers=HDR, timeout=10)
            r.encoding = "gbk"
            out = {}
            for line in r.text.strip().split("\n"):
                if "=" not in line or '"' not in line:
                    continue
                key = line.split("=")[0].split("_")[-1]
                fields = line.split('"')[1].split(",")
                if not fields or not fields[0]:
                    continue
                out[key] = fields[0]
            return out
        except Exception as e:
            if k == retries - 1:
                print(f"  ⚠️ 一批 {len(symbols)} 只失败: {type(e).__name__}: {e}")
                return {}
            time.sleep(1.0 + k)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 只（冒烟用）")
    ap.add_argument("--batch", type=int, default=60)
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    insts = [l.split("\t")[0].strip() for l in
             open(os.path.join(PROD, "instruments", "all.txt")) if l.strip()]
    insts = sorted(set(insts))
    if a.limit:
        insts = insts[:a.limit]
    print(f"名称表要覆盖 all.txt 的 {len(insts)} 只（批量 {a.batch} 只/请求）")

    by_symbol = {norm(i): i for i in insts}
    names, miss = {}, []
    syms = list(by_symbol)
    for i in range(0, len(syms), a.batch):
        chunk = syms[i:i + a.batch]
        got = fetch_batch(chunk)
        for s in chunk:
            if s in got:
                names[by_symbol[s]] = got[s]
            else:
                miss.append(by_symbol[s])
        n_done = i + len(chunk)
        if (i // a.batch) % 20 == 19:
            print(f"  进度 {n_done}/{len(syms)}｜已取回 {len(names)}｜取不到 {len(miss)}")
        time.sleep(a.sleep)

    today = time.strftime("%Y-%m-%d")
    out = a.out or os.path.join(CACHE, f"name_table_sina_{today.replace('-', '')}.csv")
    codes = list(names)
    df = pd.DataFrame({"代码": codes, "名称": [names[c] for c in codes],
                       "取数日": today, "来源": "sina"})
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    chk = pd.read_csv(tmp, encoding="utf-8-sig")
    assert len(chk) == len(df) and list(chk.columns) == ["代码", "名称", "取数日", "来源"]
    os.replace(tmp, out)
    print(f"=> 落盘 {out}：{len(df)} 只带名称｜取不到 {len(miss)} 只 {miss[:8]}")

    st = sorted(df[df["名称"].str.contains("ST", regex=False)]["代码"])
    print(f"=> 名称含 ST（大写敏感，与 spot_labels 同口径）：{len(st)} 只")

    ref = os.path.join(SNAP, "spot_20260928.csv")
    if os.path.exists(ref):
        sp = pd.read_csv(ref, encoding="utf-8-sig")
        c28 = sp["代码"].astype(str).str[:2].str.upper() + sp["代码"].astype(str).str[2:]
        m28 = dict(zip(c28, sp["名称"].astype(str)))
        st28 = set(c28[sp["名称"].astype(str).str.contains("ST", regex=False)])
        both = set(df["代码"]) & set(m28)
        diff = [c for c in both if m28[c] != df.set_index("代码").loc[c, "名称"]]
        cur_st = set(st)
        add = sorted(cur_st - st28)
        drop = sorted(st28 - cur_st)
        print(f"=> 与 09-28 真快照对拉：同码 {len(both)} 只｜名称不同 {len(diff)} 只 {diff[:8]}")
        print(f"   ST「28 无 / 今有」{len(add)} 只 {add[:12]}")
        print(f"   ST「28 有 / 今无」{len(drop)} 只 {drop[:12]}")
        print(f"   09-28 表 {len(st28)} 只 ST vs 今 {len(cur_st)} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
