# -*- coding: utf-8 -*-
"""10-01 选项A：那条带未来函数的 active 因子**污染到哪里了**——只读取数，一个字段都没改。

要回答的是三件事，分开量、别混成一个"总污染"：

R1 席位：它有没有进实盘打分那 10 条（`run_live.py:157-163` 取 |IC| 前 10），
   研究侧存下来的那套权重（`optimized_params_daily.json`）里有没有它的名字
   —— 决定它是"加权的一席"还是"等权的一席"。
R2 实盘那一路它实际出了多少力：`run_live` 的打分取的是
   `safe_eval(expr, df).iloc[-1]`（**最后一根 K 线**），而 `delay(x,-1)=x.shift(-1)`
   在最后一根上正好是 NaN ⇒ 代码里 `if not np.isfinite(v): continue` 会**当场跳过它**，
   但 `w_sum` 已经把它那份算进分母 ⇒ 全部标的的分数被同一个系数压低。
   压低了不等于决策变了，所以要分两臂算：**乙臂＝它不曾进库**（第 11 名顶上来换人），
   **丙臂＝删席不补**（纯系数），两臂都走同一份 `select_weights` 看目标组合同不同；
   再加一臂正对照（拔一个活席）证明这把尺子看得见席位变化。
R3 判重那一层它挡不挡人：它和在库其他条的截面 rank 相关若 ≥NEAR_DUP/RED_BAR，
   会把**诚实的**候选按"重复"挡掉。同一张矩阵要自带两道牙（全库 946 对的分布 +
   库里已知的逐字同式对必须恰为 1.0），否则"0 条"不作数。
   （"历史每一天都有下一根 ⇒ 回测落盘表被抬高"这一层不在本脚本里量：那要跑归档回测
   `main.py`，会覆写生产表 ⇒ 记为未量。）
R4 落盘面：它 15:58:57 进库，此后被写的表逐张扫名字——没带它的那些就不算污染。

口径边界（必读）：R2 跑两个池——实盘默认 4 只（本机缓存只到手 3 只，`513100` 要联网取，
本脚本不联网）与本机缓存 20 只宽池；R3 用同一批 20 只（生产全池 ~871 只会 OOM，
且现在另一个会话的链条在跑）⇒ R2 宽池与 R3 都是**窄池读数**，不是环3 归档那份数。
"""
import io
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

from factor_dsl import safe_eval  # noqa: F402,E402
from config import PORTFOLIO  # noqa: F402,E402
from strategy import select_weights  # noqa: F402,E402  实盘/回测同一份权重函数
import etf_admission as EA  # noqa: F402,E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
LIB = os.path.join(ROOT, "etf/v1/data/library/factor_library.csv")
PARAMS = os.path.join(ROOT, "etf/v1/data/results/optimized_params_daily.json")
BAD = "volatility_breakout_momentum"
LIVE_CODES = ["510300", "510500", "513100", "518880"]   # run_live.py:140 的默认池
R3_CODES = [f[:6] for f in sorted(os.listdir(CACHE)) if f.endswith("_daily.csv")]


