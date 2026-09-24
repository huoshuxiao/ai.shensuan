# -*- coding: utf-8 -*-
"""股票线 qlib bin 日更：收盘后把当日 A 股日线 append 进 bin（09-24）

为什么需要这一步
----------------
本线的 qlib bin 来自社区全市场包，是一次性 dump，**日历停在 2026-09-22**。
下游全链路都从它取数（pregen_source_data → daily_pv.h5 → 截面/组合评估 →
日频名单 → 看板），bin 不往前走，所有指标就永远在算同一截历史。本脚本就是
让它往前走的那一步：收盘后跑一次，日历 +1 格、每只票的 .day.bin 尾部各加一格。

只管 `instruments/all.txt`。csi300/csi500/... 那几份是**真·成分股分段表**
（csi300.txt 有 16198 行、同一票多段），日更没资格改成分，只能等整包重刷。

写入格式（与包本身同构，同 `etf/v1/src/data/dump_qlib_bin.py` 的 `_write_field`）
    features/<代码小写>/<field>.day.bin = float32[]，[0] 是起始日历下标，
    其后逐日对齐；**数组以最后一个有效格结束**（停牌不留尾部 NaN，中途停牌在
    序列内部留 NaN 格）。实测：信达证券 09-15 起停牌，数组止于 09-14、
    instruments 的 END 也是 09-14，序列内部有 20 个 NaN 格。

复权口径（09-24 锁死，#77）
--------------------------
包里每只票的价格是「后复权价 ÷ 首日盘面价」，锚在**上市首日**：
    首日 $close == 1.0              400 只抽样 400/400 精确成立
    首日 $factor == 1/首日盘面价     茅台 0.028129 = 1/35.55
    $factor_t = F_t / raw_first      F_t = 交易所口径累计复权因子
锚在首日 ⇒ **追加新一天永远不挪历史**，这是敢往 6159 只票上写字节的前提。
逐字段口径（同日外部真值反证，09-24）：
    close/open/high/low = 盘面价 raw × $factor        （后复权、按首日归一）
    volume             = 真实手数 / $factor           （不是「手」！）
    amount             = 真成交额(元) / 1000           （千元）
    vwap               = 真均价(元/股) × $factor       （复权均价）
    adjclose           = close × 首日盘面价            （未归一的后复权价）
    change             = close.pct_change()            （复权日收益）
证据分两层，别混着引用：
    价格侧：shell/probe_spot_vs_bin_0924.py —— 09-23 的 bulk 快照逐票复现 bin 的
        09-22 盘面收盘，5553 只票 p95 相对差 8.8e-08。
    量额侧：shell/probe_volume_vs_external_0924.py —— 拿新浪裸接口取 **同一天
        (09-22)** 的真实成交量，9 只票（f 从 0.0177 到 0.602）逐票
        volume÷(真实手数/f) ≡ 1.0、amount÷(元/1000) 0.974~1.008、
        vwap÷(真均价×f) 同量级。这条是独立判据；原先那份
        「amount÷(close×volume×100)=0.001」是拿 bin 自己的 volume 反推 amount，
        循环论证，不能当 volume 口径的依据（shell/probe_volume_unit_0924.py
        判据二也同理：w·V=10A 只是包内恒等式）。

新一天 factor 从哪来（不需要任何外部复权因子接口）
--------------------------------------------------
交易所公布的**除权参考价**就是快照的「昨收」列：正常日它等于上一交易日盘面
收盘，除权日它被下调。由 $close = raw × $factor 加「复权价连续」：
    f_T = f_{T-1} × raw_{T-1} / 昨收_T ，  raw_{T-1} = ($close/$factor) 上一有效格
    正常日 昨收 == raw_{T-1} ⇒ f 不变；除权日才动
实测 09-23 那场：5553 只活票里 5535 只 昨收 与 bin 末格 raw 相对差 <1%，
其余 18 只全是当日除权（名称带 XD、或 10 转 x 造成 -28.6%），正是这条公式吃的。

行情源为什么是 bulk 快照
------------------------
09-24 实测逐票接口不能当饭吃：腾讯 tx 在 akshare 1.18.81 直接 ValueError
（`invalid literal for int(): 'i'`），东财 RemoteDisconnected，新浪单票在同 IP
连打时返回空 dict（akshare 内部炸 KeyError: 'date'，8/8 全灭）。而
`ak.stock_zh_a_spot()` 一次请求 24 秒拿全市场 5567 行（含北交所 346 只），
成交量单位是**股**（成交额/成交量/最新价 中位 1.0027）。

安全边界
--------
* 一次只补一个交易日（= 日历末日的下一个交易日）；缺几天就跑几天。
* 三道闸：快照 15:00 以后、昨收↔bin 末格对齐率 ≥98%、代码前缀白名单。
  任一不过直接退出，不写半截 —— 这条闸同时挡住了「同一天跑第二次」。
  快照缓存也认收盘闸：收盘前误跑抓到的盘中快照**不落缓存、也不复用**，否则会
  把当晚那场真日更永久挡在门外（`fetch_spot`）。
* 只追加不回写：待写格子若落在数组已有区间内 ⇒ 跳过（改历史是另一轮口径作业）。
* 幂等：同一场次重复跑，日历已含该日就退出；中途崩了重跑会按同一份快照缓存
  把没补上的票补齐（数组只会长，不会写歪）。
* 动字节前先把 `day.txt` / `all.txt` 存进 `<快照目录>/bin_backup_<场次>/`。
* `--rollback` 认两种残局：日历已含该场次 ⇒ 日历与数组一起削回；数组比日历长
  （半截事故：features 写了尾巴、日历没写）⇒ **日历一个字节不动**，只砍越界格。
  按「现有格数 - (保留天数 - 起始下标)」砍，所以对补 NaN 占位的复牌票同样成立。

用法（收盘后）
    cd stock/v1/src && /usr/bin/python3.10 data/update_qlib_bin_daily.py --dry-run
    cd stock/v1/src && /usr/bin/python3.10 data/update_qlib_bin_daily.py
    # 出错就还原：
    cd stock/v1/src && /usr/bin/python3.10 data/update_qlib_bin_daily.py --rollback
"""

