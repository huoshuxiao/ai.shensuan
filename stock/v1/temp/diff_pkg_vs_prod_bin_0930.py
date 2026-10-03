#!/usr/bin/env python3.10
# -*- coding: utf-8 -*-
"""留出对拍 + 分裂诊断：chenditc 社区包（tag 2026-09-29）vs 生产 bin，逐格逐字段逐位

为什么先跑这个：生产 bin 的历史与社区包同源（日历止于 09-22 那一版），09-23 / 09-24 /
09-28 这三格是**我们自己的日更**拿新浪批量快照 append 的。要把包里的 09-29 那一格接上，
必须先知道「换过写手的那三格和包差在哪、差多少、差在哪些字段」。

读数按 日 × 字段 分裂成三层，混在一起会看不出病根：
  value_diff  两边都有值但不相等 → 再看相对差分布（1e-6 级 = 舍入口径；1e-2 级 = 真数据差）
  nan_asym    一边 NaN 一边有值   → 两源对「这只票那天有没有行情」意见不一致（停牌口径）
  only_one    一边数组根本到不了那格
**amount 是唯一不含 factor 的价格类字段**（amount = 真成交额/1000），它若逐位相同，就证明
两源的盘面成交额一致，价格格的差异全部来自复权因子那条腿，而不是行情本身。

判据（任一不满足 ⇒ 退出码 1，不写任何东西）：
  A 日历：生产 day.txt 必须是包 day.txt 的严格前缀，且包只多 09-29。
  B 起始下标：每只票每个字段两边 [0] 相等。
  C 历史前缀（≤09-22，同源段）：value_diff 与 nan_asym 都必须为 0。
  D 反恒真闸：可比格数必须接近 票×字段 的量级，缺文件必须为 0，包里 09-29 必须有格。
"""
import argparse
import os
import sys

import numpy as np

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
PKG = "/home/sunwenkun/Developer/agent-workspace/qlib_pkg_20260929/qlib_bin"
FIELDS = ["open", "high", "low", "close", "volume", "amount",
          "factor", "vwap", "adjclose", "change"]


def read_cal(p):
    return [ln.strip() for ln in open(os.path.join(p, "calendars/day.txt")) if ln.strip()]


def read_bin(p, inst, field):
    f = os.path.join(p, "features", inst, f"{field}.day.bin")
    if not os.path.exists(f):
        return None
    a = np.fromfile(f, dtype="<f4")
    return int(a[0]), a[1:]


