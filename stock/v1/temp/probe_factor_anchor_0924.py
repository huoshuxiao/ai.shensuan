# -*- coding: utf-8 -*-
"""复权口径锁定探针（09-24，日更 append 的前提判据）

问题：stock 线的 qlib bin 来自社区包（chenditc 格式，仓库里没有生成它的代码）。
要「收盘后把当日日线 append 进去」，必须先回答一个问题：面板的 `$factor` 是按什么
锚定的？因为 append 规则只有在**历史 f 与新一天 f 同一套口径**时才成立，否则每追加
一天，全历史的 复权价/收益率 都会跟着挪 —— 那等于把所有已入库结论作废。

观测到的事实（09-23 已定案）：
    盘面价 raw = $close / $factor           逐票对回新浪不复权收盘，相对差 max 9.4e-08
    $volume  = 真实手数 / $factor           真成交额 = $close × $volume × 100
所以 $factor 就是「复权价 ÷ 盘面价」这个比。剩下唯一没定的就是它的**锚**。

三条候选结论（脚本按 a→c 排他）：
    (a) $factor_t = k(票) × F_t，F_t = 外部累计复权因子（新浪 hfq/raw），k 逐票恒定
        ⇒ append 规则 = f_new = k·F_new（或等价的 f_prev×F_new/F_prev），历史一个字节都不用改
    (b) 同 (a) 但少数格子偏离 k·F ⇒ 那些格子是包内因子缺陷，**可以整列重算 k·F 覆盖**
        —— 这就是「把假跳变的账清掉」：那 90 个 RET_LIMIT 命中如果是这么来的，
        护栏就从「载荷护栏」降级成「兜底断言」
    (c) k 随时间漂移 ⇒ 包是按某个滚动基准归一化的，绝对锚不可用，只能走局部规则：
        交易所当日除权参考价（快照的「昨收」列）给 f_T = f_{T-1} × raw_{T-1}/昨收_T

判据设计成不依赖任何一方的复权定义：拿外部 F_t 当尺子，看面板 f_t 与它的比是不是常数。
"""
import socket
import sys
import time

socket.setdefaulttimeout(60)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import akshare as ak  # noqa: E402

PANEL = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/"
         "rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5")
START, END = "20081229", "20260922"
TOL = 1e-4              # k 的恒定容忍带（相对偏离）
# 09-24 00:58 那轮：18 只票 × 2 请求、重试只隔 1-2 秒，被新浪按「大量抓取封 IP」
# 打趴 —— 18/18 全在 akshare 内部 `data_df["date"]` 抛 KeyError（它拿到的是空 json）。
# 单票手工复测同一时刻就能取到，所以那是限流不是数据问题。样本砍到 8 只、票间隔开。
N_STRATA = 5            # 按 $factor 十分位分层抽样
N_GUARD = 3             # 假台阶（只有复权侧越界）命中最狠的几只
STOCK_SLEEP = 8.0       # 逐票之间的间隔（秒）

print("读面板…")
p = pd.read_hdf(PANEL)
p = p[[c for c in p.columns if c.startswith("$")]]
p.columns = [c.lstrip("$") for c in p.columns]
inst = np.array(sorted(p.index.get_level_values("instrument").unique()))
is_idx = np.array([(s[:2] == "SH" and s[2:].startswith("000"))
                   or (s[:2] == "SZ" and s[2:].startswith("399")) for s in inst])
pool = [s for s in inst[~is_idx]]
w = {c: p[c].unstack("instrument").reindex(columns=pool) for c in ("close", "factor", "open")}
del p
print(f"面板 {w['close'].shape[0]} 日 × {len(pool)} 票，末日 {w['close'].index[-1].date()}")

# ---- 假台阶清单：只有复权开盘收益越界、盘面开盘收益正常 ----
# fill_method=None 与生产口径一致（build_matrices 里就是它；pad 会把停牌缺口接起来，
# 换一批格子越界，09-23 记的 326 就是同一判据在生产口径下的数）
adj_ret = w["open"].pct_change(fill_method=None)
raw_open = (w["open"] / w["factor"])
raw_ret = raw_open.pct_change(fill_method=None)
fake = adj_ret.abs().gt(0.30) & raw_ret.abs().le(0.30)
n_fake = int(fake.values.sum())
print(f"[复算] |复权开盘收益|>30% 而盘面收益正常的格子：{n_fake} 个"
      f"（对账：09-23 记的 326 是**错数**，shell/count_fake_steps_0924.py 实测生产口径 90 个、"
      f"不切日期 92 个）")