import argparse
import glob
import os
import re
import shutil
import socket
import sys
import time

import numpy as np
import pandas as pd

socket.setdefaulttimeout(90)

# 包内实际存在的 10 个字段（ls features/sh600519 数出来的）
FIELDS = ["open", "high", "low", "close", "volume", "amount",
          "factor", "vwap", "adjclose", "change"]
MIN_BAR = 1e-6              # 价格低于此视为无效（停牌票快照给 0.00）
ALIGN_TOL = 1e-2            # 昨收 ↔ bin 末格 raw 的「对齐」容差
ALIGN_MIN = 0.98            # 对齐率下限：不到就判定快照不是这一场
RATIO_BOUND = (0.05, 20.0)  # 单日 factor 倍数的可信区间，出界只记账不采信
CODE_PREFIX = r"^(SH60|SH68|SZ00|SZ30|BJ43|BJ83|BJ87|BJ88|BJ92)"


# ---------- bin 读写 ----------
def read_calendar(provider):
    return [ln.strip() for ln in
            open(os.path.join(provider, "calendars", "day.txt")) if ln.strip()]


def write_lines(path, lines):
    """整文件原子替换（临时文件 + rename）：别让下游读到半截日历"""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def read_field(provider, inst, field):
    """→ (起始日历下标, 逐日数据)；文件不存在返回 None"""
    p = os.path.join(provider, "features", inst.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        return None
    a = np.fromfile(p, dtype="<f4")
    return int(a[0]), a[1:]


def append_cells(provider, inst, field, start, cells):
    """只追加：把 cells（float32[]）接到该字段末尾，目录缺失则新建并写 [0]=start"""
    p = os.path.join(provider, "features", inst.lower(), f"{field}.day.bin")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(np.float32(start).tobytes())
    body = np.asarray(cells, dtype="<f4")
    if not body.flags["C_CONTIGUOUS"]:
        body = np.ascontiguousarray(body)
    with open(p, "ab") as fh:
        fh.write(body.tobytes())


# ---------- 行情源 ----------
CLOSE_HM = 15 * 60          # 收盘时刻（分钟）：快照里 90% 以上的行要晚于它
CLOSE_MIN_SHARE = 0.9


def close_share(spot):
    """快照里「15:00 之后」的行占比 —— 判这一场是不是已经收盘"""
    ts = pd.to_datetime(spot["时间戳"].astype(str), format="%H:%M:%S",
                        errors="coerce")
    return float((ts.dt.hour * 60 + ts.dt.minute).ge(CLOSE_HM).fillna(False).mean())


def fetch_spot(session, cache_dir, tries=3):
    """全市场当日快照（一次请求），按场次落缓存以便重跑与事后复核

    缓存只在**收盘后**写、也只复用收盘后的缓存。少这一条会出生产事故：操作者
    收盘前误跑一次就把盘中快照钉成 `spot_<场次>.csv`，当晚再跑仍读到那份盘中
    快照、被收盘闸挡下，这一天永远补不上（除非他知道去删文件）。
    """
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"spot_{session.replace('-', '')}.csv")
    if os.path.exists(path) and os.path.getsize(path) > 4096:
        df = pd.read_csv(path, encoding="utf-8-sig")
        if close_share(df) > CLOSE_MIN_SHARE:
            print(f"  用缓存快照 spot_{session.replace('-', '')}.csv（{len(df)} 行）")
            return df
        print(f"  ⚠️ 缓存快照的收盘行占比 {close_share(df):.1%} 不合格"
              f"（那是收盘前抓的盘中快照），忽略它重取一份")
    import akshare as ak
    last = None
    for t in range(tries):
        try:
            df = ak.stock_zh_a_spot()
            if df is None or len(df) < 4000:
                raise ValueError(f"快照只有 {0 if df is None else len(df)} 行")
            rate = close_share(df)
            if rate > CLOSE_MIN_SHARE:
                df.to_csv(path, index=False, encoding="utf-8-sig")
                print(f"  抓到快照 {len(df)} 行 -> {os.path.basename(path)}")
            else:
                print(f"  抓到快照 {len(df)} 行，但收盘行占比 {rate:.1%}"
                      f"——**不落缓存**（落了就会挡住当晚真正的日更）")
            return df
        except Exception as e:
            last = e
            print(f"  ⚠️ 第 {t + 1} 次取快照失败 {type(e).__name__}: {e}")
            time.sleep(3 + 3 * t)
    raise SystemExit(f"取不到全市场快照：{type(last).__name__}: {last}")


