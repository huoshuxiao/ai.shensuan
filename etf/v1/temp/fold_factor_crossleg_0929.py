# -*- coding: utf-8 -*-
"""选项丙：折内**真挖出的因子**（不是模板）有没有截面腿？顺带钉死一件事——
挖掘那把 IC 尺子对"纯截面信息"是结构性失明的。

数据源：断点缓存 `common/data/etf/cache/run_checkpoint_daily.pkl`
  entry '主线多源因子' ⇒ 面板 = 全池全历史（主线挖掘看到的就是这个）
  entry '折 k 因子'   ⇒ 面板 = 该折训练段 + >240 bar 闸（walk_forward.py:91-93）
四条读数一起摆在同一张面板上：
  原始     = 逐只时序 IC 再对标的取均值（= 判据用的那个数）
  时序腿   = 当日截面均值那一列当因子（把所有标的共用的高度）
  去均值腿 = f_it − 当日截面均值，仍按逐只时序 IC 平均
             ⚠️ 它叫"去均值"不叫"截面"：去掉的是共同市场高度，算法还是时序的。
  逐日 RankIC = 真正截面尺子，准入层那把（MIN_CS=30，当日两侧都有值的标的不足
             30 只整日丢弃，见 etf_admission.py:475）

三枚 oracle 是这把尺子的牙。**都是用真标签反造的合成因子，只用来验尺子灵敏度，
不进任何判据、不代表真信号**：
  O1  纯时序：f_it = 该标的上一日已实现的次日收益（自己预测自己，无截面内容）
  O2  静态截面正序：c_i = 该标的整段平均次日收益的名次（逐时常量）
  O2b 静态截面反序：把 O2 整个反过来
  ⇒ O1 过闸而 O2/O2b 在挖掘口径**恒为 0.0000**（正序反序都是 0，这是类别级性质，
     不是"这一枚太弱"），同时这两枚在逐日 RankIC 下等幅反号 ⇒ 才可以说
     「真因子的截面读数上不去」意味着「判据看不见静态截面这一整类因子」，
     而不是「这些因子恰好都不行」。

自检（不成立就 die 或当场报⚠️，不许静默出数）：
  D1 恒等闸 f = 去均值腿 + 当日截面均值，逐格 max|Δ| < 1e-12；
  D2 复现闸 每条真因子重算的原始 IC 与 dict 里存的读数对表（|Δ|<5e-4 记 ✅，
     存的是别的口径就如实报"差多少/无键"，不许假装对上）；
  D2b 来源闸 simple 引擎的因子没有 expr，走 impl 里存的逐标的序列拆腿——
     那是判据当场打过分的那一串数。核 impl 逐只 ic 均值 == dict 的 mean_ic，
     对不上就说明 impl 不是这一枚的评分源，读数标⚠️不引用；
  D3 O1 必须 |原始 IC| ≥ 0.01 ⇒ 否则这把尺子连自己的 oracle 都打不出分，全废；
  D4 O2 与 O2b 的原始 IC 必须都恰为 0，且两者逐日 RankIC 等幅反号（|和|<1e-9、
     |值|≥0.02）；窄面板上 MIN_CS=30 会把所有日子丢光 ⇒ 那格写「此面板无法演示」，
     不判红。
"""
import os
import pickle
import re
import sys

import numpy as np
import pandas as pd
from scipy.stats import rankdata

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: F401

from config import FREQ                                        # noqa: E402
from data_loader import DataLoader                             # noqa: E402
from etf_universe import get_universe                          # noqa: E402
from factor_dsl import safe_eval, compute_ic                   # noqa: E402
from walk_forward import make_splits                           # noqa: E402

CKPT = os.path.abspath(os.path.join(BYPASS, "..", "..", "common", "data",
                                    "etf", "cache", "run_checkpoint_daily.pkl"))
GATE = 240
IC_GATE = 0.01            # 仅作 oracle 的"尺子活着"判据用，不是链路那道闸
# 链路上真实生效的两道（09-30 import 实测）：
BIND_GATE = 0.005         # main.py:65 select_factors 默认 + 逐源地板 ⇒「过 IC 门槛 N」判的
IC_FLAG = 0.02            # config IC_THRESHOLD(daily)，llm agent 筛自己 knowledge_base 用
MIN_CS = 30                 # etf_admission.py 的当日有效标的数下限


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def fmt(v):
    return "  n/a " if v is None else f"{v:+.4f}"


