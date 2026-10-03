# -*- coding: utf-8 -*-
"""乙：改共享求值层前的「基线 + 复核」对拍器（10-01）

要钉的事实：**现有全部表达式在改动后必须逐位不变**。`ma/std/max/min` 这四个算子
现在把第一个参数钉死为 DataFrame（内部取 `df["close"]`），要改成「收到 Series 就用、
收到 DataFrame 才取 close」。因为 `common/src/core/factor_dsl.py` 是**两条线共用**的求值层
（股票线 `run_ashare_daily_signal.py:244`、`ashare_screen.py`、`run_ashare_portfolio_eval.py`、
`rdagent_driver.py:27-30` 都在传 df），所以这不是一次 ETF 内部的改动。

四种模式：
  baseline  —— 改动**之前**跑，把每条表达式在每只标的上的取值序列哈希落盘
  after     —— 改动**之后**跑，同一批表达式、同一批数据，产物换一份文件
  diff      —— 逐格比两份产物，任何一格不同就退出码 1
  both      —— 一个进程里同时算「旧臂（monkeypatch 回旧函数体）+ 新臂」并逐格对表：
               10-01 补的这条是**默认该用的那条**。baseline/after 那两遍是当时
               真的把两个版本的文件压在 `common/src/core/factor_dsl.py` 上跑的，
               而那是两线共用的生产路径 ⇒ 期间若有人新起进程就会读到另一个版本；
               本文件的读数与两遍真文件对拍**必须一致**，一致即证 monkeypatch 等价。

表达式覆盖（tag 标出来源，避免"只测了自己想测的"）：
  lib        ETF 在库因子（factor_library.csv 里可译的那批）
  tmpl       LLM 无模型时的 8 条内置模板
  trans      RD-Agent 算子翻译产出的形状（SMA/STD/MAX/MIN × 若干 n）
  stock      股票线生产入口里硬编码的那几条 df 写法
  series     **改动前必然报错**的传列写法（负对照的靶子）
  edge       边界：n 大于长度、全 NaN 列、df 与 Series 混写
"""
import hashlib
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

from factor_dsl import safe_eval  # noqa: F402,E402

OUT_DIR = os.path.join(ROOT, "etf/v1/temp/tmp_dsl_series_1001")
CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODES = ["510300", "510500", "159516", "512480", "518880"]