by_board = pd.Series([c[:2] for c in fake.columns]).reindex(
    np.repeat(fake.columns, fake.shape[0]))
bd = fake.stack()[lambda s: s.values].reset_index()
bd["board"] = bd["instrument"].str[:2]
print("[复算] 按板块分布：" + "  ".join(
    f"{k} {v}" for k, v in bd["board"].value_counts().items()))
print("[复算] 按年份分布：" + "  ".join(
    f"{k} {v}" for k, v in pd.Series(pd.to_datetime(bd["datetime"]).dt.year)
    .value_counts().sort_index().items()))
stack = adj_ret.where(fake).stack().abs().sort_values(ascending=False)
print("[复算] 最狠 5 个：")
for (d, code), v in stack.head(5).items():
    print(f"    {code} {d.date()} 复权 {adj_ret.loc[d, code]:+.2%} / "
          f"盘面 {raw_ret.loc[d, code]:+.2%} / f 昨日 {w['factor'].shift(1).loc[d, code]:.4f} "
          f"今日 {w['factor'].loc[d, code]:.4f}")

# ---- 抽样：$factor 十分位分层 + 假台阶最狠的几只 ----
last_f = w["factor"].iloc[-1].dropna()
last_f = last_f[last_f.index.isin(pool)]
q = last_f.quantile(np.linspace(0, 1, N_STRATA + 1))
strata = [last_f[(last_f >= q.iloc[i]) & (last_f <= q.iloc[i + 1])].index.tolist()
          for i in range(N_STRATA)]
rng = np.random.default_rng(20260924)
sample = [strata[i][rng.integers(len(strata[i]))] for i in range(N_STRATA)]
# 假台阶样本要能拿到外部行情才对看得上：新浪/腾讯都没有北交所单票接口（09-23 实测
# 腾讯 KeyError: 'day'、新浪 BJ 6/6 失败），所以优先挑 SH/SZ 的那批，BJ 只在上面
# 的板块分布里报数，不进 k 对看
guard_picks = [c for c in stack.index.get_level_values(1).unique()
               if c[:2] in ("SH", "SZ") and c not in sample][:N_GUARD]
sample = list(dict.fromkeys(sample + guard_picks))
print(f"\n[样本] {len(sample)} 只：分层 {N_STRATA} + 假台阶 {len(guard_picks)}")
# 假台阶那批多是已退市票（末日 f 是 NaN），取值要回落到各自最后一个有效日
f_last_valid = w["factor"].apply(lambda s: s[s.notna()].iloc[-1] if s.notna().any() else np.nan)
print("       " + " ".join(f"{c}(f={f_last_valid[c]:.4f})" for c in sample[:12]) + " …")


def sina_symbol(code):
    return code[0].lower() + code[2:]


def fetch(code, tries=3):
    """raw 与 hfq 两份日线，索引为 date

    上一版把 `df["date"]` 放在 try 外面，于是 akshare 偶发返回「date 在索引上」的
    帧时，18 只票全以 KeyError: 'date' 失败却看不出是取数问题还是数据问题。
    这一版把整形挪进 try，并且两种形态都吃。
    """
    out = {}
    for adj, key in (("", "raw"), ("hfq", "hfq")):
        df = None
        for t in range(tries):
            try:
                df = ak.stock_zh_a_daily(symbol=sina_symbol(code), start_date=START,
                                         end_date=END, adjust=adj)
                if df is None or not len(df):
                    raise ValueError("空表")
                if "date" not in df.columns:
                    df = df.reset_index()
                if "date" not in df.columns:
                    raise ValueError(f"返回里没有 date 列：{list(df.columns)[:6]}")
                df["date"] = pd.to_datetime(df["date"])
                s = df.set_index("date")["close"].astype("float64")
                break
            except Exception as e:
                s = None
                if t == tries - 1:
                    print(f"    {code} {adj or 'raw'} 取不到: {type(e).__name__}: {e}")
                time.sleep(1.0 + t)
        if s is None:
            return None
        out[key] = s
    return out