def label(df):
    return df["close"].pct_change().shift(-1)


def mean_ic_of(series_by_code, train_pool):
    """挖掘口径：逐只时序 IC ⇒ 对标的取均值"""
    ics = []
    for c, s in series_by_code.items():
        pair = pd.concat([s.dropna(), label(train_pool[c])], axis=1).dropna()
        if len(pair) < 30:
            continue
        ic = compute_ic(pair.iloc[:, 0], pair.iloc[:, 1])
        if ic is not None and np.isfinite(ic):
            ics.append(float(ic))
    return (float(np.mean(ics)) if ics else None), len(ics)


def cs_daily_rank_ic(st, ymat, min_cs=MIN_CS):
    """准入层口径：逐日截面 RankIC 均值；返回 (均值, 可算天数, 被丢天数)"""
    vals, kept, dropped = [], 0, 0
    for d in st.index:
        pair = pd.concat([st.loc[d], ymat.loc[d] if d in ymat.index
                          else pd.Series(dtype=float)], axis=1,
                         join="inner").dropna()
        if len(pair) < min_cs:
            dropped += 1
            continue
        a = rankdata(pair.iloc[:, 0].values)
        b = rankdata(pair.iloc[:, 1].values)
        if np.std(a) == 0 or np.std(b) == 0:
            continue
        vals.append(float(np.corrcoef(a, b)[0, 1]))
        kept += 1
    return (float(np.mean(vals)) if vals else None), kept, dropped


def legs_from_wide(train_pool, st):
    """从对齐好的宽表拆三腿。全部同一张面板。

    D1 恒等闸用**相对**容差：`f = (f − 当日均值) + 当日均值` 在浮点里本来就
    不逐位相等，误差随量纲走。量能因子值域到 1.6e9，那一格 1 个 ulp 就是 2.4e-7
    ⇒ 写死绝对 1e-12 会把"算术正确"判成"拆腿实现错"（09-30 折 2
    「10-day SMA of Volume」实测：绝对残差 2.384e-07 / 相对 1.509e-16 ≈ 一个
    float64 ε，即等式本来成立）。相对阈值取 1e-13 ≈ 450 个 ulp 的余量，
    既能放过舍入，又绝放不过"减错了东西"（负对照见文件末尾 --d1-selftest）。"""
    day_mean = st.mean(axis=1)
    dm = st.sub(day_mean, axis=0)
    resid = float((dm.add(day_mean, axis=0) - st).abs().max().max())
    scale = float(st.abs().max().max()) or 1.0
    resid_rel = resid / scale
    m_raw, n_raw = mean_ic_of({c: st[c] for c in st}, train_pool)
    m_mkt, _ = mean_ic_of({c: day_mean.reindex(st[c].index)
                           for c in st}, train_pool)
    m_dm, _ = mean_ic_of({c: dm[c] for c in st}, train_pool)
    return {"原始": m_raw, "时序腿": m_mkt, "去均值腿": m_dm,
            "恒等残差": resid, "恒等残差相对": resid_rel,
            "量纲": scale, "st": st, "可算只数": n_raw}


def panel_legs(train_pool, ffun):
    """expr 路径：从表达式在训练段面板上重算因子值"""
    raw = {c: ffun(df).dropna() for c, df in train_pool.items()}
    raw = {c: s for c, s in raw.items() if not s.empty}
    if not raw:
        return None
    return legs_from_wide(train_pool, pd.concat(raw, axis=1).sort_index())


