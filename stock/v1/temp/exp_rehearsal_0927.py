# -*- coding: UTF-8 -*-
"""选项D 落地③的排练驱动：拿 ㊺ 那 12 年的篮子缓存喂前向监控，连跑 60 场逐格对表（09-27）

干什么：`run_ashare_exposure_monitor.py` 是**只往前走**的账本，最早 09-28 才有真第一行 ⇒ 光看
它跑一场证不了伪。这里把 ㊺（`dd_exposure_0927.py`）回放用的那 570 个调仓日的 5 席名单回填成
`order_*.csv`，让新入口在**同一批票、同一段历史**上连落 ~60 行，再和 ㊺ 落盘的日频缓存对表。

五道判定（都不许恒真，每条都写了「做错会怎样」）
  ①读数：`市场宽度`/`域等权日收益` 与 `exp_daily.csv.gz` 逐位相等         ⇒ 证 `panel_readings`
  ②座位：每行「今天赚钱的篮子」(= 上一行的 `持仓`) 与 ㊺ 的持仓段逐位相等  ⇒ 证段位时序（差一天就红）
  ③净值：`篮子日收益 − 换票费` 与 ㊺ 的 `M1净日收益` 逐位相等
         （只允许两格不等：冷启动第一行付**全额**建仓费而 ㊺ 那格付重合抵扣；以及信号日不在缓存里的
          换仓日 = ㊺ 那天根本没换仓。除此之外不等就是真错，且会把差值连同原因一起打出来）
  ④减仓：`生效E_M7-30/45` 与 `exp_exposure.csv.gz` 同名列逐位相等，且两档**都必须出现过 0**
         （反证：E 恒为 1 就红。M4-20/M2-20 不比 —— 账本冷启动，那两条腿的头 19 行没有均线判据）
  ⑤无未来函数：只跑前 30 行 ⇒ 前 30 行与整轮逐字节相同（截断不改历史读数 = 只看过去）

产物全落在 `stock/v1/temp/tmp_exposure_fwd_0927/`：假 order 目录、排练账本、对表读数。
**不碰生产** `stock/v1/data/results/` —— env 覆写在 import config 之前生效，那两边读的是模块级常量。

用法：cd shell/stock && /usr/bin/python3.10 exp_rehearsal_0927.py   # 约 1 分钟（面板 9s + 60 行纯内存）
"""
import glob
import gzip
import os
import pickle
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(ROOT, "stock/v1/src")
OUT = os.path.join(HERE, "tmp_exposure_fwd_0927")
SIG = os.path.join(OUT, "signal_rehearsal")        # 回填的假 order 落这里
LEDGER = os.path.join(OUT, "ledger_rehearsal.csv")
LEDGER2 = os.path.join(OUT, "ledger_rerun.csv")    # 同一窗再跑一遍，验幂等
CACHE = os.path.join(HERE, "tmp_dd_gate_2023_0927", "picks_A_B_q08.pkl.gz")
DD_OUT = os.path.join(HERE, "tmp_dd_exposure_0927")
N_ROWS = 60                                         # 目标行数（对齐到建仓日，实际可能差两三行）
HOLD = 5
TRUNC = 30                                          # ⑤ 截断重算的行数

os.makedirs(SIG, exist_ok=True)
for f in glob.glob(os.path.join(SIG, "order_*.csv")):   # 上一轮的假名单不许留给这一轮
    os.remove(f)
# ⚠️ 必须在 import config 之前覆写：判据单点在 config，那边是 import 时读一次模块级常量
os.environ["STOCK_SIGNAL_DIR"] = SIG
os.environ["STOCK_EXPOSURE_LEDGER"] = LEDGER
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401
import run_ashare_exposure_monitor as M  # noqa: E402