def cells(sp, ap, sq, aq, g0, g1):
    """全局下标 [g0,g1) 上的两边格 → dict(可比/值差/NaN单边/相对差数组/样本)

    值差判据用 float32 逐位不等（x != y），不是相对差 > 0：后者会把「只差一个 ULP」
    的两格算成相同（x/y-1 舍入成 0），而逐位不等才是这里要数的「不一样」。
    """
    lo, hi = max(sp, sq, g0), min(sp + ap.size, sq + aq.size, g1)
    out = {"cmp": 0, "val": 0, "nan": 0, "rel": np.array([]), "samp": [], "nanpos": []}
    if hi <= lo:
        return out
    x = ap[lo - sp:hi - sp]
    y = aq[lo - sq:hi - sq]
    nx, ny = np.isnan(x), np.isnan(y)
    both = ~(nx | ny)
    out["cmp"] = int(x.size)
    out["nan"] = int((nx ^ ny).sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.abs(x / y - 1.0)
    rel = np.where(np.isfinite(rel), rel, np.inf)
    rel = np.where(both, rel, 0.0)          # 不可比的格不参与「最大差」
    diff = both & (x != y)
    out["val"] = int(diff.sum())
    out["rel"] = rel[diff]
    if diff.any():
        i = int(np.argmax(rel))
        out["samp"] = [(int(lo + i), float(x[i]), float(y[i]), float(rel[i]))]
    if out["nan"]:
        for k in np.flatnonzero(nx ^ ny)[:6]:
            out["nanpos"].append((int(lo + k), "生产 NaN" if nx[k] else "包 NaN"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", type=int, default=0, help="等距抽样 N 只（0=全市场）")
    a = ap.parse_args()

    cal_p, cal_q = read_cal(PROD), read_cal(PKG)
    print(f"A 日历：生产 {len(cal_p)} 格（末 {cal_p[-1]}）| 包 {len(cal_q)} 格（末 {cal_q[-1]}）"
          f"| 严格前缀 {'✅' if cal_q[:len(cal_p)] == cal_p else '❌'}"
          f"| 包多 {cal_q[len(cal_p):]}")
    if cal_q[:len(cal_p)] != cal_p or cal_q[len(cal_p):] != ["2026-09-29"]:
        return 1
    g_cut = len(cal_p) - 1
    g22 = cal_p.index("2026-09-22")
    days = {"hist": (g22 + 1,), "d23": (g22 + 1, g22 + 2),
            "d24": (g22 + 2, g22 + 3), "d28": (g_cut, g_cut + 1)}
    print(f"   分段：≤{cal_p[g22]} 同源；我们写 {cal_p[g22+1:]}；包独有 {cal_q[-1]}\n")

    insts = sorted(os.listdir(os.path.join(PROD, "features")))
    if a.codes and a.codes < len(insts):
        insts = insts[::int(np.ceil(len(insts) / a.codes))]
    print(f"扫 {len(insts)} 只 × {len(FIELDS)} 字段{'（等距抽样）' if a.codes else '（全市场）'}",
          flush=True)

    acc = {d: {f: {"cmp": 0, "val": 0, "nan": 0, "rel": []} for f in FIELDS} for d in days}
    worst = {d + "|" + f: ("", -1, -1, -1, -1.0) for d in days for f in FIELDS}
    nan_hist = []      # 同源段里两边 NaN 单边的那几格（要能点名列举）
    fac_big = []       # 我们写的三天：复权因子相对差 >1e-3 的票
    start_bad = file_missing = 0
    n29_has = n29_short = 0
    fac29 = []
    day_g = {"d23": g22 + 1, "d24": g22 + 2, "d28": g_cut}

    def at(b, g):
        """取全局下标 g 那格的值；数组到不了就返回 NaN"""
        j = g - b[0]
        return float(b[1][j]) if 0 <= j < b[1].size else float("nan")

    for n, inst in enumerate(insts):
        if n and n % 800 == 0:
            print(f"  …{n}/{len(insts)}", flush=True)
        has_dir_pkg = os.path.isdir(os.path.join(PKG, "features", inst))
        cur = {}
        for f in FIELDS:
            bp, bq = read_bin(PROD, inst, f), (read_bin(PKG, inst, f) if has_dir_pkg else None)
            if bp is None or bq is None:
                file_missing += 1
                continue
            cur[f] = (bp, bq)
            if bp[0] != bq[0]:
                start_bad += 1
            for d, rng in days.items():
                g0 = bq[0] if d == "hist" else rng[0]
                g1 = rng[-1] if d == "hist" else rng[1]
                r = cells(bp[0], bp[1], bq[0], bq[1], g0, g1)
                acc[d][f]["cmp"] += r["cmp"]
                acc[d][f]["val"] += r["val"]
                acc[d][f]["nan"] += r["nan"]
                if r["rel"].size:
                    acc[d][f]["rel"].append(r["rel"])
                if r["samp"] and r["samp"][0][3] > worst[d + "|" + f][4]:
                    worst[d + "|" + f] = (inst, ) + r["samp"][0]
                if d == "hist" and r["nanpos"]:
                    nan_hist.append((inst, f) + r["nanpos"][0])
            g29 = g_cut + 1
            j = g29 - bq[0]
            if 0 <= j < bq[1].size:
                if f == "close":
                    n29_has += 1
                if f == "factor":
                    jp = g_cut - bq[0]
                    v, v0 = float(bq[1][j]), float(bq[1][jp]) if 0 <= jp < bq[1].size else np.nan
                    if np.isfinite(v) and np.isfinite(v0) and abs(v / v0 - 1) > 1e-3:
                        fac29.append((inst.upper(), v0, v))
            elif f == "close":
                n29_short += 1
        for d, g in day_g.items():
            if not all(k in cur for k in ("factor", "close", "change")):
                break
            pf, qf = at(cur["factor"][0], g), at(cur["factor"][1], g)
            if not (np.isfinite(pf) and np.isfinite(qf)) or abs(qf / pf - 1.0) <= 1e-3:
                continue
            fac_big.append((d, inst.upper(), pf, qf, qf / pf,
                            at(cur["close"][0], g), at(cur["close"][1], g),
                            at(cur["change"][0], g), at(cur["change"][1], g)))

    print("\n" + "=" * 84)
    print("分裂读数：值差（两边都有值不等）/ NaN 单边（一边有值一边没值）/ 相对差分位")
    print("=" * 84)
    rc = 0
    for d, nm in (("hist", f"同源前缀 ≤{cal_p[g22]}"), ("d23", "09-23 我们写"),
                  ("d24", "09-24 我们写"), ("d28", "09-28 我们写")):
        print(f"  ── {nm} ──")
        for f in FIELDS:
            r = acc[d][f]
            if not r["cmp"]:
                continue
            rel = np.concatenate(r["rel"]) if r["rel"] else np.array([0.0])
            mx = float(rel.max())
            p50 = float(np.median(rel)) if rel.size else 0.0
            big = int((rel > 1e-3).sum())
            flag = "✅" if (r["val"] + r["nan"]) == 0 else ("❌" if d == "hist" else "⚠️")
            if d == "hist" and (r["val"] or r["nan"]):
                rc = 1
            w = worst[d + "|" + f]
            print(f"    {flag} {f:<7} 比 {r['cmp']:>11,} | 值差 {r['val']:>7,} "
                  f"({0 if not r['cmp'] else r['val'] / r['cmp']:>7.3%}) | NaN单边 {r['nan']:>6,} "
                  f"| 相对差 中位 {p50:.2e} max {mx:.3e} | >1e-3 的 {big:,}")
            if w[4] > 0:
                print(f"        最大那格：{w[0]} g={w[1]} 生产={w[2]:.8g} 包={w[3]:.8g} "
                      f"相对差={w[4]:.3e}")
    print(f"\n  B 起始下标不符 {start_bad} | 缺文件 {file_missing} "
          f"| 包里 09-29 close 有格 {n29_has} / 数组到不了 {n29_short}（共 {len(insts)} 只）")
    print(f"  09-29 factor 相对 09-28 跳 |Δ|>1e-3：{len(fac29)} 只（占 "
          f"{(len(fac29) / max(1, n29_has)):.2%}）"
          + "，样例 " + ", ".join(f"{i}(×{b / aa:.4f})" for i, aa, b in fac29[:6]))
    print("\n" + "=" * 84)
    print("点名 1：同源段（≤09-22）里 NaN 单边的那几格 —— 判据 C 就是被它判红的")
    print("=" * 84)
    for inst, f, g, which in nan_hist[:24]:
        print(f"    {inst.upper():9s} ${f:<7} g={g} 日历={cal_q[g] if g < len(cal_q) else '?'} {which}")
    print(f"    合计 {len(nan_hist)} 格"
          + ("（每只票每字段最多列 6 格，超出只计数不点名）" if any(
                acc[d][f]["nan"] > 0 for d in ("hist",) for f in FIELDS) else ""))

    print("\n" + "=" * 84)
    print("点名 2：我们写的三天里，复权因子两边差 >0.1% 的票（价格/量都跟着它一起偏）")
    print("=" * 84)
    for d, nm in (("d23", "09-23"), ("d24", "09-24"), ("d28", "09-28")):
        rows = sorted([x for x in fac_big if x[0] == d], key=lambda x: -abs(x[4] - 1))
        print(f"  ── {nm}：{len(rows)} 只 ──")
        for x in rows[:14]:
            print(f"    {x[1]:9s} 因子 我们={x[2]:.8g} 包={x[3]:.8g} (×{x[4]:.4f}) "
                  f"| 收盘 我们={x[5]:.6g} 包={x[6]:.6g} | 日收益 我们={x[7]:+.5%} 包={x[8]:+.5%}")
        if len(rows) > 14:
            print(f"    …另有 {len(rows) - 14} 只")

    need = len(insts) * len(FIELDS)
    teeth = [t for t in (
        f"同源前缀可比只有 {acc['hist']['close']['cmp']:,} 格" if acc["hist"]["close"]["cmp"] < need * 0.5 else "",
        f"缺文件 {file_missing}" if file_missing else "",
        f"包里 09-29 只有 {n29_has} 只有格" if n29_has < len(insts) * 0.5 else "") if t]
    for t in teeth:
        print(f"  ❌ 尺子没牙：{t}")
    if teeth:
        rc = 1
    print("\n判定：" + ("✅ 同源段零差异，可以谈接 09-29" if rc == 0
                       else "❌ 停下：同源段有差 或 尺子没读到东西"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