def next_trade_day(cal_end):
    """日历末日的下一个交易日：一次只补一天，缺几天就跑几次"""
    import akshare as ak
    days = pd.to_datetime(ak.tool_trade_date_hist_sina()["trade_date"])
    after = days[days > pd.Timestamp(cal_end)]
    if not len(after):
        raise SystemExit(f"交易日历里没有 {cal_end} 之后的日子")
    return after.iloc[0].strftime("%Y-%m-%d")


def read_all_txt(provider):
    out = {}
    for ln in open(os.path.join(provider, "instruments", "all.txt")):
        q = ln.rstrip("\n").split("\t")
        if len(q) == 3:
            out[q[0]] = (q[1], q[2])
    return out


def phantom_session(snap_dir, cal_end):
    """半截事故的场次没进日历，只能从记账表反推：日期在日历末日之后的最新一场"""
    days = sorted(re.findall(r"append_(\d{8})\.csv$", " ".join(
        glob.glob(os.path.join(snap_dir, "append_*.csv")))))
    after = [d for d in days if d > cal_end.replace("-", "")]
    if not after:
        return ""
    d = after[-1]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


# ---------- 单日计划 ----------
def build_plan(provider, session, spot, idx):
    """算出每只票要追加的那一格，返回 ({inst: {field: value}}, 记账表)

    无有效行情的票（停牌、退市整理、快照给 0 价）压根不进 plan：包的做法是
    「数组停在最后一个有效格」，所以不写就是对的。

    idx 是本场次在日历里的下标。**前收按日历下标取（idx-1 那格）而不是拿数组的
    最后一个有效格**：本场已经写过的票，末格就是它自己，若按末格当前收，
    对齐闸会把正确的快照判成「错场」（09-23 那场 features 先写、日历没跟上，
    闸门率就从 99% 掉到 1.8%）。按下标取则重复跑/崩在半路都能接着补，
    这也才是 docstring 承诺的幂等。停牌空档取不到 idx-1 时才回退到末格。
    各字段的 start_idx 可以不同（实测有 14 只票 close 与 factor 起点不一致），
    所以一律用全局下标换算，不共用同一个数组下标。
    """
    s = spot.copy()
    s["inst"] = s["代码"].str.upper()
    ok = s["inst"].str.match(CODE_PREFIX)
    if (~ok).any():
        print(f"  代码前缀不在 A 股白名单，跳过 {int((~ok).sum())} 行："
              f"{sorted(s.loc[~ok, 'inst'])[:8]}")
    s = s[ok].set_index("inst")

    def cell_at(arr, start, g):
        i = g - start
        if arr is None or not (0 <= i < arr.size):
            return None
        v = float(arr[i])
        return v if np.isfinite(v) else None

    spans = read_all_txt(provider)
    plan, rows = {}, []
    for inst, r in s.iterrows():
        raw_close, raw_open = float(r["最新价"]), float(r["今开"])
        raw_high, raw_low = float(r["最高"]), float(r["最低"])
        amount, vol_share, ref = float(r["成交额"]), float(r["成交量"]), float(r["昨收"])
        rec = {"inst": inst, "名称": r["名称"], "盘面收盘": raw_close,
               "昨收(除权参考价)": ref, "成交量_股": vol_share, "成交额_元": amount,
               "前收_bin": np.nan, "前收下标": np.nan, "f倍数": np.nan, "factor_新": np.nan,
               "在bin里": inst in spans, "备注": ""}
        if not (raw_close > MIN_BAR and raw_open > MIN_BAR and ref > MIN_BAR
                and amount > 0 and vol_share > 0 and raw_high >= raw_low):
            rec["备注"] = "无有效行情（停牌/退市整理/字段为 0）"
            rows.append(rec)
            continue

        got = read_field(provider, inst, "close")
        if got is None:
            # 新上市：bin 里没这只票。按包口径把首日 close 归一成 1.0 ⇒ f=1/raw
            f_new = 1.0 / raw_close
            rec["factor_新"] = f_new
            rec["备注"] = ("新上市建列（首日归一为 1.0；若它其实在更早的某天已上市，"
                        "整段只差一个逐票常数，不影响收益率类因子）")
            plan[inst] = _fields(raw_open, raw_high, raw_low, raw_close, amount,
                                 vol_share, f_new, 1.0 / f_new, np.nan)
            rows.append(rec)
            continue

        s_cl, close = got
        gf = read_field(provider, inst, "factor")
        if gf is None:
            rec["备注"] = "没有 factor.day.bin，跳过"
            rows.append(rec)
            continue
        s_fa, factor = gf
        g_prev = idx - 1
        c_prev = cell_at(close, s_cl, g_prev)
        if c_prev is None:
            hit = np.flatnonzero(np.isfinite(close))
            if not hit.size:
                rec["备注"] = "bin 里全是 NaN，跳过"
                rows.append(rec)
                continue
            g_prev = int(s_cl + hit[-1])
            c_prev = float(close[hit[-1]])
            rec["备注"] = f"idx-1 无数据（停牌/数组更短），前收回退到 {g_prev}"
        f_prev = cell_at(factor, s_fa, g_prev)
        if f_prev is None:
            rec["备注"] = f"factor 在格 {g_prev} 缺失，跳过"
            rows.append(rec)
            continue
        rec["前收下标"] = g_prev
        raw_prev = c_prev / f_prev
        rec["前收_bin"] = raw_prev
        ratio = raw_prev / ref
        if not (RATIO_BOUND[0] < ratio < RATIO_BOUND[1]):
            ratio = 1.0
            rec["备注"] = (f"昨收 {ref:.3f} vs bin 前收 {raw_prev:.3f} 倍数出界 "
                           f"{raw_prev / ref:.3f} ⇒ factor 不调整，需人工看")
        elif abs(ratio - 1.0) > 1e-4:
            rec["备注"] = f"除权：参考价 {ref:.3f} vs 前收 {raw_prev:.3f}（f ×{ratio:.4f}）"
        rec["f倍数"], rec["factor_新"] = ratio, f_prev * ratio
        f_new = f_prev * ratio
        ga = read_field(provider, inst, "adjclose")
        k_adj = np.nan
        if ga is not None:
            a_c = cell_at(ga[1], ga[0], g_prev)
            if a_c is not None:
                k_adj = a_c / c_prev
        plan[inst] = _fields(raw_open, raw_high, raw_low, raw_close, amount,
                             vol_share, f_new, k_adj, c_prev)
        rows.append(rec)

    rep = pd.DataFrame(rows)
    both = rep.dropna(subset=["前收_bin", "昨收(除权参考价)"])
    d = (both["昨收(除权参考价)"] / both["前收_bin"] - 1).abs()
    exact, within = float((d < 1e-4).mean()), float((d < ALIGN_TOL).mean())
    n_gap = int(rep["备注"].str.startswith("idx-1 无数据").sum())
    print(f"  快照 A 股 {len(rep)} 行、待写 {len(plan)} 只；昨收↔bin 前收（按日历下标）："
          f"逐字相同 {exact:.2%}、相对差 <{ALIGN_TOL:g} 占 {within:.2%}"
          f"（{n_gap} 只回退到末格，{int((rep['备注'] != '').sum())} 只有备注）")
    # 两道闸各挡一种错：逐字相同率证明「快照就是这一场」（错一天的话 2 位小数的
    # 价格不会撞上），1% 容差率证明「bin 没跑在快照前面」。
    if len(both) and (exact < 0.90 or within < ALIGN_MIN):
        raise SystemExit(
            f"昨收对齐：逐字 {exact:.2%} / <{ALIGN_TOL:g} {within:.2%}，低于门槛 "
            f"{0.90:.0%}/{ALIGN_MIN:.0%} —— 快照不是 {session} 这一场"
            "（收盘前就跑了？bin 比快照还新？）。拒绝写入。")
    return plan, rep