def impl_legs(train_pool, impl):
    """impl 路径：simple 引擎的因子没有 expr 键，但 dict 里存着挖掘当场算出的
    逐标的因子序列（llm_factor_agent.py:_evaluate 的 impl[code]["factor"]）。
    拿它拆腿比"跳过"强——那是判据真正打过分的那一串数，不是我的重算。
    同时核一遍 impl 里的逐只 ic 均值 == dict 存的 mean_ic（D2b），
    对不上说明 impl 不是这一枚的评分源，读数不许用。"""
    if not isinstance(impl, dict) or not impl:
        return None
    raw, ic_vals = {}, []
    for c, v in impl.items():
        if c not in train_pool or not isinstance(v, dict):
            continue
        s = v.get("factor")
        if isinstance(s, pd.Series):
            s = s.dropna()
            if not s.empty:
                raw[c] = s
        ic = v.get("ic")
        if ic is not None and np.isfinite(float(ic)):
            ic_vals.append(float(ic))
    if not raw:
        return None
    r = legs_from_wide(train_pool, pd.concat(raw, axis=1).sort_index())
    r["来源"] = "impl 存的序列"
    r["impl_ic均值"] = float(np.mean(ic_vals)) if ic_vals else None
    r["impl只数"] = len(ic_vals)
    return r


if "--d1-selftest" in sys.argv:
    # 负对照（把 D1 换成相对容差之后必须补的一格）：相对阈值放宽了 3 个数量级，
    # 得证明它仍能抓住"减错了东西"，而不是变成一条永远绿的闸。
    # 三种错法都用**正确的**当日均值加回去 ⇒ 恒等式必然不成立 ⇒ 都该判红。
    print("\n──────── --d1-selftest：D1 恒等闸的牙（不加载真面板）────────")
    idx = pd.date_range("2015-01-05", periods=60)
    cols = [f"5103{i:02d}" for i in range(5)]
    st_t = pd.DataFrame(np.random.default_rng(5).normal(1e7, 1e6, (60, 5)),
                        index=idx, columns=cols)          # 量能那种 1e7 量级
    day_mean = st_t.mean(axis=1)

    def _rel(kind):
        if kind == "对":
            dm = st_t.sub(day_mean, axis=0)
        elif kind == "全局均值":
            dm = st_t.sub(float(st_t.values.mean()), axis=0)
        elif kind == "错一天的均值":
            dm = st_t.sub(day_mean.shift(1).fillna(day_mean.iloc[0]), axis=0)
        elif kind == "只减一半":
            dm = st_t.sub(0.5 * day_mean, axis=0)
        return float((dm.add(day_mean, axis=0) - st_t).abs().max().max()) / \
            float(st_t.abs().max().max())

    bad = []
    for kind in ("对", "全局均值", "错一天的均值", "只减一半"):
        rel = _rel(kind)
        red = rel > 1e-13
        want_red = kind != "对"
        ok = red == want_red
        bad.append(kind if not ok else None)
        print(f"    {kind:8s} 相对残差 {rel:.2e} ⇒ {'判红' if red else '放行'}"
              f"（期望 {'判红' if want_red else '放行'}）{'✅' if ok else '❌'}")
    if [b for b in bad if b]:
        die(f"D1 自检失败：{[b for b in bad if b]} 的判决和期望相反 ⇒ 这把闸不可信")
    print(f"    ✅ 正确实现放行、三种错法全判红 ⇒ D1 有牙（量纲 1e7 下 1 个 ulp"
          f" = {np.spacing(1.6e9):.2e}，绝对 1e-12 那道会把正确实现判成错）")
    sys.exit(0)


universe = get_universe()
pool = DataLoader(freq=FREQ).load_pool(universe.universe["code"].tolist())
ref_code = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref_code].index
splits = make_splits(all_ts)

if not os.path.exists(CKPT):
    die(f"断点缓存不存在：{CKPT}")
blob = pickle.load(open(CKPT, "rb"))
entries = blob.get("entries") or {}
print(f"断点缓存 {os.path.basename(CKPT)}｜数据指纹 末日 "
      f"{blob['fingerprint'].get('ts_last')}，现有 entry："
      f"{'、'.join(repr(k) for k in entries) or '（空）'}")


def panel_for(fold_no):
    if fold_no is None:
        return dict(pool), f"全池 {len(pool)} 只 × {len(all_ts)} bar"
    tr_s, tr_e = splits[fold_no - 1][0], splits[fold_no - 1][1]
    tp = {c: df.loc[tr_s:tr_e] for c, df in pool.items()}
    tp = {c: df for c, df in tp.items() if len(df) > GATE}
    med = int(np.median([len(d) for d in tp.values()]))
    return tp, f"折 {fold_no} 训练段 {len(tp)} 只 × {med} bar"


