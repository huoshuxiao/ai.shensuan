# -*- coding: utf-8 -*-
"""第二步（60 场历史回放）：让 9b 从**当年真实那张 50 只观察名单**里挑 5 只，跟生产确定性名单并排出账。

为什么要这一场：等 60 个真实场次要三个月，而裁决「要不要让 LLM 参与出名单」等不了那么久。
历史回放能把「它挑的 5 只后来涨得比生产那 5 只多还是少」一次量完。

三条腿（**同一批信号日、同一套时序、同一份费率**，只差谁挑的这 5 只）：
  `det`    生产腿：`screen_on_date` 现算当日 50 只 → `order_candidates`（同板块≤3、同行业≤1、
            未知豁免、满仓给权）。这是拿去实盘的那一份，也是对照组。
  `raw`    模型裸答案：把它给的 5 个代码原样收下（不在池内的丢掉），每席 1/实际只数。
            ⇒ 这条读的是「只靠提示词里的约束，它自己能不能守规矩」。
  `gated`  模型提名 + 事后校验层：把它的**排序**当成一张只含 5 行的观察名单，再走一遍
            `order_candidates` ⇒ 这条读的是「接进生产要加的那道闸，修完之后还剩多少」。
            （09-27 四张账单已实测：提示词里写约束 = 6/6 次被破，所以裸答案那条腿注定要配闸。）

时序与成本：与 `stock/v1/temp/dd_scale_0926.py` 那台秤同构 —— 信号日 i → 建仓日 j=i+1（开盘买）
→ 持仓段 [j+1, j+1+hold)，成本记在建仓日，单边 `ASHARE_PORT_COST_ONE_WAY`，
换手费按「与上一场名单的重合度」算（首场按全换手）。基准 = **当日可投域等权**在同一批
交易日上的均值，所以报出来的超额与 ㉗㊶ 那批读数同口径。

⚠️ 三条判读前提（写在跑之前）：
  1. 模型语料含 A 股历史 ⇒ **跑输是强证据，跑赢不能归因于本事**。有没有污染看第一步
     `probe_llm_memory_0927.py` 的读数，两张表要一起念。
  2. 回放没有当日收盘快照 ⇒ 生产那三道**执行层**闸（真成交、贴涨停不追、ST）三条腿都没跑；
     行业映射用今天这份表回溯 2015 年。所以量的是**信号层形状**，不是实盘账户。
  3. 只抽 60 场（570 场里按年分层）⇒ 单场噪声大，配对差才可信；均值年化是 60×5 天=300 个
     交易日摊出来的，不是 11 年。

用法：
    /usr/bin/python3.10 stock/v1/temp/backtest_llm_picks_0927.py --detect-check   # 只验尺子
    N_SESSIONS=60 /usr/bin/python3.10 stock/v1/temp/backtest_llm_picks_0927.py
产物全部落在 `stock/v1/temp/tmp_llm_evidence_0927/`（含 LLM 回答缓存，续跑不重烧 CPU），
**不碰 `stock/v1/data/` 任何归档**。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: F402,E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (ASHARE_BUY_MIN_HITS, ASHARE_BUY_TOP_N,  # noqa: E402
                    ASHARE_ORDER_MAX_PER_BOARD, ASHARE_ORDER_MAX_PER_INDUSTRY,
                    ASHARE_ORDER_TOP_N, ASHARE_PORT_COST_ONE_WAY, ASHARE_PORT_HOLD,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED, ASHARE_PORT_START,
                    ASHARE_SCREEN_QUANTILE, ASHARE_TRADABLE_GATE, LLM_MODEL)
from strategy.ashare_screen import (BUY_EXPR, BUY_EXTRA_EXPRS, RULE_EXPRS,
                                    board_of, build_matrices, factor_matrices, gate_desc,
                                    load_industry_map, load_panel, order_candidates,
                                    screen_on_date)
import llm_evidence_common as C  # noqa: E402

OUT = os.path.join(ROOT, "stock/v1/temp/tmp_llm_evidence_0927")
# 产物按模型名分档：换了 LLM_MODEL 就要重新问一遍，两套回答绝不并进同一张表
OUT_M = os.path.join(OUT, LLM_MODEL.replace(":", "_"))
os.makedirs(OUT_M, exist_ok=True)

TOP_N = ASHARE_ORDER_TOP_N
HOLD = ASHARE_PORT_HOLD
SEED = int(os.environ.get("STOCK_LLM_BT_SEED", "20260927"))
N_SESSIONS = int(os.environ.get("N_SESSIONS", "60"))
ARMS = ("det", "raw", "gated")
SESSIONS_PER_YEAR = 252.0 / HOLD      # 570 场/11 年那把尺子的等价数：50.4 场/年


def session_gross(codes, pos, days, ret):
    """该场篮子在持仓段 [pos+2, pos+2+HOLD) 上的日均收益（每席 1/实际只数，满仓给权）

    有效建仓价是「信号日次日开盘」（`ret` 是开盘接力收益，第一段就把这价定住了）；
    这里只取那几天的**日频算术均值**，与基准同一取法，所以两条腿可直接相减。
    """
    lo, hi = pos + 2, min(pos + 2 + HOLD, len(days))
    if not codes or hi <= lo:
        return float("nan")
    leg = ret.iloc[lo:hi][[c for c in codes]].to_numpy(dtype="float64")
    return float(leg.mean())


def session_cost(prev, cur):
    """换手费：与上一场重合越多付越少；首场按全换手（与 dd_scale 同一笔约定）"""
    if prev is None:
        return 2 * ASHARE_PORT_COST_ONE_WAY
    ov = len(set(prev) & set(cur)) / TOP_N
    return 2 * ASHARE_PORT_COST_ONE_WAY * (1.0 - ov)


def bench_gross(pos, days, ret, universe):
    """同一段交易日的**域等权**基准：逐日取「当天可投域均值」再按天平均

    与 dd_scale/㉗ 那批读数同一取法（`ret.where(universe).mean(axis=1)`），所以这里的
    超额和归档里的超额是一个口径，不用重算基线。
    """
    lo, hi = pos + 2, min(pos + 2 + HOLD, len(days))
    seg = ret.iloc[lo:hi].where(universe.iloc[lo:hi])
    return float(seg.mean(axis=1).mean())


# ===== 记账尺子自检：不花 CPU，但必须在调模型之前过 =====
def detect_check(mtx, days, universe):
    ret, u = mtx["ret_open0"], universe
    pos = len(days) - HOLD - 5          # 留够持仓段
    cols = [c for c in ret.columns if bool(u.iloc[pos + 2:pos + 2 + HOLD][c].notna().all())]
    per = {c: session_gross([c], pos, days, ret) for c in cols}
    best5 = sorted(cols, key=lambda c: -per[c])[:TOP_N]
    worst5 = sorted(cols, key=lambda c: per[c])[:TOP_N]
    g_best, g_worst = session_gross(best5, pos, days, ret), session_gross(worst5, pos, days, ret)
    cases = [
        ("单票毛收益 = 该票那几天均值（可加性）",
         abs(per[best5[0]] - session_gross([best5[0]], pos, days, ret)) < 1e-15),
        ("5 票等权 = 各票均值再平均（满仓给权，不留现金）",
         abs(g_best - np.mean([per[c] for c in best5])) < 1e-12),
        ("最好的 5 只 > 最差的 5 只（判分方向没反）", g_best > g_worst),
        ("空篮子不硬报 0（必须 NaN，否则会凭空造一场平局）",
         np.isnan(session_gross([], pos, days, ret))),
        ("无换手 ⇒ 费用 = 0", abs(session_cost(best5, best5)) < 1e-15),
        ("整篮换人 ⇒ 费用 = 双边（比无换手贵）",
         session_cost(worst5, best5) > session_cost(best5, best5) + 1e-9),
        ("首场 ⇒ 按全换手计费", abs(session_cost(None, best5) - 2 * ASHARE_PORT_COST_ONE_WAY) < 1e-15),
        ("基准腿落在两条腿之间（域等权不该比最差 5 只还差）",
         g_worst <= bench_gross(pos, days, ret, u) <= g_best),
    ]
    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(f"[记账自检] {n}: {'✅' if ok else '❌'}", flush=True)
    if bad:
        print(f"❌ 记账尺子是脏的 ⇒ 不去花 CPU：{bad}", flush=True)
        return 1
    print(f"✅ 记账自检 {len(cases)}/{len(cases)} 通过（含 3 个「必须读低/读非零」的负对照）",
          flush=True)
    return 0


def check_compliance(codes, rows):
    """照生产下单层那两道限幅复核模型裸答案（阈值从 config import，不抄第二份数字）"""
    in_pool = set(rows.index)
    outside = [c for c in codes if c not in in_pool]
    known = [c for c in codes if c in in_pool]
    boards = [rows.at[c, "板块"] for c in known]
    inds = [rows.at[c, "行业"] for c in known]
    board_over = sorted({b for b in boards if boards.count(b) > ASHARE_ORDER_MAX_PER_BOARD})
    ind_dup = sorted({g for g in inds if g != C.INDUSTRY_UNKNOWN
                      and inds.count(g) > ASHARE_ORDER_MAX_PER_INDUSTRY})
    return outside, board_over, ind_dup


def pool_text(buy, names, raw_price, s):
    """喂给模型的池子：名次|代码|简称|板块|行业|当日收盘价。刻意只给人眼看板的那几列"""
    lines = ["格式：名次|代码|简称|板块|行业|收盘价（元）"]
    for code, r in buy.iterrows():
        nm = names.get(code, "—")
        cl = raw_price.loc[s, code]
        lines.append(f"{int(r['rank'])}|{code}|{nm}|{r['板块']}|{r['行业']}|"
                     f"{'' if pd.isna(cl) else f'{cl:.2f}'}")
    return "\n".join(lines)


def stratified_sample(grid, want, rng):
    """按年分层抽样：每年至少 2 场，其余按该年场数占比摊，总数凑到 `want`。

    为什么不用简单随机：2015/2016 那两年占了网格两成多，纯随机会抽出一个「以牛市为主」
    的 60 场，读数就变成那一年的行情而非名单形状。
    """
    by_year = {}
    for s in grid:
        by_year.setdefault(s.year, []).append(s)
    total = len(grid)
    alloc = {y: max(2, int(round(want * len(v) / total))) for y, v in by_year.items()}
    years = sorted(alloc)
    while sum(alloc.values()) > want:                    # 超了就从最大层削
        y = max(years, key=lambda k: (alloc[k], k))
        if alloc[y] <= 2:
            break
        alloc[y] -= 1
    while sum(alloc.values()) < want:                    # 不够就往最大的层补
        y = max(years, key=lambda k: (len(by_year[k]) - alloc[k], k))
        alloc[y] += 1
    out = []
    for y in years:
        take = min(alloc[y], len(by_year[y]))
        # 抽索引再取原 Timestamp：拿字符串绕一圈会把日内时分秒丢掉，回头查 pos_of 就错位
        idx = rng.choice(len(by_year[y]), take, replace=False)
        out += [by_year[y][int(j)] for j in idx]
    return sorted(set(out)), {y: alloc[y] for y in years}


def main():
    t0 = time.time()
    print(f"[口径] 涨停闸={ASHARE_TRADABLE_GATE}（{gate_desc()}）｜观察名单 {ASHARE_BUY_TOP_N} 只"
          f"｜下单 {TOP_N} 席·同行业≤{ASHARE_ORDER_MAX_PER_INDUSTRY}"
          f"·同板块≤{ASHARE_ORDER_MAX_PER_BOARD}｜满仓给权｜hold {HOLD} 日"
          f"｜单边费率 {ASHARE_PORT_COST_ONE_WAY}｜共识闸≥{ASHARE_BUY_MIN_HITS}"
          f"｜域分位 {ASHARE_SCREEN_QUANTILE}｜抽 {N_SESSIONS} 场｜seed={SEED}", flush=True)
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    exprs = sorted(set(RULE_EXPRS) | {BUY_EXPR} | set(BUY_EXTRA_EXPRS))
    fms = factor_matrices(exprs, mtx)
    # 与 dd_scale 同一手：多留 HOLD 天历史喂「昨收」，网格从 `off` 那一格起算 ⇒ 信号日
    # 集合与生产归档**逐字同一批**，只是这里从中抽 60 场。
    cut = int(np.searchsorted(mtx["ret_open"].index, pd.Timestamp(ASHARE_PORT_START)))
    start = max(cut - ASHARE_PORT_HOLD, 1)
    off = cut - start
    days_all = mtx["ret_open"].index
    mtx = {k: v.loc[days_all[start:]] for k, v in mtx.items()}
    fms = {e: m.loc[m.index[start:]] for e, m in fms.items()}
    days = mtx["ret_open"].index
    universe = ((mtx["listed_days"] >= ASHARE_PORT_MIN_LISTED)
                & (mtx["amount20"] >= ASHARE_PORT_MIN_AMOUNT))
    gate = fms[RULE_EXPRS[0]]
    ind, ind_meta = load_industry_map()
    ind_map = ind.to_dict() if ind_meta.get("loaded") else {}
    names = C.load_names()
    print(f"[准备] {time.time() - t0:.0f}s｜{len(days)} 个交易日｜行业表 "
          f"loaded={ind_meta.get('loaded')} n={ind_meta.get('n')}｜简称 {len(names)} 条",
          flush=True)
    if detect_check(mtx, days, universe) != 0:
        return 1
    if "--detect-check" in sys.argv:
        print("（--detect-check：只验尺子，没调模型）", flush=True)
        return 0

    grid = [days[i] for i in range(off, len(days) - HOLD - 1, HOLD)]
    want = min(N_SESSIONS, len(grid))
    picked_dates, alloc = stratified_sample(grid, want, np.random.default_rng(SEED))
    pos_of = {d: i for i, d in enumerate(days)}
    print(f"[抽样] 网格 {len(grid)} 场 → 抽 {len(picked_dates)} 场｜分年配额 "
          f"{ {y: alloc[y] for y in sorted(alloc)} }｜年数覆盖 "
          f"{len({pd.Timestamp(x).year for x in picked_dates})}", flush=True)

    # 缓存文件名带模型名：换模型后同一把 key（日期）必须**不许**命中旧模型的回答，
    # 否则这张表就变成两个模型的混合读数，而三臂账单看起来和单模型一模一样。
    cache = C.Cache(os.path.join(OUT_M, "llm_cache_replay.jsonl"))
    cl = C.client()
    rows_out, arm_picks = [], {a: {} for a in ARMS}
    t_llm = time.time()
    for k, s in enumerate(picked_dates):
        tag = s.strftime("%Y-%m-%d")
        i = pos_of[s]
        d1 = days[i + 1]
        r = screen_on_date(s, d1, mtx, fms, gate, ASHARE_SCREEN_QUANTILE,
                           top_n=ASHARE_BUY_TOP_N, st_codes=None,
                           buy_min_hits=ASHARE_BUY_MIN_HITS)
        buy = r["buy"]
        det, det_stats = order_candidates(buy, ind_map, TOP_N,
                                          ASHARE_ORDER_MAX_PER_INDUSTRY,
                                          ASHARE_ORDER_MAX_PER_BOARD)
        det_codes = list(det.index)
        rows = pd.DataFrame({"板块": [board_of(c) for c in buy.index],
                             "行业": [str(ind_map.get(c, "")).strip() or C.INDUSTRY_UNKNOWN
                                     for c in buy.index]}, index=list(buy.index))
        rec = cache.get(tag)
        if rec is None:
            user = (f"场次 {s.strftime('%Y%m%d')}（{tag} 收盘截面，"
                    f"次日开盘买入，持有 {HOLD} 个交易日）。观察名单 {len(buy)} 只：\n"
                    + pool_text(buy.assign(行业=rows["行业"]), names, mtx["raw_price"], s)
                    + f"\n\n请挑 {TOP_N} 只，按你的优先级从高到低排列。")
            rec = C.ask_json(cl, C.SYSTEM_ORDER, user, tag)
            # 只缓存**成功**的回答：把抛错也落盘的话，续跑会永远绕过这一场，
            # 60 场里那几次网络抖动就变成永久缺样本（缓存的意义是省 CPU，不是存档失败）
            if "错误" not in rec:
                cache.put(tag, rec)
        llm_codes = list(rec.get("picks", []))
        outside, board_over, ind_dup = check_compliance(llm_codes, rows)
        raw_codes = []
        for c in llm_codes:                       # 去重 + 丢池外，保持它给的优先级
            if c in set(buy.index) and c not in raw_codes:
                raw_codes.append(c)
        raw_codes = raw_codes[:TOP_N]
        if raw_codes:
            llm_buy = pd.DataFrame(
                {"rank": np.arange(1, len(raw_codes) + 1),
                 "板块": [board_of(c) for c in raw_codes]},
                index=pd.Index(raw_codes, name="code"))
            gated, _gs = order_candidates(llm_buy, ind_map, TOP_N,
                                          ASHARE_ORDER_MAX_PER_INDUSTRY,
                                          ASHARE_ORDER_MAX_PER_BOARD)
            gated_codes = list(gated.index)
        else:
            gated_codes = []
        for a, cs in zip(ARMS, (det_codes, raw_codes, gated_codes)):
            arm_picks[a][s] = cs
        rows_out.append({
            "信号日": s, "pos": i, "名单只数": len(buy),
            "det_n": len(det_codes), "raw_n": len(raw_codes), "gated_n": len(gated_codes),
            "det凑满": len(det_codes) == TOP_N,
            "det席位": "|".join(det_codes), "raw席位": "|".join(raw_codes),
            "gated席位": "|".join(gated_codes),
            "模型秒": rec.get("秒"), "模型tk": rec.get("completion_tokens"),
            "模型错误": rec.get("错误", ""),
            "池外代码": len(outside), "破板块": len(board_over), "破行业": len(ind_dup),
            "det_obs_rank最深": int(det["obs_rank"].max()) if len(det) else -1,
            "与det重合": len(set(raw_codes) & set(det_codes)),
        })
        if (k + 1) % 10 == 0 or k + 1 == len(picked_dates):
            print(f"  [{k + 1}/{len(picked_dates)}] {tag} 模型 {rec.get('秒')}s "
                  f"池外{len(outside)} 破闸(板块{len(board_over)}/行业{len(ind_dup)}) "
                  f"重合{len(set(raw_codes) & set(det_codes))}/5｜累计 "
                  f"{time.time() - t_llm:.0f}s，缓存命中 {cache.hits}", flush=True)

    df = pd.DataFrame(rows_out)
    df.to_csv(os.path.join(OUT_M, "replay_sessions.csv"), index=False)
    ok = df[(df["det_n"] > 0)]
    print(f"[出账] {time.time() - t_llm:.0f}s｜{len(df)} 场，其中模型有可用答案 "
          f"{int((df['raw_n'] > 0).sum())} 场", flush=True)

    # ---- 逐场净值（三臂 + 基准），与上面 detect_check 同一套函数 ----
    ret = mtx["ret_open0"]
    prev = {a: None for a in ARMS}
    legs = []
    for _, row in ok.iterrows():
        s, i = row["信号日"], int(row["pos"])
        b = bench_gross(i, days, ret, universe)
        rec = {"信号日": s, "年": s.year, "bench": b}
        for a in ARMS:
            cs = arm_picks[a][s]
            g = session_gross(cs, i, days, ret)
            c = session_cost(prev[a], cs) if cs else float("nan")
            prev[a] = cs if cs else prev[a]
            rec[f"{a}_毛"], rec[f"{a}_费"], rec[f"{a}_净"] = g, c, g - c
            # 超额一律是**净**口径（臂扣完费再减基准），与 dd_scale/㉗㊶ 同一把尺子：
            # `ex = (port_net − bench).mean()×年化`。基准本身不收费，所以这一列里
            # 费用是被算进"代价"的，不要拿它去和毛收益并排读。
            rec[f"{a}_超额"] = g - c - b
        legs.append(rec)
    L = pd.DataFrame(legs)
    L.to_csv(os.path.join(OUT_M, "replay_nav.csv"), index=False)
    if len(L) < 3:
        print("❌ 有效场次不足 3 ⇒ 不出账单", flush=True)
        return 1

    def nav_dd(series):
        nav = (1.0 + series.dropna()).cumprod()
        return float((nav / nav.cummax() - 1.0).min())

    summ = []
    for a in ARMS:
        net, ex = L[f"{a}_净"], L[f"{a}_超额"]
        summ.append({
            "腿": a, "场次": int(net.notna().sum()),
            "场均净收益%": net.mean() * 100,
            "中位%": net.median() * 100,
            f"年化(×{SESSIONS_PER_YEAR:.0f}场/年)%": net.mean() * SESSIONS_PER_YEAR * 100,
            # ⚠️ 这一列是**毛**口径（臂侧毛收益 − 域等权），与配对差那几列的净口径不同尺：
            # 域等权基准本身不收费，把臂侧费用也扣进去就成了「净 vs 毛」的混账（用户逐字挑过
            # 这种「对照表每行不是同一口径」）。两列各说各话，列名里写清楚。
            "净超额年化%(臂净−域等权)": ex.mean() * SESSIONS_PER_YEAR * 100,
            "净超额t值": ex.mean() / (ex.std(ddof=1) / np.sqrt(ex.notna().sum()))
            if ex.std(ddof=1) > 0 else np.nan,
            "跑赢基准场次(净)": f"{int((ex > 0).sum())}/{int(ex.notna().sum())}",
            "最深单场%": net.min() * 100,
            "拼接NAV最大回撤%": nav_dd(net) * 100,
            "费均值bp": L[f"{a}_费"].mean() * 1e4,
        })
    S = pd.DataFrame(summ)
    for a in ("raw", "gated"):
        d = (L[f"{a}_净"] - L["det_净"]).dropna()
        S[f"{a}−det 场均差pp"] = d.mean() * 100
        S[f"{a}−det t"] = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std(ddof=1) > 0 else np.nan
        S[f"{a}−det 胜率"] = f"{int((d > 0).sum())}/{len(d)}"
    S.to_csv(os.path.join(OUT_M, "replay_summary.csv"), index=False)
    print("\n===== 三臂账单 =====", flush=True)
    print(S.to_string(index=False), flush=True)

    print("\n逐年场均净收益%（配对差，det 为对照）：", flush=True)
    yb = L.groupby("年").apply(lambda g: pd.Series({
        "场数": len(g), "det": g["det_净"].mean() * 100,
        "raw": g["raw_净"].mean() * 100, "gated": g["gated_净"].mean() * 100,
        "raw−det": (g["raw_净"] - g["det_净"]).mean() * 100,
        "gated−det": (g["gated_净"] - g["det_净"]).mean() * 100,
        "bench": g["bench"].mean() * 100}), include_groups=False).reset_index()
    yb.to_csv(os.path.join(OUT_M, "replay_yearly.csv"), index=False)
    print(yb.round(2).to_string(index=False), flush=True)

    print("\n===== 合规与换血 =====", flush=True)
    print(f"池外代码：{int(df['池外代码'].sum())} 个 / {int(df['raw_n'].sum() + 0)} 席次"
          f"（{len(df)} 场 × {TOP_N} 槽）⇒ 池外率 "
          f"{df['池外代码'].sum() / (len(df) * TOP_N):.2%}", flush=True)
    print(f"破硬闸场次：板块 {int((df['破板块'] > 0).sum())}/{len(df)}、"
          f"行业 {int((df['破行业'] > 0).sum())}/{len(df)}"
          f"（提示词里写了约束，事后校验层就是为它准备的）", flush=True)
    print(f"模型给足 {TOP_N} 只的场次：{int((df['raw_n'] == TOP_N).sum())}/{len(df)}"
          f"｜校验层凑满：{int((df['gated_n'] == TOP_N).sum())}/{len(df)}"
          f"｜生产凑满：{int(df['det凑满'].sum())}/{len(df)}", flush=True)
    print(f"与生产名单平均重合 {df['与det重合'].mean():.2f}/{TOP_N} 席 ⇒ 换血 "
          f"{TOP_N - df['与det重合'].mean():.2f} 席/场", flush=True)
    print(f"生产腿最深扫到观察名单第 {int(df['det_obs_rank最深'].max())} 名"
          f"（模型若给出更靠后的名次，说明它在读生产不看的那半张表）", flush=True)
    print(f"单次调用墙钟：中位 {df['模型秒'].median():.1f}s、"
          f"最长 {df['模型秒'].max():.1f}s；失败 {int((df['模型错误'] != '').sum())} 场",
          flush=True)
    from math import comb

    def paired(a):
        """配对读数：t 值（全部场）+ 符号检验（**剔掉严格为 0 的场**，否则分母虚高）

        尾部方向踩过一次雷：观察值低于半数时，"小尾巴"要按 **P(X ≤ 小尾)** 累加，
        不是从它往上累加 —— 写反了会让 20/57 这种明显不利的读数报出 p=1.000。
        """
        d = (L[f"{a}_净"] - L["det_净"]).dropna()
        nt = int((d != 0).sum())
        wins = int((d > 0).sum())
        tail = min(wins, nt - wins)
        p_one = sum(comb(nt, j) * 0.5 ** nt for j in range(0, tail + 1)) if nt else 1.0
        p = min(1.0, 2 * p_one)
        t = float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))) if d.std(ddof=1) > 0 else float("nan")
        return d, wins, nt, p, t

    for a in ("raw", "gated"):
        d, wins, nt, p, t = paired(a)
        print(f"配对 {a} vs det：场均差 {d.mean() * 100:+.2f}pp（年化 {d.mean() * SESSIONS_PER_YEAR * 100:+.1f}pp）"
              f"｜{a} 赢 {wins}/{nt} 场（剔并列后）、符号检验双侧 p={p:.3f}｜t={t:.2f}",
              flush=True)
    print("\n判读（跑之前定死）：① raw 跑输 det ⇒ 直接判死「LLM 自己出名单」这条路，"
          "这一条不受语料污染影响（背过也不会输）；② raw 跑赢 ⇒ 只当上界读，必须配第一步"
          "记忆探针的读数一起念；③ gated − det 才是「接了事后校验层之后」的真实代价，"
          "而 gated 与 det 的名单差已由换血率单独列出", flush=True)
    print(f"（产物：replay_sessions.csv / replay_nav.csv / replay_summary.csv / "
          f"replay_yearly.csv @ {OUT_M}）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