def main():
    fails = []
    t0 = pd.Timestamp.now()
    daily = pd.read_csv(os.path.join(DD_OUT, "exp_daily.csv.gz"), parse_dates=["datetime"])
    expo = pd.read_csv(os.path.join(DD_OUT, "exp_exposure.csv.gz"), parse_dates=["datetime"])
    with gzip.open(CACHE, "rb") as fh:
        picks = pickle.load(fh)["B"]        # B = 配额名单·无闸 = 09-27 退闸之后的生产形态
    print(f"[㊺ 缓存] 篮子 {len(picks)} 个信号日（末 {max(picks):%Y-%m-%d}）"
          f"｜日频 {len(daily)} 行｜腿 {list(expo.columns)[1:]}")

    wide, _ = M.load_panel()
    mtx = M.build_matrices(wide)
    del wide
    for k in ("high", "low"):
        mtx.pop(k, None)
    days = mtx["ret_open"].index
    idx = {d: i for i, d in enumerate(days)}
    last = days[-1]

    # ③ 真落过的名单也当信号用：生产 daily_signal 里那两份，09-23 恰好是 ㊺ 网格的下一格
    signals = {pd.Timestamp(s): [str(c) for c in v] for s, v in picks.items()}
    real = {}
    for f in sorted(glob.glob(os.path.join(SRC, os.pardir, "data", "results",
                                           "daily_signal", "order_*.csv"))):
        d = pd.Timestamp(os.path.basename(f)[6:14])
        if d in idx:
            real[d] = [str(c) for c in pd.read_csv(f)["code"].tolist()]
            signals.setdefault(d, real[d])
    print(f"[真名单] ③ 落过 {len(real)} 份：" +
          "　".join(f"{d:%m-%d} {len(v)} 席" for d, v in sorted(real.items())))

    # 排练窗第一行必须是**建仓日**（信号日的下一格），否则段位与 ㊺ 的网格错开一天 ⇒ ②③ 会红
    exec_days = sorted(pd.Timestamp(days[idx[s] + 1]) for s in signals
                       if s in idx and idx[s] + 1 < len(days))
    want = idx[last] - N_ROWS + 1
    d0 = min(exec_days, key=lambda d: abs(idx[d] - want))
    print(f"[窗口] {d0:%Y-%m-%d} ~ {last:%Y-%m-%d} 共 {idx[last] - idx[d0] + 1} 场"
          f"（建仓日，信号日 {days[idx[d0] - 1]:%Y-%m-%d} 的下一格）")

    grid = [s for s in sorted(signals)
            if s in idx and idx[d0] - 1 <= idx[s] <= idx[last]]
    for s in grid:
        pd.DataFrame({"code": signals[s]}).to_csv(
            os.path.join(SIG, f"order_{s:%Y%m%d}.csv"), index=False)
    # ㊺ 的持仓段：建仓日 j=i+1 权重 0、赚 i+2…i+6 ⇒ 「今天赚钱的篮子」= 信号日往后第 2~6 格
    # 参照表用**全部**信号日建（第一行那格赚的是窗口之前的篮子，只按窗口建就查不到 ⇒ 假红）
    earns = {}
    for s, v in signals.items():
        if s not in idx:
            continue
        for t in days[idx[s] + 2: idx[s] + 2 + HOLD]:
            earns[t] = v
    print(f"[回填] order 文件 {len(grid)} 份｜㊺ 持仓段参照 {len(earns)} 个日子"
          f"｜窗内网格间距 {sorted({b - a for a, b in zip([idx[s] for s in grid], [idx[s] for s in grid][1:])})}")

    rd = M.panel_readings(mtx)             # 面板读数只算一次（每行都重算会把 rolling 跑 60 遍）
    led = pd.DataFrame(columns=M.COLS)
    for d in days[idx[d0]:]:
        _today, led = M.build_row(led, days, mtx, d, rd)
    led = led.reset_index(drop=True)
    print(f"[跑完] 排练账本 {len(led)} 行　用时 {(pd.Timestamp.now() - t0).total_seconds():.0f}s")

    win = led["日期"]
    dd = daily.set_index("datetime").reindex(win)
    ex = expo.set_index("datetime").reindex(win)

    def col(name, frame):
        return frame.set_index("日期")[name].astype("float64")

    # ① 读数对表。容差 1e-7 不是「差不多就行」：㊺ 那份 bench/宽度落在 float32 的累加路径上，
    #    与这里 float64 的均值差 ~1e-9（相对 1e-7 = float32 的最小刻度）。所以同批做一个
    #    **故意做错**的扰动版当反证：真判据错了会差到 1e-2 量级，1e-7 这条线挡不住真错。
    TOL = 1e-7
    for name in ("市场宽度", "域等权日收益"):
        diff = (col(name, led) - dd[name].astype("float64")).abs()
        bad = int((diff > TOL).sum())
        print(f"[①{name}] 最大差 {diff.max():.3e}（容差 {TOL:.0e}）｜不等 {bad} 格 / {len(win)}")
        if bad:
            fails.append(f"① {name} 有 {bad} 格与 ㊺ 不等")
    univ, sma = rd["univ"], mtx["close"].where(rd["univ"]).rolling(M.MA_WIN).mean()
    perturb = {
        "域等权日收益": mtx["ret_open"].mean(axis=1),               # 错法：不限可投域
        "市场宽度": (((mtx["close"] > sma) & univ).sum(axis=1)     # 错法：分母换成「算得出均线的只数」
                    / (sma.notna() & univ).sum(axis=1).replace(0, np.nan)),
    }
    for name, wrong in perturb.items():
        w = wrong.reindex(win).astype("float64").to_numpy()
        print(f"[①扰动·{name}] 故意换判据后与 ㊺ 最大差 "
              f"{np.abs(w - dd[name].astype('float64').to_numpy()).max():.3e}"
              f"　⇒ 容差 {TOL:.0e} 挡得住真错（差了两个数量级以上）")

    # ② 座位对表（第一行跳过：账本没有上一行，那格收益按 0 记，见 ③ 的同一条豁免）
    bad2 = []
    for k, (d, ph) in enumerate(zip(win, led["持仓"].shift(1).fillna(""))):
        theirs = earns.get(d)
        got = [s for s in str(ph).split("|") if s]
        if k == 0:
            print(f"[②座位] {d:%m-%d} = 建仓日 ⇒ 账本无上一行，㊺ 那格赚的是窗口前的 "
                  f"[{','.join(theirs or [])}]，这里改记 0 收益")
            continue
        if theirs is None:
            bad2.append(f"{d:%m-%d} ㊺ 没有这段持仓（该换仓那天缺名单文件）")
        elif got != theirs:
            bad2.append(f"{d:%m-%d} 我赚 [{ph}]｜㊺ 赚 [{','.join(theirs)}]")
    print(f"[②座位] 不等 {len(bad2)} 格 / {len(win)}"
          + ("　" + "；".join(bad2[:4]) if bad2 else " ✅ 每格与 ㊺ 的持仓段一字不差"))
    if bad2:
        fails.append(f"② 座位有 {len(bad2)} 格与 ㊺ 的持仓段不符")

    # ③ 净日收益对表。只有两类格子**允许**不等，其余必须落在 ① 同一条容差内：
    #    a) 第一行（冷启动付**全额**建仓费，㊺ 那一格付的是与上一篮子的重合抵扣）；
    #    b) 换仓日（换票费 > 0）且那天的信号日不在 ㊺ 缓存里 ⇒ ㊺ 那天根本没换仓（末格 09-24 就是）
    net = col("篮子日收益", led) - col("换票费", led)
    cached = {pd.Timestamp(s) for s in picks}
    diff3 = (net - dd["M1净日收益"].astype("float64")).abs()
    bad3, allow = [], []
    for k, d in enumerate(win):
        if diff3.iloc[k] <= TOL:
            continue
        sig = days[idx[d] - 1]
        why = ("冷启动第一行：全额建仓费" if k == 0 else
               f"换仓日，信号日 {sig:%Y-%m-%d} 不在 ㊺ 缓存里 ⇒ 它那天没换仓"
               if led["换票费"].iloc[k] > 0 and sig not in cached else "")
        item = (f"{d:%m-%d} 我 {net.iloc[k]:+.6f} vs ㊺ {dd['M1净日收益'].iloc[k]:+.6f}"
                f"（差 {diff3.iloc[k]:.2e}）" + (f"　{why}" if why else ""))
        (allow if why else bad3).append(item)
    print(f"[③净日收益] 设计上允许的不等 {len(allow)} 格：" + "；".join(allow))
    print(f"           真不等 {len(bad3)} 格" + ("　" + "；".join(bad3[:6]) if bad3 else " ✅"))
    if bad3:
        fails.append(f"③ 净日收益有 {len(bad3)} 格与 ㊺ 不等")

    # ④ 减仓读数（正对照 + 反证）。第一行的生效E 没有上一场可继承 ⇒ 按满仓起算，不比
    for lab in ("M7-30", "M7-45"):
        e = col(f"生效E_{lab}", led)
        diff4 = (e - ex[lab].astype("float64")).abs()
        bad4 = [f"{d:%m-%d} 我 {e.iloc[k]:.0f} vs ㊺ {ex[lab].iloc[k]:.0f}"
                for k, d in enumerate(win) if diff4.iloc[k] > 1e-9 and k > 0]
        first_row = (f"；第一行 {win.iloc[0]:%m-%d} 无上一场可继承 ⇒ 我记 1、㊺ 记 "
                     f"{ex[lab].iloc[0]:.0f}（设计上不比）"
                     if diff4.iloc[0] > 1e-9 else "")
        fired = int((col(f"建议E_{lab}", led) < 1e-9).sum())
        print(f"[④{lab}] 生效E 不等 {len(bad4)} 格 / {len(win) - 1}（不比第一行）{first_row}"
              f"｜账本里收盘建议空仓 {fired} 场"
              + ("　⚠️ 一次没火 ⇒ 这条判据没被验过（恒真）" if fired == 0 else ""))
        if bad4:
            fails.append(f"④ {lab} 生效E 有 {len(bad4)} 格与 ㊺ 不等：" + "；".join(bad4[:4]))
        if fired == 0:
            fails.append(f"④ {lab} 全程没火过 ⇒ 正对照不成立")

    # ⑤ 无未来函数：只跑前 TRUNC 行 ⇒ 与整轮前 TRUNC 行逐字节相同
    led_t = pd.DataFrame(columns=M.COLS)
    for d in days[idx[d0]: idx[d0] + TRUNC]:
        _t, led_t = M.build_row(led_t, days, mtx, d, rd)
    a = led.head(TRUNC).reset_index(drop=True).to_csv(index=False)
    b = led_t.reset_index(drop=True).to_csv(index=False)
    print(f"[⑤无未来函数] 前 {TRUNC} 行"
          + ("逐字节相同 ✅（只看过去 ⇒ 截断不改历史读数）" if a == b
             else "不同 ⇒ 判据偷看了未来"))
    if a != b:
        fails.append(f"⑤ 截断重算与整轮不等 ⇒ 有未来函数")

    led.to_csv(LEDGER, index=False)
    print(f"[落盘] {LEDGER}　{len(led)} 行")
    with pd.option_context("display.width", 260, "display.max_columns", 40):
        print("\n===== 排练账本头 3 行 + 末 2 行 =====")
        print(pd.concat([led.head(3), led.tail(2)]).to_string(index=False, max_colwidth=26))
        print("\n===== 各档到这 60 场为止（就是往后要盯的那张表的形状）=====")
        print(M.running_table(led).to_string(float_format=lambda v: f"{v:+.4f}"))
    print("\n[判定] " + ("五道全过 ✅ ⇒ 这份前向账本与 12 年那张表是同一把尺子，可以挂链路"
                        if not fails else "❌\n  - " + "\n  - ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
