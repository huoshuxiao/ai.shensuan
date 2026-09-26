# -*- coding: utf-8 -*-
"""待买入名单的行业集中度：50 只收成 5 只，会不会 5 只全在同一个板块（09-24）

用户在 A/B/C/D 四条路之间还没定，卡点是「5 只太集中」这件事一直没量过。面板本身
没有行业字段，所以先由 `stock/v1/src/data/fetch_industry_map.py` 落一份外部映射
（新浪 49 板块为主 + 深市官方证监会一级兜底），本脚本再对着它数三件事：

 1. **当日（09-23 生产名单）真实分布**：top5 / top10 / top20 / top50 各自的
    最大单一行业占比、行业个数、未知占比；top5 直接从生产 `buy_20260923.csv`
    的前 5 行取，不在此重算判据。
 2. **历史抽样**（2015-01 起每月最后一个交易日，约 141 个调仓日）：同样三量按年
    汇总，看「5 只挤一坨」是常态还是今天凑巧。近似口径见 `pick_hist` 的 docstring。
 3. **同行业配对占比**：top5 的两两配对里有多少落在同一行业，与 top50 对比。
    这是 `shell/topn_concentration_0924.py` 那两个相关系数（0.368 vs 0.312）的
    可解释版本 —— 相关高是不是因为同板块。

两条口径必须分开数（本脚本的主口径 = 新浪 49 板块）：兜底那 1342 只挂的是**证监会
一级行业名**，与新浪板块名不在同一套分类里。混用会把同一个实体行业拆成两个名字
（新浪「电子器件」/ 证监会「制造业」），集中度**系统性偏低**，正好把要判的问题做没。
所以未知列 = 无映射 ∪ 只有证监会标签；`--mixed` 另跑一遍混用版，两个数一起看才是
上下界。

**分母一律用「已知有行业的只数」，不是名单张数。** 把未知并成一个类去算最大占比，
量出来的其实是映射缺口：新浪这 49 板块漏科创板 96%，2021 年起名单里科创板/新股变多，
那个假「未知」板块自己就能占到四五成，读出来是「很集中」，实际是「不知道」。
"""
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(ROOT, "stock/v1/src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "strategy"))

import glob

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401
from config import (ASHARE_SIGNAL_DIR, ASHARE_INDUSTRY_CSV, ASHARE_PORT_MIN_AMOUNT,
                    ASHARE_PORT_MIN_LISTED, ASHARE_PORT_LIMIT_UP)
from ashare_screen import BUY_EXPR, build_matrices, guard_ret, load_panel

UNKNOWN = "未知"
MIXED = "--mixed" in sys.argv
NS = (5, 10, 20, 50)
SIG_DIR = ASHARE_SIGNAL_DIR if os.path.isabs(ASHARE_SIGNAL_DIR) else os.path.join(
    os.path.join(ROOT, "stock/v1"), ASHARE_SIGNAL_DIR)


def load_map():
    """外部行业映射 -> (新浪口径 industry Series, 证监会兜底 industry Series)

    行业源 三类的含义（fetch_industry_map.py 落盘时的标记）：
      新浪+深市官方 / 新浪  -> `行业` 是新浪板块名（49 个，互斥）
      深市官方             -> `行业` 是证监会一级名（19 个，另一套口径）
    """
    p = os.environ.get("STOCK_INDUSTRY_CSV") or ASHARE_INDUSTRY_CSV
    d = pd.read_csv(p, dtype=str).fillna("")
    sina_only = d["行业源"].isin(["新浪", "新浪+深市官方"])
    a = pd.Series(np.where(sina_only, d["行业"], UNKNOWN), index=d["代码"]).sort_index()
    b = d.set_index("代码")["行业"].replace("", UNKNOWN).sort_index()
    print(f"[映射] {p}：{len(d)} 只；新浪口径可用 {int(sina_only.sum())} 只、"
          f"证监会兜底 {int((~sina_only).sum())} 只；抓取日 {d['抓取日'].max()}")
    return (b if MIXED else a), (a if MIXED else b)


def stat(codes, ind, n):
    """n 只票的行业集中度

    主数 **最大占比_已知内**（最大实体行业 / 有行业的只数），未知不当行业数：
    把「未知」并成一个类去算最大占比，等于用映射缺口冒充集中度 —— 2021 年起新浪
    漏的科创板一多，那条假板块就能占到 40~80%，读出来是「很集中」，实际是「不知道」。
    所以覆盖率（已知数）必须和集中度一起看，`行业数` 只数实体行业、不含未知。
    """
    s = ind.reindex(codes).fillna(UNKNOWN)
    vc = s.value_counts()
    kn = int((s != UNKNOWN).sum())
    kv = vc.drop(UNKNOWN, errors="ignore")
    return {"已知数": kn, "最大行业": (kv.index[0] if len(kv) else UNKNOWN),
            "最大占比": (float(kv.iloc[0]) / kn) if kn else np.nan,
            "占整张名单": (float(kv.iloc[0]) / n) if kn else 0.0,
            "行业数": int(len(kv)), "未知数": n - kn}


def pair_share(codes, ind):
    """两两配对里同行业的占比（与 topn_concentration_0924.py 的相关系数同一段问题）

    只在**有行业的票**之间数配对：两只都未知不等于同行业，那样会把映射缺口算成分散度。
    """
    s = ind.reindex(codes).fillna(UNKNOWN).to_numpy()
    s = s[s != UNKNOWN]
    same = tot = 0
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            tot += 1
            same += s[i] == s[j]
    return (same / tot if tot else np.nan), tot


# ---------- 1 当日生产名单 ----------
sig_files = sorted(glob.glob(os.path.join(SIG_DIR, "signal_*.csv")))
d_last = os.path.basename(sig_files[-1])[7:15]
buy = pd.read_csv(os.path.join(SIG_DIR, f"buy_{d_last}.csv"), dtype={"code": str})
ind, ind_alt = load_map()
print(f"\n===== 1 当日名单（生产 buy_{d_last}.csv，{len(buy)} 只，按名次）=====")
codes_all = list(buy["code"])
for n in NS:
    r = stat(codes_all[:n], ind, n)
    ps, pt = pair_share(codes_all[:n], ind)
    print(f"  top{n:<3d}：已知 {r['已知数']}/{n}、最大行业「{r['最大行业']}」占已知 "
          f"{r['最大占比']:.0%}（占整张名单 {r['占整张名单']:.0%}）、覆盖 {r['行业数']} 个行业、"
          f"同行业配对 {ps:.0%}（{pt} 对）")
    if n == 5:
        s5 = ind.reindex(codes_all[:5]).fillna(UNKNOWN)
        print(f"        top5 = {list(zip(codes_all[:5], buy['name'].tolist()[:5], s5.tolist()))}")
# 两条口径的差 = 那 3 只只有证监会标签的票 + 1 只两源都没有的票（见 load_map docstring）
alt_all = stat(codes_all, ind_alt, len(codes_all))
sin_all = stat(codes_all, ind, len(codes_all))
print(f"  [上下界] 混用证监会兜底口径数整张 {len(codes_all)} 只：已知 {alt_all['已知数']} 只、"
      f"最大行业「{alt_all['最大行业']}」占已知 {alt_all['最大占比']:.0%} —— "
      f"比新浪口径的 {sin_all['最大占比']:.0%}（「{sin_all['最大行业']}」）高，"
      f"因为 18 类比 49 类粗")

# ---------- 2 历史抽样 ----------
wide, _b = load_panel()
mtx = build_matrices(wide)
del wide
cl, raw, vol = mtx["close"], mtx["raw_price"], mtx["volume"]
q = vol.rolling(20).std()                        # 本探针跑的是**换轴前**口径（当时 BUY_EXPR=ts_std(volume,20)）；
                                                 # 09-24 换轴后现轴 = ts_mean(volume,20)，换口径要重跑第 8/9 节

# 近似闸门：因子有值 + 上市满 N 日 + 20 日均额达标 + 当日真有行情有量 + 不追涨停
gate = (cl.notna() & raw.notna() & vol.gt(0)
        & (mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
        & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
print(f"\n===== 2 历史抽样：闸门近似覆盖 {int(gate.values.sum()):,} 个(票,日)格子，"
      f"面板 {cl.index[0].date()} ~ {cl.index[-1].date()} =====")


def pick_hist(day, n, qq=None):
    """某调仓日的低分侧前 n 只（**近似**生产名单，差三件事，都往「池子更宽」偏）：
      1) 不跑量能族四条剔除（那要 4 个表达式逐票求值，本问的是分散度不是收益）；
      2) 没有 ST 闸（历史日无收盘快照，生产侧同样没有）；
      3) 涨停闸用当日涨幅≥ ASHARE_PORT_LIMIT_UP 近似生产的「次日开盘跳升」。
    行业映射本身是**今日截面**贴到历史日期上 ⇒ 分年度数字带重分类/前视偏差，
    只用来回答「5 只会不会挤在同一板块」这种量级问题，不当收益证据。
    `qq` 换排名轴（默认 STD($volume,20)；第 6 节传真手数口径看漂移是不是复权假象）。
    """
    prev = cl.index[cl.index.get_loc(day) - 1]
    chg = guard_ret(cl.loc[day] / cl.loc[prev] - 1.0, raw.loc[day] / raw.loc[prev] - 1.0)
    ok = gate.loc[day] & ~chg.fillna(False).ge(ASHARE_PORT_LIMIT_UP)
    v = (q if qq is None else qq).loc[day].astype("float64").where(ok).dropna()
    return list(v.sort_values().index[:n]), int(ok.sum())


days = pd.Series(cl.index).groupby([cl.index.year, cl.index.month]).max().tolist()
rows, unk_rows, seg_rows = [], [], []


def seg(code):
    """代码段：映射缺口按段差得极多（见 fetch_industry_map 的「口径边界」），
    未知集中在哪一段，决定了上面那些占比能不能信。"""
    s = str(code)
    if s.startswith("BJ"):
        return "北交所"
    if s.startswith("SH"):
        return "科创板" if s[2:5] in ("688", "689") else "沪市主板"
    return "创业板" if s[2] == "3" else "深市主板"

for d0 in days:
    if cl.index.get_loc(d0) < 21:
        continue
    for n in NS:
        codes, pool_n = pick_hist(d0, n)
        if len(codes) < n:
            continue
        r = stat(codes, ind, n)
        ps, pt = pair_share(codes, ind)
        r.update(名单=n, 年=d0.year, 调仓日=d0.date(), 池=pool_n,
                 同行业配对=ps, 配对数=pt)
        rows.append(r)
        # 段构成：「5 只全在同一段市场」是另一种集中，行业映射管不着，单独记
        cs = pd.Series([seg(c) for c in codes]).value_counts()
        for sgm, cnt in cs.items():
            seg_rows.append({"年": d0.year, "调仓日": d0.date(), "名单": n, "段": sgm, "只数": cnt})
        # 未知落在哪一段也要记：占比的分母就是被它们吃掉的
        for c in codes:
            if str(ind.get(c, UNKNOWN)) == UNKNOWN:
                unk_rows.append({"年": d0.year, "名单": n, "段": seg(c)})
h = pd.DataFrame(rows)
h.to_csv("/tmp/industry_hist_0924.csv", index=False)

def yearly(col, mul=100, fmt="{:5.0f}%"):
    piv = h.pivot_table(index="年", columns="名单", values=col, aggfunc="median")
    return (piv * mul).round(0).to_string(float_format=lambda x: fmt.format(x))

print(f"[抽样] {h['调仓日'].nunique()} 个调仓日 × {len(NS)} 档 = {len(h)} 行，"
      f"落 /tmp/industry_hist_0924.csv")
print("\n按年：最大单一实体行业占「已知只数」的中位（口径不含未知）")
print(yearly("最大占比"))
print("\n按年：名单里已知有行业的只数中位（上面那些数的可信度看这张）")
piv2 = h.pivot_table(index="年", columns="名单", values="已知数", aggfunc="median")
print(piv2.round(1).to_string())
print("\n按年：同行业配对占比中位（只在已知票之间数）")
print(yearly("同行业配对"))

print("\n全抽样汇总（月频调仓日中位；占比一律按已知只数为分母）：")
for n in NS:
    g = h[h["名单"] == n]
    if not len(g):
        continue
    line = (f"  top{n:<3d}：最大行业占已知 中位 {g['最大占比'].median():.0%}"
            f"（p90 {g['最大占比'].quantile(.9):.0%}）、行业数 中位 {g['行业数'].median():.0f}、"
            f"已知 中位 {g['已知数'].median():.1f}/{n}、"
            f"同行业配对 中位 {g['同行业配对'].median():.0%}")
    if n == 5:
        # 「5 只全同行业」只在 5 只都有行业时才算得出，所以分母用那批天
        f5 = g[g["已知数"] == 5]
        line += (f"；5 只全同行业 {(f5['最大占比'] >= 0.999).sum()}/{len(f5)} 天，"
                 f"已知不足 5 只的 {int((g['已知数'] < 5).sum())}/{len(g)} 天")
    print(line)
# 最大行业在历史上由谁占据：集中度是不是同一个板块在贡献
print("\n历史上「最大单一实体行业」出现频次 top6（top5 档）：")
print(h[h["名单"] == 5]["最大行业"].value_counts().head(6).to_string())
print("\n历史上「最大单一实体行业」出现频次 top6（top50 档）：")
print(h[h["名单"] == 50]["最大行业"].value_counts().head(6).to_string())

# ---------- 3 未知落在哪一段：上面那些占比的可信度诊断 ----------
u = pd.DataFrame(unk_rows)
print(f"\n===== 3 映射缺口：历史名单里 {len(u)} 个(票,日)格子没行业 =====")
if len(u):
    seg_t = u.groupby(["年", "段"]).size().unstack(fill_value=0)
    print("每年四档名单里落进各代码段的未知只数（一格 = 一只票的一个调仓日）：")
    print(seg_t.to_string())
    print(f"\ntop5 档的未知按段汇总：{dict(u[u['名单'] == 5]['段'].value_counts())}"
          f"（对照：top5 共 {len(h[h['名单'] == 5]) * 5} 格）")

    # ---------- 4 代码段本身：集中度不只「同行业」，还有同一段市场 ----------
    print("\n===== 4 代码段构成（只数/调仓日 平均；2014 只有 4 个月、2026 到 9 月，故取平均）=====")
    sr = pd.DataFrame(seg_rows)
    for n in (5, 50):
        g = sr[sr["名单"] == n]
        tot = g.groupby(["年", "段"])["只数"].sum().unstack(fill_value=0)
        t = tot.div(g.groupby("年")["调仓日"].nunique(), axis=0)
        print(f"\ntop{n}（单位：只/调仓日）：")
        print(t.round(2).to_string())

    # ---------- 5 近似口径与生产名单对得上吗 + 三个具体调仓日的 top5 ----------
    approx50, _ = pick_hist(cl.index[-1], 50)
    inter = set(approx50) & set(codes_all)
    print(f"\n===== 5 近似 vs 生产（{cl.index[-1].date()} 同一份面板末端）=====")
    print(f"  近似 top50 与生产 buy_{d_last}.csv 的交集 {len(inter)}/50"
          f"（差的那 {50 - len(inter)} 只来自量能族四条剔除未跑，见 pick_hist）")
    for y, m0 in ((2021, 6), (2024, 12), (2025, 6)):
        dd = [x for x in days if x.year == y and x.month == m0]
        if not dd:
            continue
        c5, pn = pick_hist(dd[0], 5)
        det = [(c, seg(c), str(ind.get(c, UNKNOWN)),
                f"{q.loc[dd[0], c]:.0f}", f"{mtx['amount20'].loc[dd[0], c] / 1e8:.2f}亿") for c in c5]
        print(f"  {dd[0].date()}（池 {pn} 只）top5：{det}")

    # ---------- 6 换「真手数」排名轴：第 4 节那段北交所漂移是行情还是复权基准假象 ----------
    print("\n===== 6 排名轴口径对照：adj=STD($volume,20)（生产用）vs real=STD(真实手数,20) =====")
    print("为什么这一节必须跑：面板里 $volume = 真实手数/$factor（ashare_screen 文件头），"
          "所以 $factor 越大的票在这根轴上天然越「安静」。\n北交所恰好是高复权因子（低价、"
          "多次送转）密度最高的一段 ⇒ 第 4 节测到的漂移里可能混着复权基准的机械效应。"
          "若换 real 口径后北交所占比明显回落，那一段就不能全算成「小票真的安静」。")
    q_real = mtx["volume_real"].rolling(20).std()      # volume_real = $volume×$factor
    seg_rows_real = []
    for d0 in days:
        if cl.index.get_loc(d0) < 21:
            continue
        for n in (5, 50):
            codes, _ = pick_hist(d0, n, q_real)
            if len(codes) < n:
                continue
            cs = pd.Series([seg(c) for c in codes]).value_counts()
            for sgm, cnt in cs.items():
                seg_rows_real.append({"年": d0.year, "调仓日": d0.date(), "名单": n,
                                      "段": sgm, "只数": cnt})
    sra = pd.DataFrame(seg_rows)
    srr = pd.DataFrame(seg_rows_real)
    for n in (5, 50):
        merged = None
        for tag, sr in (("adj", sra), ("real", srr)):
            g = sr[sr["名单"] == n]
            tot = g.groupby(["年", "段"])["只数"].sum().unstack(fill_value=0)
            t = tot.div(g.groupby("年")["调仓日"].nunique(), axis=0).round(2)
            t.columns = pd.MultiIndex.from_product([[tag], t.columns])
            merged = t if merged is None else merged.join(t, how="outer").fillna(0.0)
        cols = sorted(merged.columns.get_level_values(1).unique())
        show = [c for c in ("北交所", "科创板", "沪市主板", "创业板", "深市主板") if c in cols] + \
               [c for c in cols if c not in ("北交所", "科创板", "沪市主板", "创业板", "深市主板")]
        print(f"\ntop{n}（单位：只/调仓日；adj|real 两列并排）：")
        print(merged[[(b, c) for c in show for b in ("adj", "real") if (b, c) in merged.columns]]
              .to_string())
    # 两口径的名单重合度：重合低 = 排名轴本身被 $factor 拉偏，而不是两边同解
    both = []
    for d0 in days:
        if cl.index.get_loc(d0) < 21:
            continue
        a5, _ = pick_hist(d0, 5)
        r5, _ = pick_hist(d0, 5, q_real)
        if len(a5) == 5 and len(r5) == 5:
            both.append(len(set(a5) & set(r5)) / 5.0)
    if both:
        print(f"\ntop5 两口径重合：中位 {np.median(both):.0%}、平均 {np.mean(both):.0%}"
              f"（{len(both)} 个调仓日）")
    for y, m0 in ((2024, 12), (2025, 6)):
        dd = [x for x in days if x.year == y and x.month == m0]
        if not dd:
            continue
        r5, _ = pick_hist(dd[0], 5, q_real)
        det = [(c, seg(c), f"{q.loc[dd[0], c]:.0f}", f"{q_real.loc[dd[0], c]:.0f}") for c in r5]
        print(f"  {dd[0].date()} real 口径 top5：{det}")

    # ---------- 7 尺度不变的写法（CV）：判定第 6 节那场分歧是行情之争还是量纲之争 ----------
    print("\n===== 7 把同一根轴写成与量纲无关的形式：CV = STD(Vol,20) / MEAN(Vol,20) =====")
    print("第 6 节两口径 top5 重合中位 0%，怀疑点是排序被「每票一个常数」($factor) 决定。\n"
          "CV 的分子分母同乘 k 不变 ⇒ **两口径必须给出同一份名单**，这是可以在数据上验的判据："
          "若 CV 两口径重合 100%，那 0% 就是量纲之争，不是「哪种安静更真」的行情之争。")
    q_cv = q / vol.rolling(20).mean().where(lambda x: x > 0)
    q_cv_real = q_real / mtx["volume_real"].rolling(20).mean().where(lambda x: x > 0)
    for tag, qq in (("adj", q_cv), ("real", q_cv_real)):
        c5, _ = pick_hist(cl.index[-1], 5, qq)
        print(f"  {cl.index[-1].date()} CV（{tag} 手数口径）top5：{c5}")
    ca = set(pick_hist(cl.index[-1], 5, q_cv)[0])
    cb = set(pick_hist(cl.index[-1], 5, q_cv_real)[0])
    print(f"  → CV 两口径重合 {len(ca & cb)}/5（判据：应为 5/5）")

    cv_rows, cv_seg, cv_ovl = [], [], []
    for d0 in days:
        if cl.index.get_loc(d0) < 21:
            continue
        c5, pn = pick_hist(d0, 5, q_cv)
        a5, _ = pick_hist(d0, 5)
        if len(c5) < 5:
            continue
        r = stat(c5, ind, 5)
        ps, pt = pair_share(c5, ind)
        r.update(年=d0.year, 调仓日=d0.date(), 池=pn, 同行业配对=ps, 配对数=pt)
        cv_rows.append(r)
        for c in c5:
            cv_seg.append({"年": d0.year, "调仓日": d0.date(), "段": seg(c)})
        cv_ovl.append(len(set(c5) & set(a5)) / 5.0)
    cvh = pd.DataFrame(cv_rows)
    print(f"\nCV 口径 top5 vs 生产(adj) 口径 top5 重合：中位 {np.median(cv_ovl):.0%}、"
          f"平均 {np.mean(cv_ovl):.0%}（{len(cv_ovl)} 个调仓日）")
    print("\n按年：CV 口径 top5 的最大实体行业占已知中位")
    print((cvh.pivot_table(index="年", values="最大占比", aggfunc="median") * 100).round(0)
          .to_string(float_format=lambda x: "{:5.0f}%".format(x)))
    print(f"\nCV 口径全抽样：最大行业占已知 中位 {cvh['最大占比'].median():.0%}"
          f"（p90 {cvh['最大占比'].quantile(.9):.0%}）、已知 中位 {cvh['已知数'].median():.1f}/5、"
          f"同行业配对 中位 {cvh['同行业配对'].median():.0%}")
    f5 = cvh[cvh["已知数"] == 5]
    print(f"           5 只全同行业 {(f5['最大占比'] >= 0.999).sum()}/{len(f5)} 天；"
          f"对照 adj 口径同一量 {int((h[(h['名单'] == 5) & (h['已知数'] == 5)]['最大占比'] >= 0.999).sum())}"
          f"/{int(((h['名单'] == 5) & (h['已知数'] == 5)).sum())} 天")
    cs = pd.DataFrame(cv_seg)
    t = cs.groupby(["年", "段"]).size().unstack(fill_value=0)
    t = t.div(cs.groupby("年")["调仓日"].nunique(), axis=0).round(2)
    print("\nCV 口径 top5 段构成（只/调仓日；对照第 4 节的 adj 表看北交所漂移还剩多少）：")
    print(t.to_string())

    # ---------- 8 量纲敏感性随档位怎么变：5 只这一档是不是最脆的那一档 ----------
    print("\n===== 8 重合度随档位（142 个调仓日中位；重合 = 两份名单交集/档位）=====")
    print("第 6/7 节两个 2% 都只量了 top5。若档位放大到 50 就基本重合，那「量纲敏感」是"
          "**榜首那一小截的性质**（恰好是 50→5 要动的那一段）；若 50 也只对上两三成，"
          "那整根轴的排序都不稳，收窄名单等于把不确定性按比例放大。")
    ovl = {n: {"real": [], "cv": []} for n in NS}
    for d0 in days:
        if cl.index.get_loc(d0) < 21:
            continue
        base = pick_hist(d0, max(NS))[0]
        if len(base) < max(NS):
            continue
        for n in NS:
            a5 = set(base[:n])
            ovl[n]["real"].append(len(a5 & set(pick_hist(d0, n, q_real)[0])) / n)
            ovl[n]["cv"].append(len(a5 & set(pick_hist(d0, n, q_cv)[0])) / n)
    print(f"  {'档位':<6}{'vs 真手数口径':>14}{'vs CV 口径':>14}")
    for n in NS:
        print(f"  top{n:<4d}{np.median(ovl[n]['real']):>13.0%}{np.median(ovl[n]['cv']):>14.0%}")

    # 今日 CV 前 5 名在生产名单（adj 口径 50 只）里的排位：差距具体落在哪
    cv5, _ = pick_hist(cl.index[-1], 5, q_cv)
    pr = buy.set_index("code")["rank"].to_dict()
    nm = buy.set_index("code")["name"].to_dict()
    print(f"\n今日（{d_last}）CV 口径 top5 在生产 50 只名单里的名次："
          f"{[(c, pr.get(c, '不在名单'), str(nm.get(c, ''))) for c in cv5]}")
    print(f"  对照：生产 top5 = {codes_all[:5]}")
    print(f"  生产名单里 CV 轴的分位跨度：CV 值 最小 {q_cv.loc[cl.index[-1], codes_all].min():.3f}"
          f"、最大 {q_cv.loc[cl.index[-1], codes_all].max():.3f}、"
          f"生产前 5 名 {q_cv.loc[cl.index[-1], codes_all[:5]].median():.3f}（中位）")

    # ---------- 9 一根「波动」轴为什么会随量纲整段翻？因为它排的是水平不是波动 ----------
    # 猜想：STD(Vol,20) 的截面排序被序列**水平**主导（大致 ρ≈1），「安静度」这个名字
    # 描述的其实是「成交额小」。若成立，第 8 节那些 0% 就毫不奇怪 —— 任何每票一个
    # 常数的变换都会把按水平排的名单整段重排，而 CV（去掉水平）自然与它无关。
    print("\n===== 9 这根轴的排序被什么主导：截面 Spearman（池内全票，闸门后）=====")
    lvl20 = vol.rolling(20).mean()                      # 序列水平（复权手手数口径）
    for d0 in (cl.index[-1], cl.index[-250], cl.index[-1250]):
        prev = cl.index[cl.index.get_loc(d0) - 1]
        chg = guard_ret(cl.loc[d0] / cl.loc[prev] - 1.0, raw.loc[d0] / raw.loc[prev] - 1.0)
        ok = (gate.loc[d0] & ~chg.fillna(False).ge(ASHARE_PORT_LIMIT_UP)
              & q.loc[d0].notna() & lvl20.loc[d0].notna())
        a = q.loc[d0].where(ok).astype("float64")
        lvl = lvl20.loc[d0].where(ok).astype("float64")
        amt = mtx["amount20"].loc[d0].where(ok).astype("float64")
        cvr = q_cv.loc[d0].where(ok).astype("float64")
        n = int(ok.sum())
        print(f"  {d0.date()}（池 {n} 只）：STD(Vol,20) vs 水平 MEAN(Vol,20) ρ="
              f"{a.corr(lvl, method='spearman'):.3f}、vs 成交额水平 amount20 ρ="
              f"{a.corr(amt, method='spearman'):.3f}；CV vs 水平 ρ="
              f"{cvr.corr(lvl, method='spearman'):.3f}、CV vs STD ρ="
              f"{cvr.corr(a, method='spearman'):.3f}")
    # ρ 高不等于「名单就是按水平排的」——直接把两口径选出的 5 只对上：
    # 若重合接近 100%，那「安静度」这个名字下其实站着「成交额最小的 5 只」，
    # 波动那一项几乎不贡献排序，而水平正是 $factor 可以随意缩放的那个量。
    ov_lvl = []
    for d0 in days:
        if cl.index.get_loc(d0) < 21:
            continue
        a5, _ = pick_hist(d0, 5)
        l5, _ = pick_hist(d0, 5, lvl20)
        if len(a5) == 5 and len(l5) == 5:
            ov_lvl.append(len(set(a5) & set(l5)) / 5.0)
    print(f"  → STD 口径 top5 与「纯水平 MEAN(Vol,20) 口径」top5 重合：中位 "
          f"{np.median(ov_lvl):.0%}、平均 {np.mean(ov_lvl):.0%}（{len(ov_lvl)} 个调仓日）"
          f"；对照 CV 口径与 STD 的重合 {np.median(cv_ovl):.0%}")
