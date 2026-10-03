# -*- coding: utf-8 -*-
"""ETF 线 Alpha158 因子表：生成 + 小样本试算（只读，不落任何权威产物）

来路（为什么表达式文本可以直接用股票线那份翻译结果）：`alpha158_exprs_0926.py` 的产物
是把 qlib `Alpha158DL.get_feature_config()` 的 158 条**公开定义**做语法树翻译，翻译时
带树同形自校验。这一步只依赖 qlib 的定义与本线共享的 DSL 文法（AGENT.md §15.2 明确
「只有 DSL 命名文法共用」），不含任何股票线的阈值、IC 读数或提示词措辞 ⇒ 复用表达式
文本不违反两线隔离。**股票线量出来的 IC 一律不带过来**，本线全部重算。

本线补第 158 条：股票线面板没有 vwap 列，只能跳过 `VWAP0 = $vwap/$close`；本线
`data/universe_all/` 的 CSV 有 `amount`（元）与 `volume`（份），`amount/volume` 即当日
成交均价。单位已用 `etf/v1/temp/a158_vwap_unit_0926.py` 实测过：与前复权 close 的比值
中位 0.9998、逐年中位数极差最大 0.0151、**0/616** 只超过 5% ⇒ 两列同基准，不是复权台阶。

跑法：`/usr/bin/python3.10 etf/v1/temp/a158_etf_exprs_0926.py [--full N]`
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
sys.path.insert(0, SRC)
os.chdir(SRC)                                     # 环 1 入口按相对路径找 config

import pandas as pd                               # noqa: E402
import etf_admission as EA                        # noqa: E402

STOCK_JSON = os.path.join(REPO, "stock", "v1", "temp", "tmp_alpha158",
                          "alpha158_exprs_0926.json")
OUT_DIR = os.path.join(HERE, "tmp_a158_etf")
OUT_JSON = os.path.join(OUT_DIR, "a158_etf_exprs.json")

# 共享层 `factor_dsl.safe_eval` 只把 open/high/low/close/volume/returns 绑成裸名，
# `amount` 不在其中 ⇒ 走 env 里始终存在的 `df`，这样不必为一条因子改共享层
# （改了会同时放开股票线的可用符号）。
VWAP0 = {"family": "价格水平", "name": "VWAP0",
         "expr": '(df["amount"] / df["volume"] / close)',
         "qlib_src": "Ref($vwap, 0)/$close",
         "note": "本线独有：股票线面板无 vwap，未扫这一条"}


def build_specs():
    with open(STOCK_JSON, encoding="utf-8") as f:
        rows = json.load(f)
    specs = [{"family": r.get("family", "外部"), "name": r["name"],
              "expr": r["expr"]} for r in rows]
    names = {s["name"] for s in specs}
    assert "VWAP0" not in names, "股票线那份已含 VWAP0，不必重复补"
    specs.append(VWAP0)
    for s in specs:                              # 表达式不许重复，否则同名两读
        assert sum(1 for x in specs if x["expr"] == s["expr"]) == 1, s["expr"]
    return specs


def main():
    specs = build_specs()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in s.items() if k != "note"} for s in specs],
                  f, ensure_ascii=False, indent=1)
    print(f"[表] {len(specs)} 条 → {os.path.relpath(OUT_JSON, REPO)}")

    pool = EA.load_pool()
    print(f"[池] 可评估标的 {len(pool)} 只")
    n_probe = 8
    codes = sorted(pool)[:n_probe]
    small = {c: pool[c] for c in codes}
    t0 = time.time()
    facs = EA.evaluate_factors(small, specs, verbose=False)
    dt = time.time() - t0
    dead = [s["name"] for s in specs if s["name"] not in facs]
    print(f"[试算] {n_probe} 只 × {len(specs)} 条 → 成表 {len(facs)} 列，"
          f"耗时 {dt:.1f}s")
    print(f"[不可求值] {len(dead)} 条" + ("：" + "、".join(dead) if dead else ""))
    bar_mean = sum(len(pool[c]) for c in codes) / len(codes)
    print(f"[外推] 单标的 {bar_mean:.0f} bar ⇒ {dt/n_probe:.2f}s/只 ⇒ "
          f"全池 {len(pool)} 只 ≈ {dt/n_probe*len(pool)/60:.1f} 分钟"
          "（粗估，未算截面 IC 那段）")
    # 反证：VWAP0 必须真的算得出、且数量级像"均价/收盘价"而不是 100 倍
    v = facs["VWAP0"].stack()
    print(f"[VWAP0 读数] 非空 {len(v)} 点，中位 {v.median():.5f}，"
          f"1~99 分位 [{v.quantile(.01):.4f}, {v.quantile(.99):.4f}]"
          "（应在 1 附近）")
    assert 0.9 < v.median() < 1.1, "VWAP0 中位数不在 1 附近 ⇒ 单位或口径错了"
    print("探针通过")


if __name__ == "__main__":
    main()