def load(codes):
    pool = {}
    for c in codes:
        p = os.path.join(CACHE, f"{c}_daily.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        df.columns = [str(x).lower() for x in df.columns]
        pool[c] = df
    return pool


def top10(lib):
    """复现 run_live 的取法：有 expr 的 active，按 |IC| 降序取前 10。"""
    act = [r for _, r in lib.iterrows() if str(r.get("expr") or "") not in ("", "nan")]
    act.sort(key=lambda r: -abs(float(r["ic"])))
    return act[:10]


def r1(lib):
    t = top10(lib)
    names = [r["name"] for r in t]
    seat = BAD in names
    import json
    fw = json.load(open(PARAMS)).get("factor_weights", {})
    hit = [n for n in names if n in fw]
    print("## R1 席位")
    print(f"  |IC| 前 10 = {names}")
    print(f"  它在榜内第 {names.index(BAD)+1 if seat else '-'} 席 ⇒ 进实盘打分：{'是' if seat else '否'}")
    print(f"  研究侧存的权重表({len(fw)} 条 {list(fw)}) 与这 10 条的名字交集 = {hit}")
    print(f"  ⇒ {'交集为空 ⇒ ws 全 0 ⇒ 走「缺失等权」分支，每席 1/10 = 10.0%' if not hit else '命中 ⇒ 按存的权重加权'}")
    return seat, not hit


def _score_pool(t, pool):
    """照抄 run_live 的打分算式（等权分支）：返回 {code: score} 与每席死活。"""
    ws = {f["name"]: 1.0 for f in t}          # 权重表无交集 ⇒ 等权
    w_sum = sum(ws.values())
    dead, scores = [], {}
    for code, df in pool.items():
        s = 0.0
        for f in t:
            try:
                v = float(safe_eval(str(f["expr"]), df).iloc[-1])
            except Exception:
                v = float("nan")
            if not np.isfinite(v):
                continue
            s += (1.0 if float(f.get("ic", 0.0)) >= 0 else -1.0) \
                * ws[f["name"]] / w_sum * float(np.tanh(v))
        scores[code] = s
    for f in t:
        try:
            vs = [float(safe_eval(str(f["expr"]), df).iloc[-1])
                  for _, df in pool.items()]
            if not any(np.isfinite(v) for v in vs):
                dead.append(f["name"])
        except Exception:
            dead.append(f["name"])
    return scores, dead, ws, w_sum


def _run_pool(tag, pool, A, B, C):
    """同一套算式在两个池上各跑三臂。差异要先在「分数层」看见，
    决策层若两臂恒等（今天就是），那一句要当读数念、不能当结论念。"""
    print(f"\n  ── {tag}：{len(pool)} 只 ──")
    out = {}
    for name, t in (("甲 现状(它在，占 1 席)", A),
                    ("乙 它不曾进库(第 11 名顶上)", B),
                    ("丙 删席不补(9 席)", C)):
        sc, dead, ws, w_sum = _score_pool(t, pool)
        tg = select_weights(sc)
        passed = sorted(c for c, s in sc.items()
                        if s > PORTFOLIO["min_score"])
        out[name] = (sc, dead, tg, passed)
        print(f"    {name}: 死席={dead}")
        print(f"      分数（前 6 只）{ {k: round(v, 5) for k, v in list(sc.items())[:6]} }"
              f"｜均值 {np.mean(list(sc.values())):+.5f}")
        print(f"      过闸(>{PORTFOLIO['min_score']}) {len(passed)} 只 ⇒ 目标 {len(tg)} 只 "
              f"{sorted(tg) if len(tg) <= 12 else str(len(tg)) + ' 只'}")
    k = list(out)
    num, den = out[k[0]][0], out[k[2]][0]
    dev = max(abs(num[c] / den[c] - 0.9) for c in num if abs(den[c]) > 1e-12)
    print(f"    身份闸：甲/丙 逐只比值 ≡ 0.9？max|Δ|={dev:.2e}"
          f"｜{'✅ 成立 ⇒ 这一席的实际贡献恰为 0，它只把分母撑大 10%' if dev < 1e-9 else '🚫 不成立 ⇒ 「纯系数」推断被推翻，重看'}")
    if dev >= 1e-9:
        raise SystemExit("甲/丙 比值闸失败")
    dAB = max(abs(out[k[0]][0][c] - out[k[1]][0][c]) for c in num)
    tAB = sorted(out[k[0]][2]) != sorted(out[k[1]][2])
    print(f"    甲 vs 乙（撤库＝换人）：分数最大差 {dAB:.6f}"
          f"｜目标组合{'不同 ⇒ ' + str(sorted(set(out[k[0]][2]) ^ set(out[k[1]][2]))) if tAB else '相同（今天这一层不敏感）'}")
    live = [f["name"] for f in A if f["name"] not in out[k[0]][1]]
    scD, _, tgD, _ = _score_pool([f for f in A if f["name"] != live[0]], pool)
    dD = max(abs(scD[c] - num[c]) for c in scD)
    tD = sorted(tgD) != sorted(out[k[0]][2])
    print(f"    牙（拔一个活席 {live[0]}）：分数最大差 {dD:.6f}、目标组合{'变' if tD else '未变'}"
          f" ⇒ {'✅ 尺子看得见席位级变化，上面的「0」是真读数' if dD > 1e-9 else '🚫 拔活席都看不见 ⇒ 尺子无牙'}")
    return out


def r2(seat, equal, lib):
    print("\n## R2 实盘那一路它实际出了多少力（最后一根 K 线，算式照抄 run_live.py:184-201）")
    if not seat:
        print("  不在榜内，本路无影响")
        return
    A = top10(lib)
    B = top10(lib[lib.name != BAD])
    C = [f for f in A if f["name"] != BAD]
    promoted = [f["name"] for f in B if f["name"] not in [g["name"] for g in A]]
    print(f"  甲＝现状；乙＝它不曾进库（顶上来的是 {promoted}）；丙＝删席不补")
    pool = load(LIVE_CODES)
    print(f"  实盘默认池 {LIVE_CODES}，本机缓存到手 {list(pool)}"
          f"｜缺 {sorted(set(LIVE_CODES) - set(pool))}（那只走网络取数，本脚本不联网）")
    _run_pool("实盘默认池", pool, A, B, C)
    _run_pool("本机缓存宽池", load(R3_CODES), A, B, C)
    print(f"\n  门槛读数：PORTFOLIO.min_score={PORTFOLIO['min_score']}（绝对值）、"
          f"top_k={PORTFOLIO['top_k']}、weighting={PORTFOLIO['weighting']}、"
          f"max_weight={PORTFOLIO.get('max_weight')}"
          f" ⇒ 甲臂把每只分数乘了 0.9，符号不变 ⇒ min_score=0 这一档**结构上翻不了**「买/空仓」；"
          f"能翻的是 min_score>0 的那一档（生产未开）")


def r3(lib):
    NEAR = EA.NEAR_DUP
    print(f"\n## R3 回测/判重那一路（窄池 {len(R3_CODES)} 只，生产全池 ~871 只，别拿这张去对环3 归档）")
    pool = load(R3_CODES)
    names, series = [], {}
    for _, r in lib.iterrows():
        e = str(r.get("expr") or "")
        if e in ("", "nan"):
            continue
        cols = {}
        for c, df in pool.items():
            try:
                cols[c] = safe_eval(e, df)
            except Exception:
                pass
        if len(cols) < 3:
            continue
        wide = pd.concat(cols, axis=1)          # 行=日期，列=标的
        names.append(r["name"])
        series[r["name"]] = wide.median(axis=1)  # 逐日截面中位 ⇒ 一条时间序列（与环3 同向的近似）
    S = pd.DataFrame(series)
    C = S.corr(method="spearman")
    assert abs(C.loc[BAD, BAD] - 1.0) < 1e-12, "自相关必须恰为 1，尺子坏了"
    row = C.loc[BAD].drop(BAD).sort_values(key=lambda s: -s.abs())
    print(f"  可比条目 {len(names)} 条；与它 |rank corr| 最高的 6 条：")
    for n, v in row.head(6).items():
        tag = "🚫 判红(≥RED_BAR)" if abs(v) >= EA.RED_BAR else \
              ("⚠ 危险区(≥NEAR_DUP)" if abs(v) >= EA.NEAR_DUP else "可提名")
        print(f"    {n:42s} {v:+.4f}  {tag}")
    n_red = int((row.abs() >= EA.RED_BAR).sum())
    n_dup = int((row.abs() >= NEAR).sum())
    print(f"  ⇒ 它挡人读数：判红级 {n_red} 条（阈值 RED_BAR={EA.RED_BAR}）、"
          f"危险区 {n_dup} 条（NEAR_DUP={NEAR}）"
          f"｜这些条若是**新候选**来核查，会被按「重复」处理")
    # 牙①：同一张矩阵的全库分布。全矩阵若连一对 ≥NEAR 都没有，上面那个 0 就是恒零读数
    tri = np.triu(np.ones(C.shape, bool), 1)
    vals = C.values[tri]
    vals = vals[np.isfinite(vals)]
    g_red = int((np.abs(vals) >= EA.RED_BAR).sum())
    g_dup = int((np.abs(vals) >= NEAR).sum())
    tooth = (f"尺子有牙：同库 {len(vals)} 对里能量出 {g_dup} 对 ≥{NEAR}"
             if (g_dup or g_red) else
             "🚫 全矩阵零对超线 ⇒ 上面「0 条挡人」是恒零读数，不作数")
    print(f"  牙①（全库 {len(vals)} 对）：判红级 {g_red} 对、危险区 {g_dup} 对，"
          f"最高 |corr| {np.abs(vals).max():.4f}｜{tooth}")
    if not (g_dup or g_red):
        raise SystemExit("负对照失败：判重尺子对全库零敏感")
    # 牙②：库里已知的逐字同式对，必须落在 1.0000（尺子的身份闸）
    dupg = [list(v) for _, v in lib[lib.expr.notna()].groupby("expr")["name"]
            if len(v) > 1]
    for a, b in [tuple(g[:2]) for g in dupg if len(g) >= 2]:
        if a in C.columns and b in C.columns:
            got = C.loc[a, b]
            ok = abs(got - 1.0) < 1e-9
            print(f"  牙② 逐字同式 {a} vs {b} ⇒ corr={got:+.6f} "
                  f"{'✅ 恰为 1' if ok else '🚫 同式不同值 ⇒ 尺子坏了'}")
            if not ok:
                raise SystemExit("负对照失败：同式对没到 1.0")
    # 正对照：换成合法滞后那一版，同一把尺子重算，看它挡不挡人
    honest = "delay(max(high, 5), 1) / ma(df, 90) - delay(min(low, 5), 1) / ma(df, 90)"
    cols = {}
    for c, df in pool.items():
        cols[c] = safe_eval(honest, df)
    hn = "__honest__"
    S2 = S.copy()
    S2[hn] = pd.concat(cols, axis=1).median(axis=1)
    C2 = S2.corr(method="spearman")
    h = C2.loc[hn].drop([hn, BAD]).sort_values(key=lambda s: -s.abs())
    print(f"  对照：同一式子把 -1 换成 +1（合法滞后）⇒ 判红级 {int((h.abs()>=EA.RED_BAR).sum())} 条、"
          f"危险区 {int((h.abs()>=EA.NEAR_DUP).sum())} 条，最高 |corr| {h.abs().max():.4f}")
    return C, row


def r4():
    """R4 落盘清扫：这个名字进过哪些表。只 stat + 只读匹配，不打开任何写句柄。"""
    print("\n## R4 落盘表清扫（它 15:58:57 进库，此后被写的表才算嫌疑面）")
    admission = os.path.getmtime(LIB)
    hitset, newer = set(), []
    for root, dirs, fs in os.walk(os.path.join(ROOT, "etf/v1/data")):
        dirs[:] = [d for d in dirs if d not in ("cache",)]
        for f in fs:
            if not f.endswith((".csv", ".json", ".md")):
                continue
            p = os.path.join(root, f)
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            if BAD in txt:
                hitset.add(p)
            if os.path.getmtime(p) >= admission - 1:
                newer.append((p, os.path.getmtime(p)))
    for p, m in sorted(newer, key=lambda x: -x[1]):
        print(f"  {'带它' if p in hitset else '没它'}｜{time.ctime(m)}｜"
              f"{os.path.relpath(p, ROOT)}")
    print(f"  全库区名字命中：{sorted(os.path.relpath(p, ROOT) for p in hitset)}")
    eq = os.path.join(ROOT, "etf/v1/data/results/equity_daily.csv")
    print(f"  环2 归档（equity_daily/signals_daily/trades_daily）mtime = "
          f"{time.ctime(os.path.getmtime(eq))}，早于 15:58:57 进库"
          f" ⇒ 那三张表按时间就不可能被它污染")


def main():
    # 另一条会话的日更链正在跑（16:16 已 upsert 过一次），故整场只读**一次**快照，
    # 三段共用同一份 DataFrame；跑完再核一次 sha，变了只说明读数已过期、不影响内部一致。
    import hashlib
    raw = open(LIB, "rb").read()
    sha0 = hashlib.sha256(raw).hexdigest()
    lib = pd.read_csv(io.StringIO(raw.decode("utf-8-sig")))
    print(f"# 库快照＝{LIB} mtime={time.ctime(os.path.getmtime(LIB))} sha256={sha0[:12]}"
          f"｜{len(lib)} 行（active {int((lib.status=='active').sum())}）"
          f"｜尺子＝判重那三条线 RED_BAR/NEAR_DUP 直读 etf_admission")
    seat, equal = r1(lib)
    dead = r2(seat, equal, lib)
    r3(lib)
    r4()
    sha1 = hashlib.sha256(open(LIB, "rb").read()).hexdigest()
    print(f"\n## 读数期间生产库 sha256 {'未变（三段同一份快照）' if sha1 == sha0 else '已变 ⇒ 下面三段是 ' + sha0[:12] + ' 那一版，生产库已经前进了'}")
    print("EXIT=0（本脚本零写入：只读库、只读归档、只读日线缓存）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
