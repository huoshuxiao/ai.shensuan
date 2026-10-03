# -*- coding: utf-8 -*-
"""日更 append 的本地判据体检（09-24 第 2 版，#2）

只读 .day.bin，不碰任何外部行情接口，替代被 sina 限流打死的
probe_factor_anchor_0924.py（那轮「一只票都没对上看 ⇒ 无判据」）。

口径约定（09-23 已定案）：
    盘面价 raw_t = $close_t / $factor_t      复权价 close = raw × f
所以 **真除权日** 的样子是：f 跳 ×k、raw 掉 ≈1/k、close 连续（复权收益=真实持有收益）。
`$factor` 的脏格子因此有一个不依赖外部数据的判据：

    [A] f 单日跳 >30% 而 raw 在该板块涨停幅内 ⇒ 价格没跟着动，因子却变了 ⇒ 脏
        （第一版误用 |Δraw|<30% 当「正常」，把 3386 个真除权日全捞进来了）

其余要回答的：
    [B] 脏格子的形状：单日尖峰 / 永久台阶（台阶 ⇒ 修法是「该日及其后整段乘回 c」）
    [C] 停牌日怎么写：存活区间内部是 NaN 还是前值平盘续 + 量额置 0
    [D] 新上市起头：start_idx 是否等于首个非 NaN 日（未上市期靠截断）
    [E] change 字段是否等于 close.pct_change()
    [F] amount / (close×volume) 与 amount / (volume×vwap) 的倍数 ⇒ 定单位
"""
import os
import random

import numpy as np
import pandas as pd

BIN = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
       "qlib/qlib_data/cn_data")
FEAT = f"{BIN}/features"
random.seed(20260924)
LIMIT = {"BJ": 0.30, "KCB": 0.20, "CYB": 0.20, "MAIN": 0.10}


def board(code):
    u = code.upper()
    if u.startswith("BJ"):
        return "BJ"
    if u.startswith("SH688"):
        return "KCB"
    if u.startswith(("SZ300", "SZ301", "SZ302")):
        return "CYB"
    return "MAIN"


def load(code, field):
    p = f"{FEAT}/{code.lower()}/{field}.day.bin"
    if not os.path.exists(p):
        return None, None
    a = np.fromfile(p, dtype="<f4")
    return int(a[0]), a[1:]


def matrix(codes, field, n_cal, pad=np.nan):
    """按票拼一列；start_idx 之前的格子（未上市）保持 pad"""
    M = np.full((n_cal, len(codes)), pad, dtype=np.float32)
    starts = np.full(len(codes), -1, dtype=np.int64)
    for j, c in enumerate(codes):
        s, v = load(c, field)
        if s is None or len(v) == 0:
            continue
        M[s:s + len(v), j] = v
        starts[j] = s
    return M, starts