def _fields(raw_open, raw_high, raw_low, raw_close, amount, vol_share,
            f_new, k_adj, close_prev):
    """盘面 OHLCV -> 包内 10 字段（口径见模块 docstring）"""
    close = raw_close * f_new
    return {
        "open": raw_open * f_new,
        "high": raw_high * f_new,
        "low": raw_low * f_new,
        "close": close,
        "volume": (vol_share / 100.0) / f_new,        # 真实手数 / factor
        "amount": amount / 1000.0,                    # 元 -> 千元
        "factor": f_new,
        "vwap": (amount / vol_share) * f_new,         # 真均价 × factor
        "adjclose": close * k_adj if np.isfinite(k_adj) else np.nan,
        "change": close / close_prev - 1.0 if np.isfinite(close_prev) else np.nan,
    }


# ---------- 落盘 ----------
def backup_meta(provider, snap_dir, session):
    """把两份清单存一份：day.txt / all.txt 是文本小文件，回滚时按原样还原"""
    d = os.path.join(snap_dir, f"bin_backup_{session.replace('-', '')}")
    os.makedirs(d, exist_ok=True)
    for name in ("calendars/day.txt", "instruments/all.txt"):
        src = os.path.join(provider, name)
        with open(src, "rb") as fh, open(os.path.join(d, os.path.basename(name)), "wb") as out:
            out.write(fh.read())
    return d


