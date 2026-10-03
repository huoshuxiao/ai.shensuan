# -*- coding: utf-8 -*-
"""10-01 用户裁「甲：只接闸，不动库」的 e2e 账单。

走的是**生产写库那一路**（`factor_library.FactorLibrary.batch_upsert` → `upsert`），
输入是**真库 45 行的原样快照**，输出落进临时目录。四件事分开量：

E1 误杀账单：默认档（闸开）下真库 45 行有几行写不进去 —— 只能由那条未来函数
   `volatility_breakout_momentum` 承担，其余 44 行必须原样收下（含 expr 为空的那 1 行）。
   注意起步库就是真库的副本 ⇒ 这里量的是**已存在那一条被不被更新**，
   「新条目根本进不来」那一格在 `etf/v1/tests/test_library_static_gate.py`。
E2 差异有没有活到落盘：甲臂（闸开）与乙臂（闸关）各自写出的 CSV 逐格对表，
   不同的格子必须**只出现在那一条名字上**（两臂恒等＝这一改没有任何后果，不作数）。
E3 牙：把 `factor_library.check_expr` 换成恒返回 '' 的假尺子、开关仍为开 ⇒
   必须退回 45 行全收。少了这一格，"1 行被挡"就不能归因给那把尺子。
E4 生产库一行未动：三张库文件在整场读数前后的 sha256 必须相同。

落盘面的一处机关：`save_markdown` 写 CSV 用的是**模块级** `LIBRARY_DIR`（不是实例路径），
所以每臂跑前都要把那个模块全局指到本臂的临时目录，否则两臂互相覆盖同一张表。

内存/网络：只读 45 行 CSV，不碰行情、不联网、不起容器。
"""
import hashlib
import io
import os
import shutil
import sys
import time

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD_LIB_DIR = os.path.join(ROOT, "etf/v1/data/library")
WORK = "/tmp/gate_e2e_1001"
BAD = "volatility_breakout_momentum"
FILES = ("factor_library.csv", "factor_library_index.json", "factor_library.md")


def snap():
    return {f: hashlib.sha256(open(os.path.join(PROD_LIB_DIR, f), "rb")
                              .read()).hexdigest()[:12] for f in FILES}


def main():
    os.environ["ETF_DATA_DIR"] = os.path.join(WORK, "unused")
    os.environ["ETF_BASE_DATA_DIR"] = os.path.join(WORK, "unused_base")
    sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
    import _bootstrap  # noqa: F402,E402
    import config
    # 账单场绝不许自动提交：git_dir 是真仓库根（同 tests/conftest.py:45-46）
    config.FACTOR_LIBRARY_GIT["enabled"] = False
    config.FACTOR_LIBRARY_GIT["auto_commit"] = False
    import factor_library as FL

    prod_before = snap()
    import pandas as pd
    raw = open(os.path.join(PROD_LIB_DIR, "factor_library.csv"), "rb").read()
    df = pd.read_csv(io.StringIO(raw.decode("utf-8-sig")))
    cands = [{"name": r["name"],
              "expr": "" if str(r["expr"]) == "nan" else r["expr"],
              "mean_ic": float(r["ic"]), "icir": float(r["icir"]),
              "source": r["source"]} for _, r in df.iterrows()]
    print(f"真库快照 {len(df)} 行 / mtime "
          f"{time.strftime('%F %T', time.localtime(os.path.getmtime(PROD_LIB_DIR + '/factor_library.csv')))}；"
          f"生产默认档 static_gate = {config.FACTOR_LIBRARY['static_gate']}")

    def arm(tag, extra):
        d = os.path.join(WORK, tag)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(os.path.join(d, "data", "library"))
        shutil.copy(PROD_LIB_DIR + "/factor_library_index.json",
                    d + "/data/library/factor_library_index.json")
        FL.LIBRARY_DIR = os.path.join(d, "data", "library")   # 见 docstring 的机关
        p = {"index_path": d + "/data/library/factor_library_index.json",
             "md_path": d + "/data/library/factor_library.md",
             "enabled": True}
        p.update(extra)
        lib = FL.FactorLibrary(p)
        lib.batch_upsert(cands, source="e2e")
        lib.save_markdown()
        tab = pd.read_csv(d + "/data/library/factor_library.csv",
                          dtype=str).fillna("")
        cells = {(str(row["name"]), col): row[col]
                 for _, row in tab.iterrows() for col in tab.columns}
        return lib, cells

    # ---- E1 甲臂：生产默认档（闸开）----
    a, cell_a = arm("on", {})
    assert a.p["static_gate"] is True, "甲臂必须继承生产默认（开）"
    print(f"\n[E1] 甲臂：{len(cands)} 行送进去 → 库 {len(a.factors)} 条 / "
          f"拒 {len(a.rejected)} 条 = {a.rejected}")
    assert set(a.rejected) == {BAD}, f"只能拒那一条，实际 {set(a.rejected)}"
    assert len(a.factors) == len(cands), "条数不该变（这条已在库里，挡的是更新）"
    assert "ma_ratio_10_30" not in a.rejected, "空 expr 那行必须放行（豁免格）"

    # ---- E2 乙臂：闸关 ----
    b, cell_b = arm("off", {"static_gate": False})
    print(f"[E2] 乙臂：拒 {len(b.rejected)} 条（应当 0）；那条的 ic 历史长度 "
          f"甲={len(a.factors[BAD]['ic_history'])} 乙={len(b.factors[BAD]['ic_history'])}")
    assert b.rejected == {}
    assert len(b.factors[BAD]["ic_history"]) == \
        len(a.factors[BAD]["ic_history"]) + 1, \
        "差异必须活到落盘：乙臂给那条多记一次，甲臂一个字段都没动"
    assert set(cell_a) == set(cell_b), "两臂格子集合应当相同（只差内容）"
    diff = {k for k in cell_a if cell_a[k] != cell_b[k]}
    owners = {k[0] for k in diff}
    print(f"[E2] 落盘 CSV 逐格对表：{len(cell_a)} 格，不同 {len(diff)} 格，"
          f"涉及名字 {sorted(owners)}")
    assert owners == {BAD}, f"不同的格子只能来自那一条，实际 {sorted(owners)}"

    # ---- E3 牙：假尺子 + 开关仍为开 ----
    orig = FL.check_expr
    FL.check_expr = lambda e: ""
    try:
        c, _ = arm("teeth", {})
    finally:
        FL.check_expr = orig
    print(f"[E3] 把 check_expr 换成恒返回 '' ⇒ 拒 {len(c.rejected)} 条（应当 0）"
          f"＝那一行尺子确实在咬")
    assert c.rejected == {}, "拔掉尺子还挡人＝这一格没有牙"

    # ---- E4 生产库未动 ----
    prod_after = snap()
    for f in FILES:
        print(f"[E4] {f:32s} {prod_before[f]} → {prod_after[f]}")
    assert prod_before == prod_after, "本账单不许碰生产库"
    print("\n✅ 四件事全部对上：误杀 0（除那一条）、差异活到落盘、牙在、生产库未动")


if __name__ == "__main__":
    main()
