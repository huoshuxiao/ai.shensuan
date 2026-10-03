#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""选项A：把社区包的 2026-09-29 那一格接进生产 bin，每只票先过一道「接缝带」硬闸

为什么要这道闸
--------------
包与我们的 bin 是同一份底（≤09-22 全市场 17,949,319 格 × 10 字段值差 0），行情腿可信；
可疑的只有它的 $factor 那条腿。而因子一旦接进来就**永久留在复权序列里**：我们的规则是
    f_T = f_{T-1} × raw_{T-1} / ref_T
往后每一天的 f 都从上一格乘出来 ⇒ 一根错台阶 = 这只票此后所有价格水平类因子的永久偏移。

接缝（接进来之后面板那天会看到的涨幅）
    seam = 复权close_29 / 复权close_28(生产) − 1 = (raw_29 / raw_28) × e − 1
    e = 包 $factor_29 / 包 $factor_28（包声称的那一天因子步长）
    raw_28 取自**生产** bin 第 6479 格 —— 新格就接在它后面，基准必须是它
判据：一个**正确**的因子会把除权完全吃掉 ⇒ seam 就是那一天的真实涨幅 ⇒ 必须落在该票板块的
涨跌停带内（主板 ±10%、创业板/科创板 ±20%、北交所 ±30%），再留 SLACK 余量。带是量出来的，
不是背出来的：见 band_of 里那道「主板 ST 实测走 ±10%」的墙（第一版按 5% 写，把 *ST岭南
09-29 的 -10.39% 判成了假数据）。
    甲 收 e 在带内            → 价格与系数都接
    乙 收 e 出带、收 1 在带内  → 只接价格，系数照抄上一格（M4 那条 SZ002713 就是这一型：
                               交易所说 09-23 不除权、包把因子抬 ×1.2866 ⇒ 复权凭空 +26.9%）
    丙 收 e 与收 1 都出带      → 整格不接（连盘面价格都不可信），下游当停牌

诚实的边界：这道闸是**必要条件**过滤，不是真伪裁决。它抓得住「算出来不可能的涨幅」（SZ002713
那种 28%），抓不住 0.2%~1% 的小假跳（09-29 的 63 只候选里 54 只属于这一档）——那部分按 M3/M7
的实测，与我 bin 自身在用历史（|Δf|>0.1% 日均 0.95% 只）本来就同水位。

写之前必须过的夹具（不绿就 exit，一个字节都不写）
    正对照 = 09-23 交易所说真除权的那批（append_20260923.csv 的 f倍数），必须全判「甲」
    负对照 = SZ002713 的 09-23，必须**不**判「甲」
    人造   = 7 格，其中同一个 15% 接缝在主板判红、在创业板放行 ⇒ 证明带是按板块拨的，
             不是全局一个 ±30%（那正是 ashare_screen.py:425 guard_ret 拦不住的原因）

落盘全部复用生产日更脚本的单点（build_plan / write_day / refresh_all_txt / backup_meta），
撤销走它自带的 --rollback（结尾打印那条命令）。

用法
    /usr/bin/python3.10 stock/v1/temp/graft_pkg_0929_0930.py            # 只算不写
    /usr/bin/python3.10 stock/v1/temp/graft_pkg_0929_0930.py --write    # 真接