def write_day(provider, cal, session, plan, dry):
    """日历 +1 格，再把每只票的尾巴补到新格（中途停牌的需要 NaN 占位）

    每只票要写的格数 = 新日下标 - (起始下标 + 现长)，停牌复牌时会 >1：
    前面那些天是 NaN，最后一格才是今天的行情。负数表示该格已有数据 ⇒ 跳过，
    本脚本只往末尾加字节，绝不回写历史。

    dry 必须一路管到 append_cells：09-24 第一版只在最后 gate 了日历，
    结果 --dry-run 真把 5555 只票的尾巴写进了 bin（靠快照对齐闸才没写第二遍）。
    """
    new_cal = cal + [session]
    idx = len(cal)                      # 新交易日在日历里的下标
    n_pad = n_ahead = 0
    for inst, v in plan.items():
        got = read_field(provider, inst, "close")
        if got is None:                 # 新上市：整目录新建，起点就是这一天
            if not dry:
                for field in FIELDS:
                    append_cells(provider, inst, field, idx, [v.get(field, np.nan)])
            continue
        close_base, close_arr = got
        gap = idx - (close_base + len(close_arr))
        if gap < 0:
            n_ahead += 1                # 这一格已写过：崩在半路后重跑 / 幂等重跑
            continue
        n_pad += gap > 0
        cells = dict(v)
        if gap > 0:
            # 停牌空档之后的第一天：pct_change 没有可比的前收盘，按包口径留 NaN
            cells["change"] = np.nan
        if dry:
            continue
        for field in FIELDS:
            have = read_field(provider, inst, field)
            if have is None:
                append_cells(provider, inst, field, close_base,
                             [np.nan] * gap + [cells.get(field, np.nan)])
                continue
            base, arr = have
            extra = idx - (base + len(arr))
            if extra < 0:
                continue
            append_cells(provider, inst, field, base,
                         [np.nan] * extra + [cells.get(field, np.nan)])
    print(f"  {'[dry-run] ' if dry else ''}待写 {len(plan)} 只（其中复牌需 NaN 占位 "
          f"{n_pad} 只、这一格已有数据故跳过 {n_ahead} 只），"
          f"日历 {cal[-1]} → {session}")
    if not dry:
        write_lines(os.path.join(provider, "calendars", "day.txt"), new_cal)
    return new_cal