fold_pat = re.compile(r"^折 (\d+) 因子$")
targets = []
if "主线多源因子" in entries:
    targets.append(("主线", None, entries["主线多源因子"]["value"]))
for k, v in sorted(entries.items()):
    m = fold_pat.match(k)
    if m:
        targets.append((k, int(m.group(1)), v["value"]))
if not targets:
    die("缓存里没有可读的因子段 ⇒ 无事可量")

print("\n──────── 真因子四条读数（原始 = 判据看的数；最后一列 = 准入那把尺子）"
      "────────")
for tag, fold_no, facs in targets:
    tp, desc = panel_for(fold_no)
    ymat = pd.concat({c: label(df) for c, df in tp.items()}, axis=1).sort_index()
    print(f"\n▎{tag}｜{desc}")
    for f in facs or []:
        expr = f.get("expr") or ""
        nm = str(f.get("name") or f.get("formulation") or expr)[:30]
        src = str(f.get("source") or "?")
        r, via = None, ""
        if expr:
            try:
                r = panel_legs(tp, lambda df, e=expr: safe_eval(e, df))
                via = "expr 重算"
            except Exception as ex:
                print(f"    {nm:30s}｜{src:8s} 求值失败 {type(ex).__name__}: "
                      f"{str(ex)[:60]}")
                continue
        else:
            # simple 引擎只给名字不给表达式 ⇒ 用判据当场打过分的序列
            r = impl_legs(tp, f.get("impl"))
            via = "impl 存的序列"
            if r is None:
                print(f"    {nm:30s}｜{src:8s} 既无 expr 又无可对齐的 impl "
                      f"⇒ 跳过（不猜表达式）")
                continue
        if r is None:
            print(f"    {nm:30s}｜{src:8s} 全面板求值为空 ⇒ 跳过")
            continue
        if r["恒等残差相对"] > 1e-13:
            die(f"D1 {tag}/{nm} 恒等重建**相对**残差 {r['恒等残差相对']:.2e}"
                f"（绝对 {r['恒等残差']:.2e}／量纲 {r['量纲']:.3g}）⇒ 拆腿实现错")
        cs, kept, dropped = cs_daily_rank_ic(r["st"], ymat)
        stored = f.get("mean_ic")
        note = ""
        if stored is None:
            note = f"（dict 无 mean_ic 键，键={sorted(f.keys())} ⇒ 无法对表）"
        else:
            d = abs(float(stored) - r["原始"])
            note = (f"（存 {float(stored):+.4f}｜重算 {r['原始']:+.4f}｜"
                    f"{'D2 ✅对表' if d < 5e-4 else f'D2 ⚠️差 {d:.4f}，可能不是同一口径'}"
                    f"）")
            if via == "impl 存的序列":
                iv = r.get("impl_ic均值")
                if iv is None:
                    note += "（impl 无逐只 ic ⇒ D2b 无从校）"
                else:
                    d2b = abs(iv - float(stored))
                    note += (f"｜impl 逐只 ic 均值 {iv:+.4f} "
                             f"{'D2b ✅' if d2b < 5e-4 else f'D2b ⚠️与 mean_ic 差 {d2b:.4f}'}")
        print(f"    {nm:30s}｜{src:8s}｜{via:9s} 原始 {fmt(r['原始'])}｜"
              f"时序腿 {fmt(r['时序腿'])}｜去均值腿 {fmt(r['去均值腿'])}｜"
              f"逐日RankIC {fmt(cs)}（可算 {kept} 天/丢 {dropped} 天，"
              f"可算 {r['可算只数']} 只）{note}")