rows = []
bad_cells = {}
print("\n[逐票] k = 面板$factor ÷ 外部累计因子(hfq/raw)，恒定则说明包是「外部因子 × 逐票常数」")
for i, code in enumerate(sample):
    if i:
        time.sleep(STOCK_SLEEP)
    got = fetch(code)
    if not got:
        continue
    days = got["raw"].index.intersection(got["hfq"].index)
    days = days.intersection(w["close"].index)
    days = days[days >= w["close"].index[0]]
    if len(days) < 200:
        print(f"    {code} 交集仅 {len(days)} 天，跳过")
        continue
    F = (got["hfq"].reindex(days) / got["raw"].reindex(days))
    f = w["factor"].loc[days, code]
    c = w["close"].loc[days, code]
    r = got["raw"].reindex(days)
    k = (f / F)
    kk = float(k.median())
    dev = (k / kk - 1.0).abs()
    price_dev = (c / (r * f) - 1.0).abs()          # 恒等式 close = raw×factor 是否成立
    n_bad = int((dev > TOL).sum())
    rows.append({"code": code, "days": len(days), "k": kk,
                 "k_dev_med": float(dev.median()), "k_dev_p99": float(dev.quantile(.99)),
                 "k_dev_max": float(dev.max()), "n_bad>TOL": n_bad,
                 "close恒等式_max": float(price_dev.max()),
                 "F_last": float(F.iloc[-1]), "f_last": float(f.iloc[-1]),
                 "first_bad": str(dev[dev > TOL].index[0].date()) if n_bad else ""})
    if n_bad:
        bad_cells[code] = dev[dev > TOL]
rep = pd.DataFrame(rows)
pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 20)
print(rep.round(6).to_string(index=False))

if not len(rep):
    print("\n[结论] 一只票都没对上看 ⇒ **无判据**，不许据此选 append 规则；先修取数再跑")
    sys.exit(2)
if len(rep):
    print(f"\n[判据] k 逐票恒定（dev_p99 ≤ {TOL:g}）的票数："
          f"{int((rep['k_dev_p99'] <= TOL).sum())}/{len(rep)}")
    print(f"       全样本 k 相对偏离中位 {rep['k_dev_med'].median():.2e}、"
          f"最坏票的 dev_max {rep['k_dev_max'].max():.2e}")
    print(f"       close = raw×factor 恒等式最大破坏 {rep['close恒等式_max'].max():.2e}")
    tot_bad = int(rep['n_bad>TOL'].sum())
    print(f"\n[结论候选] (a) 锚恒定可直接 append / (b) 有 {tot_bad} 个 (票,日) 的 f 偏离 k·F"
          f" ⇒ 那些格子是包内缺陷、可整列重算")

# 偏离格子与假台阶格子的重合度：这条决定「清账」是不是真能把 326 个跳变解释掉
if bad_cells:
    bad_set = {(d, c) for c, s in bad_cells.items() for d in s.index}
    hit_cells = {(d, c) for (d, c) in stack.index}
    inter = bad_set & hit_cells
    print(f"[重合] 因子偏离格子 {len(bad_set)} 个，其中落在假台阶清单里的 {len(inter)} 个；"
          f"假台阶总 {n_fake} 个（样本只覆盖 {len(sample)} 只票，别把覆盖率当 100%）")
    for (d, c) in sorted(inter)[:6]:
        print(f"    {c} {d.date()}: 面板 f {w['factor'].loc[d, c]:.6f} / "
              f"昨日 {w['factor'].shift(1).loc[d, c]:.6f} / 复权开盘收益 {adj_ret.loc[d, c]:+.2%}")
else:
    if len(rep):
        print("[重合] 没有任何格子偏离 k·F ⇒ 结论 (a)：锚是外部累计因子 × 逐票常数")
    else:
        print("[重合] 一只票都没取上 ⇒ 本轮对 k 恒定**没有结论**，先修取数")
        sys.exit(2)