"""
import argparse
import hashlib
import importlib.util
import os
import re
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
PROD = os.path.join(ROOT, "common/data/stock/qlib/qlib_data/cn_data")
PKG = "/home/sunwenkun/Developer/agent-workspace/qlib_pkg_20260929/qlib_bin"
SNAP = os.path.join(ROOT, "common/data/stock/daily_snapshot")
HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = "2026-09-29"
CAL_END_BEFORE = "2026-09-28"     # 不满足就拒绝动手：只在这种形态下接
CAL_LEN_BEFORE = 6480
SLACK = 0.02                      # 涨跌停带外放的余量（绝对涨幅 2pp）
JUMP_TOL = 1e-3                   # 因子步长偏离 1 多远算「一次声称的除权」
OUT = os.path.join(HERE, "graft_pkg_0929_0930.class.csv")

_U = None


def u():
    """按路径加载生产日更脚本一次，复用它的落盘单点（口径不在这儿重写第二遍）"""
    global _U
    if _U is None:
        p = os.path.join(ROOT, "common/src/data/stock/update_qlib_bin_daily.py")
        spec = importlib.util.spec_from_file_location("upd", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["upd"] = m
        spec.loader.exec_module(m)        # 顶层只有 stdlib + numpy/pandas，不碰网络
        _U = m
    return _U


def read_field(provider, inst, field):
    return u().read_field(provider, inst.lower(), field)


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def cell(pair, g):
    """取全局日历下标 g 那一格；越界/缺文件/NaN 一律 np.nan"""
    if pair is None:
        return np.nan
    s, a = pair
    i = g - s
    if not (0 <= i < a.size):
        return np.nan
    return np.float64(a[i])


def raw_at(provider, inst, g):
    """盘面（未复权）价 = 复权 close / factor，两棵 bin 树同一把尺子"""
    c = cell(read_field(provider, inst, "close"), g)
    f = cell(read_field(provider, inst, "factor"), g)
    if not (np.isfinite(c) and np.isfinite(f)) or f <= 0:
        return np.nan
    return c / f


def bare(inst):
    return inst[2:] if inst[:2].upper() in ("SH", "SZ", "BJ") else inst


def band_of(inst):
    """该票单日涨跌停带半宽：创业板/科创板 20%、北交所 30%、其余主板 10%

    不给 ST/*ST 单独拨 5% 档（第一版给了，是错的，09-30 实测推翻）：
    我们自己的 bin 最近 60 场、主板 144 只 ST 的 8,573 个日涨幅格，|涨幅| 最大
    10.465%、p99 已经贴着 10.03%、**超过 10.5% 的格数 = 0** —— 和同窗口非 ST
    （p99 10.007%、最大同样收在 10.5% 以内）同一道墙。⇒ 主板 ST 现在走 ±10%。
    名字表还可能是陈旧的，判据不该依赖它，所以这一档整个删掉而不是放宽。
    """
    c = bare(inst)
    if c.startswith(("30", "68")):
        return 0.20
    if c.startswith(("43", "83", "87", "88", "92")):
        return 0.30
    return 0.10


def decide(raw_ratio, e, b):
    """→ (档位, 采信的因子步长, 采信那一档下的接缝)。见模块 docstring 的甲/乙/丙"""
    seam_on = raw_ratio * e - 1.0
    if abs(seam_on) <= b + SLACK:
        return "甲", e, seam_on
    seam_off = raw_ratio - 1.0
    if abs(seam_off) <= b + SLACK:
        return "乙", 1.0, seam_off
    return "丙", 1.0, seam_off


def read_names():
    """名称只用来判 ST：优先 09-28 的收盘快照（最新一份），补 09-23 记账表"""
    names = {}
    for f in ("append_20260923.csv", "spot_20260928.csv"):
        p = os.path.join(SNAP, f)
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        key = "代码" if "代码" in d.columns else "inst"
        for k, v in zip(d[key], d["名称"]):
            names[str(k).upper()] = str(v)
    return names


# ---------- 夹具：先证明这把闸装了牙 ----------
def fixture(names, g23, g22):
    """→ (放行数, 判红数, 是否通过)

    正对照拿**交易所记账表**当答案（09-23 那天的 f倍数 就是真除权名册）：闸若把真除权判成
    出带，说明带太窄或算术写错 ⇒ 接包会把好的也扔掉，所以不许接。
    """
    print("=" * 72)
    print("夹具：这把闸有没有牙（不绿就不写一个字节）")
    print("=" * 72, flush=True)
    ok = True
    n_pass = n_red = 0

    def arm(inst, name, g_now, g_prev, src_now=PKG, src_prev=PROD):
        """按真字节算一遍接缝：包给「今」，生产给「昨」（和新格要接的那条缝同构）"""
        e = cell(read_field(src_now, inst, "factor"), g_now) / \
            cell(read_field(src_now, inst, "factor"), g_prev)
        r_now = raw_at(src_now, inst, g_now)
        r_prev = raw_at(src_prev, inst, g_prev)
        b = band_of(inst)
        d, e_ok, seam = decide(r_now / r_prev, e, b)
        return d, e, r_now / r_prev - 1.0, b, seam, e_ok

    book = pd.read_csv(os.path.join(SNAP, "append_20260923.csv"), encoding="utf-8-sig")
    jia = book[(book["f倍数"] - 1.0).abs() > JUMP_TOL]
    if len(jia) < 3:
        print(f"❌ 正对照空转：09-23 记账表里真除权只有 {len(jia)} 只")
        return 0, 0, False
    print(f"正对照｜09-23 交易所说真除权 {len(jia)} 只 ⇒ 必须全部判「甲」", flush=True)
    bad, worst, killed = [], 0.0, []
    for _, r in jia.iterrows():
        inst = str(r["inst"]).upper()
        d, e, rr, b, seam, _ = arm(inst, r["名称"], g23, g22)
        worst = max(worst, abs(seam))
        if abs(e - 1.0) <= JUMP_TOL:
            killed.append((inst, e, r["f倍数"]))
        if d != "甲":
            bad.append((inst, e, seam, b, d))
            print(f"  ❌ {inst} 包因子比={e:.5f} 接缝={seam:+.4%} 带=±{b:.0%} ⇒ 判「{d}」", flush=True)
    if bad:
        ok = False
        n_red += len(bad)
    else:
        n_pass += len(jia)
        print(f"  ✅ {len(jia)} 只全部在带内（最宽一道接缝 {worst:.2%}）")
        print(f"  削平阈值的代价：这 {len(jia)} 只真除权里有 {len(killed)} 只包的步长够不上 "
              f"{JUMP_TOL:g}（会被当抖动丢掉）：{[f'{i}({e:.5f}/交易所{kb:.5f})' for i, e, kb in killed[:6]]}")

    print("\n负对照｜M4 那条 SZ002713：交易所说不除权、包把因子抬 ×1.2866 ⇒ 必须判红", flush=True)
    d, e, rr, b, seam_on, e_ok = arm("SZ002713", names.get("SZ002713", ""), g23, g22)
    hit = d != "甲"
    ok &= hit
    n_red += hit
    n_pass += not hit
    print(f"  {'✅' if hit else '❌'} SZ002713 包因子比={e:.5f} ⇒ 收系数会得 {(1 + rr) * e - 1:+.4%}"
          f"、收 1 得 {rr:+.4%} 带=±{b:.0%} ⇒ 判「{d}」、采信因子比 {e_ok:g}")

    print("\n人造 7 格｜带按板块拨，同一个接缝在主板出带、在创业板合法", flush=True)
    synth = [("SH600519", "贵州茅台", 0.95, 1.05, "甲"),      # 除权 + 下跌：接缝 -0.25%
             ("SH600000", "人造主板除权", 0.90, 1.11, "甲"),  # 接缝 -0.10%
             ("SZ301301", "人造创业板", 1.15, 1.00, "甲"),    # +15% 在创业板合法
             ("SZ000001", "平安银行", 1.00, 1.15, "乙"),      # 同一个 +15% 在主板出带
             ("SZ300750", "宁德时代", 1.10, 1.14, "乙"),      # 接缝 +25.4% > 22%
             ("BJ830799", "艾融软件", 1.20, 1.13, "乙"),      # 接缝 +35.6% > 32%
             ("SH600750", "人造主板假价格", 1.13, 1.00, "丙")]  # 收 1 也 +13% > 12% ⇒ 整格不接
    for inst, name, rr, e, want in synth:
        b = band_of(inst)
        d, _, seam = decide(rr, e, b)
        hit = d == want
        ok &= hit
        n_pass += hit and want == "甲"
        n_red += hit and want != "甲"
        print(f"  {'✅' if hit else '❌'} {inst:<9} 带=±{b:.0%}+{SLACK:g} 接缝={seam:+.2%}"
              f" ⇒ 判「{d}」 期望「{want}」")
    print(f"\n夹具读数：放行 {n_pass} 只 / 判红 {n_red} 只 ⇒ "
          f"{'装了牙 ✅' if ok else '没牙或算术错 ❌ 不许接包'}")
    return n_pass, n_red, ok


# ---------- 主流程 ----------
def classify():
    """逐票算接缝，→ (分类表, 跳过计数)"""
    prod_cal = u().read_calendar(PROD)
    pkg_cal = u().read_calendar(PKG)
    g29 = len(prod_cal)
    g28 = g29 - 1
    names = read_names()
    feat = os.path.join(PKG, "features")
    pat = re.compile(u().CODE_PREFIX)
    insts = sorted(d for d in os.listdir(feat)
                   if os.path.isdir(os.path.join(feat, d)) and pat.match(d.upper()))
    print(f"\n扫包里 {len(insts)} 只票的 {SESSION} 那一格（只读）", flush=True)
    rows, skip = [], dict.fromkeys(("包里当天无格", "包因子缺失", "生产末格缺失", "新上市"), 0)
    for k, d in enumerate(insts):
        inst = d.upper()
        f = read_field(PKG, d, "factor")
        raw29 = raw_at(PKG, d, g29)
        if np.isnan(raw29):
            skip["包里当天无格"] += 1
            continue
        name = names.get(inst, "")
        common = {"inst": inst, "名称": name, "raw29": raw29, "f29": cell(f, g29),
                  "raw_open": cell(read_field(PKG, d, "open"), g29) / cell(f, g29),
                  "raw_high": cell(read_field(PKG, d, "high"), g29) / cell(f, g29),
                  "raw_low": cell(read_field(PKG, d, "low"), g29) / cell(f, g29),
                  "成交额_元": cell(read_field(PKG, d, "amount"), g29) * 1000.0,
                  "成交量_股": cell(read_field(PKG, d, "volume"), g29) * cell(f, g29) * 100.0}
        # 包里的 volume/amount 与我们同单位（真手数/factor、千元），这里按 _fields 的口径反解回
        # 快照那套「股 / 元」，好让 build_plan 走它自己的原路
        if read_field(PROD, d, "close") is None:
            skip["新上市"] += 1
            rows.append({**common, "档位": "新上市", "带": np.nan, "包因子比": np.nan,
                         "盘面接缝": np.nan, "采信因子比": 1.0, "接缝": np.nan,
                         "raw28_prod": np.nan})
            continue
        raw28 = raw_at(PROD, d, g28)
        if np.isnan(raw28):
            skip["生产末格缺失"] += 1
            continue
        f28 = cell(f, g28)
        if not np.isfinite(f28) or f28 <= 0:
            skip["包因子缺失"] += 1
            continue
        e = cell(f, g29) / f28
        # 包的 $factor 每天都带 1e-4 级抖动（09-29 有 1,920 只落在 1e-4~1e-3 之间），而我们自己
        # 的日更在非除权日写出的步长恒等于 1（ref 与 raw_prev 是同一个 2 位小数的价）。
        # ⇒ 够不上一道真除权的抖动一律削平：因子会被往后的每一天乘出来，焊上去就拆不掉了。
        e_eff = e if abs(e - 1.0) > JUMP_TOL else 1.0
        b = band_of(inst)
        d_, e_ok, seam = decide(raw29 / raw28, e_eff, b)
        rows.append({**common, "档位": d_, "带": b, "包因子比": e, "削平后步长": e_eff,
                     "盘面接缝": raw29 / raw28 - 1.0, "采信因子比": e_ok, "接缝": seam,
                     "raw28_prod": raw28})
        if (k + 1) % 1500 == 0:
            print(f"  …{k + 1}/{len(insts)}", flush=True)
    return pd.DataFrame(rows), skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真接进生产 bin（默认只算不写）")
    ap.add_argument("--no-fixture", action="store_true",
                    help="跳过夹具（只给人复核读数用，别拿来当验收）")
    a = ap.parse_args()

    prod_cal = u().read_calendar(PROD)
    pkg_cal = u().read_calendar(PKG)
    print(f"生产 bin 日历 {len(prod_cal)} 格（末 {prod_cal[-1]}）｜"
          f"包日历 {len(pkg_cal)} 格（末 {pkg_cal[-1]}）")
    if prod_cal[-1] != CAL_END_BEFORE or len(prod_cal) != CAL_LEN_BEFORE:
        raise SystemExit(f"拒绝：生产日历末格不是约定的 {CAL_END_BEFORE}/第 {CAL_LEN_BEFORE} 格"
                         "——先查是谁又写了一格，别在它上面叠第二格")
    if SESSION not in pkg_cal or pkg_cal.index(SESSION) != len(pkg_cal) - 1:
        raise SystemExit("拒绝：包日历末日不是 2026-09-29")
    if pkg_cal[:len(prod_cal)] != prod_cal:
        raise SystemExit("拒绝：包日历不是生产的严格前缀（中间缺一天或多一天，两边下标就对不上）")
    g29 = len(prod_cal)
    print(f"  接缝基准 = 生产第 {g29 - 1} 格（{prod_cal[g29 - 1]}），新格 = 第 {g29} 格（{SESSION}）")

    md5_before = md5(os.path.join(PROD, "calendars", "day.txt"))
    names = read_names()
    if not a.no_fixture:
        n_pass, n_red, ok = fixture(names, pkg_cal.index("2026-09-23"), pkg_cal.index("2026-09-22"))
        if not ok:
            raise SystemExit(f"夹具没过（放行 {n_pass} / 判红 {n_red}）⇒ 一个字节都不写")

    r, skip = classify()
    r.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n分类表 -> {os.path.relpath(OUT, ROOT)}")
    print("跳过：" + "、".join(f"{k} {v} 只" for k, v in skip.items()))
    print("\n档位分布")
    for d_, grp in r.groupby("档位", sort=False):
        extra = ""
        if d_ in ("甲", "乙", "丙"):
            n_e = int((grp["包因子比"].sub(1).abs() > JUMP_TOL).sum())
            extra = (f"｜其中包声称除权 {n_e} 只｜|接缝| 最大 {grp['接缝'].abs().max():.2%}")
        print(f"  {d_:<4} {len(grp):>5} 只{extra}")
    dev = (r["包因子比"] - 1.0).abs()
    print(f"\n因子步长的三本账（阈值 {JUMP_TOL:g} 就是「算不算一次除权」那条线）")
    print(f"  当除权收下 {int((dev > JUMP_TOL).sum())} 只｜当抖动削平 "
          f"{int(dev.between(1e-4, JUMP_TOL).sum())} 只｜本来就等于 1 {int((dev <= 1e-4).sum())} 只")
    small = r[dev.between(1e-4, JUMP_TOL)]
    if len(small):
        worst = small.loc[(small["包因子比"] - 1.0).abs().idxmax()]
        print(f"  被削平里最大一条：{worst['inst']} 步长 {worst['包因子比']:.6f}"
              f"（差 {abs(worst['包因子比'] - 1):.2%}）⇒ 万一它是真分红，这一天起永久少补这么多")

    yi = r[r["档位"] == "乙"].copy()
    yi["e"] = yi["包因子比"]
    print(f"\n被闸拦下系数的 {len(yi)} 只（价格照收、因子照抄上一格）：")
    for _, x in yi.reindex(yi["e"].sub(1).abs().sort_values(ascending=False).index).head(20).iterrows():
        print(f"  {x['inst']} {str(x['名称'])[:10]:<10} 包因子比={x['e']:.5f} "
              f"收系数会得 {x['raw29'] / x['raw28_prod'] * x['e'] - 1:+.4%}（出带）"
              f" ⇒ 收 1 得 {x['盘面接缝']:+.4%} 带=±{x['带']:.0%}")
    bing = r[r["档位"] == "丙"]
    print(f"\n整格不接的 {len(bing)} 只：")
    for _, x in bing.iterrows():
        print(f"  {x['inst']} {str(x['名称'])[:10]:<10} 包因子比={x['包因子比']:.5f} "
              f"收系数 {x['raw29'] / x['raw28_prod'] * x['包因子比'] - 1:+.2%} / "
              f"收 1 {x['盘面接缝']:+.2%} 都出带 带=±{x['带']:.0%}")

    jia = r[r["档位"] == "甲"]
    print(f"\n其中「甲」里被闸改过判定的余量检查：把 SLACK 从 {SLACK:g} 收到 0，"
          f"甲会少 {int((jia['接缝'].abs() > jia['带']).sum())} 只")

    todo = r[r["档位"].isin(["甲", "乙", "新上市"])]
    print(f"\n=> 待接 {len(todo)} 只（甲 {len(jia)} / 乙 {len(yi)} / 新上市 "
          f"{int((r['档位'] == '新上市').sum())}）｜拒系数 {len(yi)}、拒整格 {len(bing)}")

    spot = pd.DataFrame({
        "代码": todo["inst"].values,
        "名称": todo["名称"].fillna("").values,
        "最新价": todo["raw29"].values,
        "今开": todo["raw_open"].values,
        "最高": todo["raw_high"].values,
        "最低": todo["raw_low"].values,
        "成交额": todo["成交额_元"].values,
        "成交量": todo["成交量_股"].values,
    })
    # 造一个「除权参考价」喂回生产脚本：ref = 前收 / 采信因子比 ⇒ 它算出的 ratio 正好 = 采信步长
    # （甲收包的 e，乙与新上市收 1）。前收由 build_plan 自己从 bin 反算，这里用同一个数。
    prev = todo["raw28_prod"].values
    ref = np.where(np.isnan(prev), spot["最新价"].values, prev / todo["采信因子比"].values)
    spot["昨收"] = ref

    # build_plan 是只读的（它只算不写），dry-run 也要走一遍：崩在它某一道理赔判据里
    # 和「压根没跑到」是两种完全不同的结论，别把前者当后者报出去。
    print("\n" + "=" * 72)
    print(f"build_plan（只算）{SESSION}", flush=True)
    print("=" * 72)
    print("  ⚠️ 它打印的「昨收↔bin 前收对齐率」在本次是**自证**（那个昨收就是我拿 bin 自己"
          "造出来的）⇒ 不构成证据。真正的牙只有两把：上面那道接缝带夹具 + M1 的全市场逐格"
          "对表（≤09-22 值差 0、amount 恒差 1 个 float32 ULP）。")
    plan, rep = u().build_plan(PROD, SESSION, spot, g29)
    print(f"  build_plan 出计划 {len(plan)} 只（造了 {len(spot)} 行，"
          f"{len(spot) - len(plan)} 只被它自己的有效行情判据剔掉）", flush=True)
    print("  被剔掉的备注分布：" + "、".join(
        f"{k} {v}" for k, v in rep.loc[~rep.inst.isin(plan), "备注"]
        .fillna("(无备注)").value_counts().items()))
    rep_path = os.path.join(HERE, f"graft_{SESSION.replace('-', '')}_记账表.csv")
    rep.to_csv(rep_path, index=False, encoding="utf-8-sig")
    print(f"  记账表草稿 -> {os.path.relpath(rep_path, ROOT)}")
    if not a.write:
        print("\n[dry-run] 以上全是读数，bin 一个字节没动。加 --write 才接。")
        return 0

    print("\n" + "=" * 72)
    print(f"开写 {SESSION}：先备份元数据", flush=True)
    print("=" * 72)
    print(f"  元数据备份 -> {u().backup_meta(PROD, SNAP, SESSION)}")
    u().write_day(PROD, prod_cal, SESSION, plan, dry=False)
    u().refresh_all_txt(PROD, SESSION, plan, dry=False)
    out = os.path.join(SNAP, f"append_{SESSION.replace('-', '')}.csv")
    rep.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  记账表 -> {out}")

    new_cal = u().read_calendar(PROD)
    print(f"\n验收：日历 {len(new_cal)} 格（末 {new_cal[-1]}）｜"
          f"day.txt md5 {md5_before[:8]} -> {md5(os.path.join(PROD, 'calendars', 'day.txt'))[:8]}")
    if len(new_cal) != CAL_LEN_BEFORE + 1 or new_cal[-1] != SESSION or new_cal[:len(prod_cal)] != prod_cal:
        raise SystemExit("❌ 写完的日历不对，立刻撤销：\n  " + rollback_cmd())
    print(f"  已写 {len(plan)} 只 × 10 字段。下一步：重跑下游 ②面板 → ③名单 → ④审计 → ⑤仓位账本")
    print(f"  要撤销：{rollback_cmd()}")
    return 0


def rollback_cmd():
    return (f"/usr/bin/python3.10 "
            f"{os.path.join(ROOT, 'common/src/data/stock/update_qlib_bin_daily.py')} "
            f"--rollback --session {SESSION} --provider {PROD}")


if __name__ == "__main__":
    sys.exit(main())