def refresh_all_txt(provider, session, plan, dry):
    """instruments/all.txt：今天写到行情的票 END 跟着推到场次，新上市加一行

    刻意**只做最小改动**：不重写没动过的行。包里退市票的 END 记的是摘牌日、
    数组尾部还留着 NaN 整理期（实测 SH600005 END 2017-02-13、数组止于
    2017-02-14），按「最后一个有效格」重算会把这 18 只历史票的 END 全往回挪，
    白制造一批 diff。成分股表（csi300 等）是分段真名单，更不动。
    """
    path = os.path.join(provider, "instruments", "all.txt")
    lines = [ln.rstrip("\n") for ln in open(path) if ln.strip()]
    spans = {ln.split("\t")[0]: ln.split("\t")[1:] for ln in lines}
    moved = [i for i in plan if i in spans]
    added = sorted(i for i in plan if i not in spans)
    out = []
    for ln in lines:
        inst, start, _end = ln.split("\t")
        out.append(f"{inst}\t{start}\t{session}" if inst in moved else ln)
    for inst in added:
        out.append(f"{inst}\t{session}\t{session}")
    out.sort()
    print(f"  instruments/all.txt：{len(out)} 行（END 推到 {session} 的 {len(moved)} 只、"
          f"新入表 {len(added)} 只 {added[:5]}）")
    if added:
        print(f"    ⚠️ 新上市的成分股归属不在 all.txt 里，csi300 等成分表要等整包重刷")
    if not dry:
        write_lines(path, out)