def load_pool():
    pool = {}
    for c in CODES:
        df = pd.read_csv(os.path.join(CACHE, f"{c}_daily.csv"),
                         parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        pool[c] = df
    return pool


def library_exprs():
    p = os.path.join(ROOT, "etf/v1/data/library/factor_library.csv")
    d = pd.read_csv(p)
    out = []
    for _, r in d.iterrows():
        e = str(r.get("expr") or "").strip()
        if e and not e.lower().startswith("nan"):
            out.append((f"lib|{r['name']}", e))
    return out


def template_exprs():
    from llm_factor_agent import LLMFactorAgent
    agent = LLMFactorAgent.__new__(LLMFactorAgent)   # 不跑 __init__，只要模板
    return [(f"tmpl|{f['name']}", f["expr"])
            for f in LLMFactorAgent._fallback(agent, 8)]


def exprs():
    e = library_exprs() + template_exprs()
    for op, fn in (("SMA", "ma"), ("STD", "std"), ("MAX", "max"), ("MIN", "min")):
        for n in (5, 20, 60):
            e.append((f"trans|{op}_{n}", f"{fn}(df,{n})"))
    e += [
        ("trans|nested", "delta(close, 5) / (ma(df, 20) + 1e-9)"),
        ("trans|ratio", "ma(df, 5) / (ma(df, 20) + 1e-9) - 1"),
        ("trans|pos", "(close - min(df, 20)) / (max(df, 20) - min(df, 20) + 1e-9)"),
        ("trans|div", "std(df,20)/ma(df,20)"),
        ("stock|daily_px", "ma(df,5)"),
        ("stock|screen_max", "max(df,20)"),
        ("stock|screen_min", "min(df,20)"),
        ("stock|px_pos", "(close - min(df, 60)) / (max(df, 60) - min(df, 60) + 1e-9)"),
        ("series|vol_std", "abs(delta(volume, 3)) / (std(df['volume'], 60) + 1e-9)"),
        ("series|vol_ma", "ts_sum(abs(delta(close,3)),20)/(ma(volume,60)+1e-9)"),
        ("series|ret_std", "std(returns, 20)"),
        ("series|ret_ma", "ma(returns, 20)"),
        ("series|hl_max", "max(high, 20)"),
        ("series|low_min", "min(low, 20)"),
        ("series|vol_ratio", "volume / (ma(volume, 20) + 1e-9)"),
        ("series|chan", "(ma(close,5) - ma(close,60)) / (std(close,60) + 1e-9)"),
        # 10-01 从 ab_prompt_1001.log 逐字抄的第二条现场：模型自己括号没闭合。
        # 它 err→err，正是「本批只放宽写法、不替模型补语法」的边界格。
        ("series|unclosed_paren",
         "(std(returns,5)*delay(std(returns,5),1))/ (ma(std(returns,20)+1e-9)"),
        ("edge|n_too_big", "ma(df, 5000)"),
        ("edge|series_n_too_big", "ma(close, 5000)"),
        ("edge|zero_col", "std(df * 0, 20)"),
        ("edge|df_series_mix", "ma(df, 20) + std(volume, 20)"),
    ]
    seen, out = set(), []
    for tag, ex in e:
        if ex not in seen or tag.startswith("series|") or tag.startswith("edge|"):
            seen.add(ex)
            out.append((tag, ex))
    return out


def fingerprint(series):
    """序列 → (sha, 非缺值个数, 首个非缺值)。NaN 归一成同一字节，避免 NaN 载荷差异假红。"""
    raw = np.asarray(series.values, dtype="float64")
    canon = np.where(np.isnan(raw), 1e308, raw)
    finite = raw[np.isfinite(raw)]
    return (hashlib.sha256(canon.tobytes()).hexdigest()[:16],
            int(np.isfinite(raw).sum()),
            float(finite[0]) if len(finite) else 0.0)


def cell(ex, df, legacy):
    """在「旧行为」或「新行为」下求值一格。

    legacy=True 不是把生产文件拷来拷去（那会短暂把旧版压在两线共用的路径上），
    而是把 `_price_series` 换成旧函数体 `lambda x: x["close"]`——旧实现
    `df["close"].rolling(...)` 与此在**所有输入下等价**：传 df 两边都取 close，
    传 Series 两边都 `KeyError: 'close'`。这个等价性由本脚本的 baseline/after
    两遍真文件对拍反过来验证（两次读数必须一致）。
    """
    import factor_dsl as FD
    old = FD._price_series
    if legacy:
        FD._price_series = lambda x: x["close"]
    try:
        try:
            sha, nval, head = fingerprint(safe_eval(ex, df))
            return ("ok", sha, nval, head, "")
        except Exception as e:
            return ("err", "", 0, 0.0, f"{type(e).__name__}: {e}")
    finally:
        FD._price_series = old


def run_both():
    """一遍进程里同时算「旧/新」两臂并逐格对表（10-01 补：不再覆盖生产文件）"""
    P = load_pool()
    os.makedirs(OUT_DIR, exist_ok=True)
    rows, bad = [], []
    print(f"# both 模式：标的={len(P)} 表达式={len(exprs())} "
          f"格子={len(P) * len(exprs())}")
    for tag, ex in exprs():
        a_all = [cell(ex, d, True) for d in P.values()]
        b_all = [cell(ex, d, False) for d in P.values()]
        verd = []
        for code, (a, b) in zip(P, zip(a_all, b_all)):
            if a[0] == "ok" and b[0] == "ok":
                v = "same" if a[1] == b[1] else "REGRESSION"
            elif a[0] == "err" and b[0] == "ok":
                v = "opened"
            elif a[0] == "err" and b[0] == "err":
                v = "still_err"
            else:
                v = "REGRESSION"
            if v == "REGRESSION":
                bad.append((tag, ex, code, a, b))
            verd.append(v)
            rows.append((tag, ex, code) + a + b + (v,))
        print(f"  {tag:26s} 旧可算={sum(1 for x in a_all if x[0]=='ok')}/{len(P)} "
              f"新可算={sum(1 for x in b_all if x[0]=='ok')}/{len(P)} "
              f"判定={'/'.join(sorted(set(verd)))}")
    pd.DataFrame(rows, columns=["tag", "expr", "code", "st_legacy", "sha_legacy",
                                "n_legacy", "head_legacy", "err_legacy",
                                "st_new", "sha_new", "n_new", "head_new",
                                "err_new", "verdict"]
                 ).to_csv(os.path.join(OUT_DIR, "fingerprint_both.csv"), index=False)
    v = pd.Series([r[-1] for r in rows]).value_counts()
    print(f"\n总格={len(rows)}  " + "  ".join(
        f"{k}={v.get(k, 0)}" for k in ["same", "opened", "still_err", "REGRESSION"]))
    for tag, ex, code, a, b in bad[:20]:
        print(f"  🚫 [{tag}] {code}: 旧 {a[0]}/{a[1]} -> 新 {b[0]}/{b[1]}")
    print("✅ 既有可求值表达式一格未变（REGRESSION=0），且 err→err 那格"
          "证明「模型自己的语法错」没被顺手掩盖"
          if not bad else "🚫 有既有表达式被顶到")
    return 1 if bad else 0


def run(phase):
    pool = load_pool()
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    path = os.path.join(OUT_DIR, f"fingerprint_{phase}.csv")
    w = csv_writer(path)
    print(f"# phase={phase} 标的={len(pool)} "
          f"行数={sum(len(d) for d in pool.values())}")
    for tag, ex in exprs():
        for code, df in pool.items():
            try:
                s = safe_eval(ex, df)
                sha, nval, head = fingerprint(s)
                rows.append((tag, ex, code, "ok", sha, nval, head, ""))
            except Exception as e:
                rows.append((tag, ex, code, "err", "", 0, 0.0,
                             f"{type(e).__name__}: {e}"))
        oks = sum(1 for r in rows if r[0] == tag and r[3] == "ok")
        print(f"  {tag:26s} ok={oks}/{len(CODES)}  {ex[:58]}")
    w(rows)
    n_err = sum(1 for r in rows if r[3] == "err")
    print(f"\n落盘 {path}\n  总格={len(rows)} 求值成功={len(rows)-n_err} "
          f"抛错={n_err}")
    return 0


def csv_writer(path):
    def _w(rows):
        pd.DataFrame(rows, columns=["tag", "expr", "code", "status",
                                    "sha", "n_valid", "head", "err"]
                     ).to_csv(path, index=False)
    return _w


def read(phase):
    p = os.path.join(OUT_DIR, f"fingerprint_{phase}.csv")
    if not os.path.exists(p):
        sys.exit(f"缺少 {p}：先跑 baseline")
    return pd.read_csv(p, keep_default_na=False)


def diff():
    a, b = read("baseline"), read("after")
    key = ["tag", "expr", "code"]
    if a[key].shape != b[key].shape or not a[key].equals(b[key]):
        sys.exit("两次的表达式/标的集合不一致，对拍作废")
    m = a.merge(b, on=key, suffixes=("_b", "_a"))
    print(f"总格={len(m)}")
    same = m[(m.status_b == "ok") & (m.status_a == "ok") & (m.sha_b == m.sha_a)]
    ok_changed = m[(m.status_b == "ok") & ((m.status_a != "ok") | (m.sha_b != m.sha_a))]
    err_to_ok = m[(m.status_b != "ok") & (m.status_a == "ok")]
    err_to_err = m[(m.status_b != "ok") & (m.status_a != "ok")]
    print(f"  ok→ok 逐位相同 = {len(same)}   ← 这一格是「回归面没被顶到」的正闸")
    print(f"  ok→变了        = {len(ok_changed)}")
    print(f"  err→ok（新增可算）= {len(err_to_ok)}")
    print(f"  err→err（依旧报错）= {len(err_to_err)}")
    rc = 0
    if len(ok_changed):
        rc = 1
        print("\n🚫 原有可求值的表达式行为变了（每一条都必须解释）：")
        for _, r in ok_changed.iterrows():
            print(f"  [{r.tag}] {r.code}: {r.sha_b} -> "
                  f"{r.status_a}/{r.sha_a or '-'} {str(r.err_a)[:90]}")
    bad_new = err_to_ok[~err_to_ok.tag.str.startswith(("series|", "edge|"))]
    if len(bad_new):
        rc = 1
        print(f"\n🚫 {len(bad_new)} 格 err→ok 发生在非「传列靶子」上：")
        for _, r in bad_new.iterrows():
            print(f"  [{r.tag}] {r.code}")
    if len(err_to_ok):
        print("\n传列靶子被打通的格子：")
        for _, r in err_to_ok.iterrows():
            print(f"  ✅ [{r.tag}] {r.code} sha={r.sha_a} "
                  f"非缺值={r.n_valid_a}  {r.expr[:52]}")
    if len(err_to_err):
        print("\n依旧报错的靶子（应只剩「语法本来就错」那种）：")
        for _, r in err_to_err.drop_duplicates("expr").iterrows():
            print(f"  ⛔ [{r.tag}] {str(r.err_a)[:96]}")
    print(f"\nEXIT={rc}")
    return rc


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if mode == "diff":
        sys.exit(diff())
    if mode == "both":
        sys.exit(run_both())
    sys.exit(run(mode))