print("\n──────── 三枚 oracle：这把 IC 尺子看不看得见截面信息 ────────")
for tag, fold_no in [("折 1", 1), ("主线", None)]:
    tp, desc = panel_for(fold_no)
    ymat = pd.concat({c: label(df) for c, df in tp.items()}, axis=1).sort_index()
    o1 = {c: label(df).shift(1) for c, df in tp.items()}      # 纯时序
    st1 = pd.concat(o1, axis=1).sort_index()
    m1, n1 = mean_ic_of({c: st1[c] for c in st1}, tp)
    cs1, k1, d1 = cs_daily_rank_ic(st1, ymat)

    avg = ymat.mean(axis=0)                                   # 纯截面（逐时常数）
    rk = avg.rank(pct=True)
    st2 = pd.concat({c: pd.Series(float(rk[c]), index=tp[c].index)
                     for c in tp}, axis=1).sort_index()
    m2, n2 = mean_ic_of({c: st2[c] for c in st2}, tp)
    cs2, k2, d2 = cs_daily_rank_ic(st2, ymat)
    # O2b 反序：同一类静态截面排序，方向反过来。正序/反序在挖掘口径都必须是 0，
    # 而在准入口径应当幅度相同、符号相反 ⇒ 证明失明覆盖「这一整类因子」，
    # 不是恰好这一枚太弱
    st2b = (-st2)
    m2b, _ = mean_ic_of({c: st2b[c] for c in st2b}, tp)
    cs2b, _, _ = cs_daily_rank_ic(st2b, ymat)

    print(f"\n▎{tag}｜{desc}（原始 IC 可算的标的数：O1 {n1}、O2 {n2}）"
          f"｜闸口径：真闸 0.005（main.py:65）/ llm 旗 {IC_FLAG}（config_base.py:112）")
    print(f"    O1 纯时序 oracle：原始 {fmt(m1)} ⇒ "
          f"{'过真闸' if m1 is not None and abs(m1) >= IC_GATE else '没过真闸'}"
          f"／{'过 llm 旗' if m1 is not None and abs(m1) >= IC_FLAG else '没过 llm 旗'}"
          f"｜逐日 RankIC {fmt(cs1)}（可算 {k1} 天/丢 {d1} 天）")
    print(f"    O2 静态截面正序：原始 {fmt(m2)}｜逐日 RankIC {fmt(cs2)}"
          f"（可算 {k2} 天/丢 {d2} 天）")
    print(f"    O2b 静态截面反序：原始 {fmt(m2b)}｜逐日 RankIC {fmt(cs2b)}")
    if m1 is None or abs(m1) < IC_FLAG:
        print(f"    ⚠️ D3 未成立：连纯时序 oracle 都过不了链路上最严那道旗"
              f"（{IC_FLAG}）⇒ 这把尺子的读数整体不可解释，上面所有腿作废")
        continue
    if k2 == 0:
        print(f"    ⓘ D4 在此面板无法演示：当日不足 {MIN_CS} 只，"
              f"逐日 RankIC 的日子全被丢掉（折 1 只有 4 只 ⇒ 一天都不剩）。"
              f"这本身就是条读数：准入那把尺子在这张面板上无定义。")
    elif m2 is None or abs(m2) > 1e-12 or m2b is None or abs(m2b) > 1e-12:
        print(f"    ⚠️ D4 未成立：静态截面因子在挖掘口径读出 "
              f"{fmt(m2)} / {fmt(m2b)} ≠ 0 ⇒ 「类别级失明」不成立")
    elif cs2 is None or cs2b is None or abs(cs2) < 0.02 \
            or abs(cs2 + cs2b) > 1e-9:
        print(f"    ⚠️ D4 未成立：正序 {fmt(cs2)} 与反序 {fmt(cs2b)} 没有"
              f"「等幅反号」⇒ 对照不成立，失明结论不许引用")
    else:
        print(f"    ✅ D3+D4 同时成立：时序型 oracle 打得出分（{m1:+.4f}，过闸）；"
              f"静态截面这一整类因子——正序、反序都是——挖掘口径恒为 0.0000，"
              f"而准入口径读得出 {cs2:+.4f} / {cs2b:+.4f}（等幅反号）"
              f" ⇒ 判据对这一类因子是构造性失明"
              f"（factor_dsl.py:195 常量列返回 0），不是这一枚弱")

print("\n读数边界：O1/O2 是用真标签反造的合成因子，只证明尺子的灵敏度边界，"
      "不构成任何信号证据；真因子有没有截面信息，看上面那张四条读数的表里「逐日RankIC」那一列。")