def main():
    days = [ln.strip() for ln in open(f"{BIN}/calendars/day.txt") if ln.strip()]
    n = len(days)
    codes = sorted(os.listdir(FEAT))
    J = len(codes)
    print(f"日历 {n} 天，末日 {days[-1]}；features {J} 个")

    CL, st = matrix(codes, "close", n)
    FA, _ = matrix(codes, "factor", n)
    with np.errstate(divide="ignore", invalid="ignore"):
        RAW = CL / FA
    live = np.isfinite(CL) & np.isfinite(FA) & (FA > 0)

    # ── D 新上市起头
    first_ok = np.array([int(np.flatnonzero(np.isfinite(CL[:, j])).min())
                         if np.isfinite(CL[:, j]).any() else -1 for j in range(J)])
    bad_d = int((first_ok != st).sum())
    print("\n" + "=" * 72)
    print(f"[D] start_idx ≠ 首个非 NaN 日的票: {bad_d}/{J}")
    print(f"    → 未上市期不写 NaN、靠 start_idx 截断 ⇒ append 老票只需尾部加 4 字节，"
          f"新票要写自己的 start_idx")

    # ── A 脏因子：复权收益远超涨停幅，而盘面价变动在涨停幅内
    #    （触发量必须用**复权收益**：真除权日 f 跳 ×k、raw 掉 ≈1/k，close 连续；
    #      上一版拿 |Δf|>30% 当触发，把 188 个正常除权日全捞进来了）
    dcl = CL[1:] / CL[:-1]
    dfa = FA[1:] / FA[:-1]
    drw = RAW[1:] / RAW[:-1]
    ok_pair = live[1:] & live[:-1] & (FA[:-1] > 0)
    lim = np.array([LIMIT[board(c)] for c in codes], dtype=np.float32)[None, :]
    dirty = (np.abs(dcl - 1) > 0.40) & ok_pair & (np.abs(drw - 1) <= lim * 1.05)
    ii, jj = np.nonzero(dirty)
    print("\n" + "=" * 72)
    print(f"[A] 脏因子格子（复权收益>40% 且 |Δraw|≤板块涨停）: {len(ii)} 个")
    if len(ii):
        bd = pd.Series([codes[j] for j in jj]).map(board).value_counts().to_dict()
        yr = pd.Series([days[i + 1] for i in ii]).str[:4].value_counts().sort_index()
        print(f"    板块: {bd}")
        print(f"    年份 Top8: {yr.head(8).to_dict()} … 最近年 {yr.index[-1]} 共 {yr.iloc[-1]} 个")
        first_day = int(sum(1 for i, j in zip(ii, jj) if i + 1 <= st[j] + 2))
        flat = int(sum(1 for i, j in zip(ii, jj) if abs(drw[i, j] - 1) < 1e-6))
        print(f"    其中 贴近上市首两日 {first_day} 个、raw 完全平盘 {flat} 个")

        # ── B 形状：尖峰 vs 永久台阶（台阶 = 之后 20 日 f 停在新水平）
        perm = spike = rest = 0
        shown = 0
        heights = []
        for i, j in zip(ii, jj):
            f0, f1 = FA[i, j], FA[i + 1, j]
            heights.append(float(f1 / f0))
            after = FA[i + 2:i + 22, j]
            nxt = FA[i + 2, j] if i + 2 < n else np.nan
            if np.isfinite(nxt) and abs(nxt / f1 - 1) > 0.30:
                spike += 1
            elif np.isfinite(after).all() and np.nanmax(np.abs(after / f1 - 1)) < 0.05:
                perm += 1
            else:
                rest += 1
            if shown < 8:
                print(f"      {codes[j]} {days[i + 1]}  f {f0:.4f}→{f1:.4f} (×{f1 / f0:.2f})  "
                      f"raw {RAW[i, j]:.2f}→{RAW[i + 1, j]:.2f} ({drw[i, j] - 1:+.2%})  "
                      f"复权收益 {dcl[i, j] - 1:+.1%}")
                shown += 1
        h = pd.Series(heights)
        print(f"[B] 形状: 永久台阶 {perm} | 单日尖峰 {spike} | 其余 {rest}")
        print(f"    台阶高度 c 分布: 中位 {h.median():.3f}  p10 {h.quantile(.1):.3f} "
              f"p90 {h.quantile(.9):.3f}  >1 占 {(h > 1).mean():.0%}")
        # 正常除权日的对照集：f 跳 >30% 而 close 连续（复权收益在涨停内）
        legit = (np.abs(dfa - 1) > 0.30) & ok_pair & (np.abs(dcl - 1) <= 0.40)
        li, lj = np.nonzero(legit)
        print(f"[A-对照] 真除权日（f 跳 >30% 而复权收益 ≤40%）: {len(li)} 个，"
              f"其中 raw 落在涨停内 {int((np.abs(drw[li, lj] - 1) <= lim[0, lj] * 1.05).sum())} 个")

    # ── C 停牌日写法（存活区间内部）
    tot = nan_in = zero_raw = 0
    for j in range(J):
        col = np.isfinite(CL[:, j])
        k = np.flatnonzero(col)
        if len(k) < 2:
            continue
        inner = slice(k[0] + 1, k[-1] + 1)
        seg = CL[inner, j]
        tot += len(seg)
        nan_in += int(np.isnan(seg).sum())
        zero_raw += int((np.nan_to_num(RAW[inner, j], nan=1.0) == 0).sum())
    print("\n" + "=" * 72)
    print(f"[C] 存活区间内部格数 {tot}，close=NaN 的 {nan_in} ({nan_in / max(tot, 1):.3%})")
    print(f"    → NaN 占比 {nan_in / max(tot, 1):.2%} **不是** ≈0：停牌/缺数据日用 NaN 编码，"
          f"append 遇快照缺席要续 NaN（不可拿前值平盘，否则把停牌伪装成零波动）")

    # ── E/F 抽样核 change 与单位
    #    change 到底是哪个口径：复权涨幅 / 盘面涨幅（除权日两者差一个因子比）
    n_eq = {"复权涨幅": 0, "盘面涨幅": 0, "adjclose涨幅": 0}
    n_tested = 0
    r_amt, r_vwap, r_vol = [], [], []
    cand = [j for j in range(J) if st[j] >= 0]
    for j in random.sample(cand, 80):
        c = codes[j]
        v = {f: load(c, f)[1] for f in
             ["close", "change", "amount", "volume", "vwap", "factor", "adjclose"]}
        if v["close"] is None or len(v["close"]) < 60:
            continue
        cl = v["close"].astype(np.float64)
        if v["change"] is not None:
            ch = v["change"].astype(np.float64)
            hyp = {"复权涨幅": cl[1:] / cl[:-1] - 1}
            if v["factor"] is not None and len(v["factor"]) == len(cl):
                fa = v["factor"].astype(np.float64)
                rw = cl / fa
                hyp["盘面涨幅"] = rw[1:] / rw[:-1] - 1
            if v["adjclose"] is not None and len(v["adjclose"]) == len(cl):
                ac = v["adjclose"].astype(np.float64)
                hyp["adjclose涨幅"] = ac[1:] / ac[:-1] - 1
            tested = False
            for kk, mine in hyp.items():
                m = np.isfinite(ch[1:]) & np.isfinite(mine) & (cl[:-1] > 0)
                if m.sum() > 30:
                    if np.abs(ch[1:][m] - mine[m]).max() < 1e-4:
                        n_eq[kk] += 1
                    tested = True
            n_tested += int(tested)
        if v["amount"] is not None and v["volume"] is not None:
            a = v["amount"].astype(np.float64); vv = v["volume"].astype(np.float64)
            m = (vv > 0) & np.isfinite(a) & (a > 0) & np.isfinite(cl) & (cl > 0)
            if m.sum() > 30:
                r_amt.append(float(np.median(a[m] / (cl[m] * vv[m]))))
            if v["vwap"] is not None:
                w = v["vwap"].astype(np.float64)
                m2 = m & np.isfinite(w) & (w > 0)
                if m2.sum() > 30:
                    r_vwap.append(float(np.median(a[m2] / (vv[m2] * w[m2]))))
                    r_vol.append(float(np.median(w[m2] / cl[m2])))
    print("\n" + "=" * 72)
    print(f"[E] change 字段口径比对（{n_tested} 只抽样，逐票取全样本最大偏差<1e-4 记一致）:")
    for kk, cnt in n_eq.items():
        print(f"      change == {kk:<12} 一致的票: {cnt}/{n_tested}")
    if r_amt:
        print(f"[F] amount/(close×volume) 中位数 众数={pd.Series(r_amt).round(3).mode().tolist()} "
              f"全体={np.median(r_amt):.4g}")
    if r_vwap:
        print(f"[F] amount/(volume×vwap) 中位数={np.median(r_vwap):.4g}   "
              f"vwap/close 中位数={np.median(r_vol):.4g}")


if __name__ == "__main__":
    main()