def rollback(provider, snap_dir, session=None, dry=False):
    """撤销 append：把数组削回「保留日历区间」，必要时同时削日历

    两种残局都要能吃，而且修法不同：
      * 真跑完一场 —— 日历已含该场次 ⇒ 日历削掉末日，数组跟着削。
      * 半截事故 —— 数组比日历长（09-24 的 --dry-run 就是这么把 5555 只票的
        尾巴写进 bin 的：日历没动，features 却多了一格）⇒ **日历一个字节都不动**，
        只砍越界格。这里若按「len(cal)-1」砍就会把每只票的合法末格一起削掉。
    判据用「下标」而不是「比上一版长多少」：砍的格数 = 现有格数 - (保留天数 - 起始下标)，
    对停牌补的 NaN 占位格同样成立（那些格本来就在被砍的区间里）。
    保留天数之外什么都不剩的文件（本次事故新建的新上市目录）整个删掉。
    """
    cal = read_calendar(provider)
    if len(cal) < 2:
        raise SystemExit("日历短得不对劲，不动")

    def end_of(inst_dir, field):
        """→ (起始下标, 数据格数)；只用 4 字节表头 + 文件大小，不把整份读进内存"""
        p = os.path.join(inst_dir, f"{field}.day.bin")
        if not os.path.exists(p):
            return None, None
        with open(p, "rb") as fh:
            start = int(np.frombuffer(fh.read(4), dtype="<f4")[0])
        return start, os.path.getsize(p) // 4 - 1

    feat = os.path.join(provider, "features")
    insts = sorted(d for d in os.listdir(feat) if os.path.isdir(os.path.join(feat, d)))
    max_end = -1
    for inst in insts:
        for field in FIELDS:
            start, n = end_of(os.path.join(feat, inst), field)
            if n is not None:
                max_end = max(max_end, start + n - 1)
    if max_end < 0:
        raise SystemExit("features 空了，不动")

    beyond = max_end - (len(cal) - 1)          # >0 ⇒ 数组比日历长，日历是干净的
    if session in cal:
        if cal.index(session) != len(cal) - 1:
            raise SystemExit(f"只能撤销末日：{session} 不在 {cal[-1]} 这个位置")
        keep, new_cal = len(cal) - 1, cal[:-1]
    elif beyond > 0:
        keep, new_cal = len(cal), cal          # 只砍多出来的格，日历原样
    elif session is not None:
        raise SystemExit(f"日历没有 {session}、数组也没越界（末格 {cal[max_end]}），无可撤销")
    elif os.path.isdir(os.path.join(snap_dir, f"bin_backup_{cal[-1].replace('-', '')}")):
        # 有那天的元数据备份 ⇒ 末日确实是本脚本 append 上去的，可以撤
        keep, new_cal = len(cal) - 1, cal[:-1]
    else:
        raise SystemExit(
            f"无可撤销：日历末 {cal[-1]} 没有 bin_backup_{cal[-1].replace('-', '-')}/，"
            f"说明那一天不是日更写进去的（整包 dump 自带）。"
            f"要撤某场请显式 --session YYYY-MM-DD。")

    print(f"回滚：日历 {len(cal)} 格（末 {cal[-1]}）、数组末格下标 {max_end}"
          f"（越界 {beyond} 格）⇒ 保留前 {keep} 格，砍掉 "
          f"{cal[keep] if keep < len(cal) else '日历之外的 phantom 格'}"
          f"{'（dry-run，只报不砍）' if dry else ''}")

    n_trim = n_del = n_dir_del = 0
    for inst in insts:
        d = os.path.join(feat, inst)
        for field in FIELDS:
            p = os.path.join(d, f"{field}.day.bin")
            start, n = end_of(d, field)
            if n is None:
                continue
            target = keep - start
            if target <= 0:                    # 整列都属于被砍的那天 ⇒ 新建的，删
                if not dry:
                    os.remove(p)
                n_del += 1
            elif n > target:
                if not dry:
                    with open(p, "r+b") as fh:
                        fh.truncate(4 + target * 4)
                n_trim += 1
        if not dry and not any(f.endswith(".day.bin") for f in os.listdir(d)):
            os.rmdir(d)
            n_dir_del += 1

    # 被撤销那一天：日历末日（真跑完一场）或记账表里那个没进日历的日子（半截事故）。
    # all.txt 是最小改动写出去的（改 END、加新行），按下标反推不出来 ⇒ 只认那天的备份。
    if keep < len(cal):
        undone = session or cal[-1]
    else:
        undone = session or phantom_session(snap_dir, cal[-1])
    backup = os.path.join(snap_dir, f"bin_backup_{undone.replace('-', '')}")
    if keep < len(cal) and not dry:
        src = os.path.join(backup, "day.txt")
        if os.path.exists(src):                # 有备份就按字节还原，最省事也最准
            shutil.copyfile(src, os.path.join(provider, "calendars", "day.txt"))
            print(f"  日历由 {src} 还原")
        else:
            write_lines(os.path.join(provider, "calendars", "day.txt"), new_cal)
            print(f"  日历削回 {len(new_cal)} 格，末日 {new_cal[-1]}")

    src = os.path.join(backup, "all.txt")
    if not dry and os.path.exists(src):
        shutil.copyfile(src, os.path.join(provider, "instruments", "all.txt"))
        print(f"  instruments/all.txt 由 {src} 还原")
    elif not dry:
        print(f"  ⚠️ 无 {src}：END 改动与新入表行没还原（本次事故就是这个形态），"
              "请手工核对 all.txt 后再重跑 refresh")

    print(f"回滚{'（dry-run，未动字节）' if dry else '完成'}：削尾格 {n_trim} 个文件、"
          f"删整列 {n_del} 个文件（含 {n_dir_del} 个空目录）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--rollback", action="store_true", help="撤销最近一次 append")
    ap.add_argument("--session", default=None,
                    help="指定场次（默认=日历末日的下一个交易日）")
    ap.add_argument("--provider", default=None,
                    help="qlib bin 根，默认 config 的 RDAGENT_QLIB_PROVIDER")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import _bootstrap  # noqa: F401  裸模块名导入（from config import ...）的前提
    from config import RDAGENT_QLIB_PROVIDER, ASHARE_SNAPSHOT_DIR
    provider = args.provider or RDAGENT_QLIB_PROVIDER

    if args.rollback:
        rollback(provider, ASHARE_SNAPSHOT_DIR, args.session, args.dry_run)
        return

    t0 = time.time()
    cal = read_calendar(provider)
    session = args.session or next_trade_day(cal[-1])
    print(f"bin 根 {provider}\n  日历 {cal[0]} ~ {cal[-1]}（{len(cal)} 格）"
          f"→ 待补场次 {session}")
    if session in cal:
        print("  日历已含该场次，无需再补")
        return

    spot = fetch_spot(session, ASHARE_SNAPSHOT_DIR)
    rate = close_share(spot)
    print(f"  收盘时间检查：{rate:.1%} 的行在 15:00 之后")
    if rate <= CLOSE_MIN_SHARE:
        raise SystemExit("这一场还没收盘（或时间戳列坏了），等收盘后再跑")

    plan, rep = build_plan(provider, session, spot, len(cal))
    if not args.dry_run:                       # 先存档再动字节：回滚靠这两份清单
        print(f"  元数据备份 -> {backup_meta(provider, ASHARE_SNAPSHOT_DIR, session)}")
    write_day(provider, cal, session, plan, args.dry_run)
    refresh_all_txt(provider, session, plan, args.dry_run)
    out = os.path.join(ASHARE_SNAPSHOT_DIR, f"append_{session.replace('-', '')}.csv")
    if args.dry_run:
        # 记账表也是产物：dry-run 写下去会让 phantom_session 把一场没发生的
        # append 反推成待撤销的残局
        print(f"  [dry-run] 记账表未落盘（本应写 {out}）")
    else:
        os.makedirs(ASHARE_SNAPSHOT_DIR, exist_ok=True)
        rep.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"  记账表 -> {out}")
    print(f"  {'（dry-run，未写任何字节）' if args.dry_run else '已写入'}"
          f"，用时 {time.time() - t0:.0f}s")
    if not args.dry_run:
        print("  下一步：重生成面板与下游产物（#81）\n"
              "    QLIB_PROVIDER_URI=<上面的 bin 根> python pregen_source_data.py <ws>")


if __name__ == "__main__":
    main()
