# -*- coding: utf-8 -*-
"""股票线日更链路：一条命令走完「快照 → bin → 面板 → 名单 → 次日真账」，并做跨步验收（09-24 #114）

为什么要串（㉒ 的遗留那句）：这四步此前**全靠手敲**，而它有两个已经实测过的失败模式
——② 漏 `QLIB_PROVIDER_URI` 会死在读日历那一行、② 用系统 python3.10 会因为没有 qlib 而起不来。
手工四步就会天天撞这两下；顺序也不是可以随意调换的：② 读 ① 写出的日历末格，③ 读 ② 写出的
`daily_pv.h5`（`config.ASHARE_DAILY_H5`），所以 ①→②→③ 是**硬依赖**，不是习惯。

四步与各自的解释器（这是链路最容易出错的地方，全部在这里钉死一次）

    ① `data/update_qlib_bin_daily.py`            /usr/bin/python3.10
       当日 bulk 快照 append 进本线 qlib bin。自带四道闸（**场次已经发生**=休市日/
       未来场次直接拒、且在抓数之前、15:00 收盘闸、昨收↔bin 末格
       对齐率 ≥98%、代码前缀白名单）+ 幂等（日历已含该场次就退出）+ 写前备份。
       休市日跑这一场不是错误：它拒在第一个字节之前，bin 末格本来就已等于最近一场。
       缓存复用处也上同一把尺子（09-25 加）：目录里**已经躺着**的脏快照（钉着未来
       场次名的、改名拷进来的）先抽样对表「昨收↔bin 末格」，不过就重取——收盘行
       占比只判得出「是不是收盘后抓的」，判不出「是哪一天的」。
    ② `common/rdagent_docker/pregen_source_data.py`   conda run -n rdagent python
       重生成 `daily_pv.h5`。**必须**带 `QLIB_PROVIDER_URI=<本线 RDAGENT_QLIB_PROVIDER>`：
       缺了它默认落到 cwd，`FileNotFoundError: calendars/day.txt`，死在读陈旧度检查、
       一行字节都不写（所以失败是干净的，面板不会被污染）。
       它自己的复用判据是三条**同时**成立（`pregen_source_data.py:131`）：
       面板末格 >= 日历末格、无缺别名列、面板股数 >= `instruments/all.txt` 股数。
       实测今天（09-24）是 `instruments=6101/6161` 第三条不满足 ⇒ **每天走全量重生成**
       （重写 0.8GB；耗时没有常量：命令行 120s、页面起的两遍自报 36s 与 81s，差的是页缓存冷热
       加上当时机器上还有谁在跑批），这一步慢不是 bug，是那 60 只没进面板的票在咬判据
       （#80 那条 instruments/新上市日更欠账的可见后果）。所以别指望 ② 会被跳过。
    ③ `run_ashare_daily_signal.py`               /usr/bin/python3.10
       出 `signal/buy/order/meta_YYYYMMDD`。不带任何 `STOCK_*` 覆写 = 生产路径。
    ④ `run_ashare_daily_audit.py`                /usr/bin/python3.10
       把 ③ 那三张产物对回**外部快照**：昨日名单今天买不买得到、近涨停闸挡掉的那批
       今天出不出账（跨场次汇总表，每天自己长一行）。只读。
       （第 5 步是看板：streamlit 每次交互重跑 `app.py`，**不需要重启**，见 ㉒。）

该跑哪一场：从**交易所日历**往前看一格（09-25 选项F，取代旧的「从快照文件名反推」）

    `decide_session(bin 末格)` 问 `pending_session` = 交易所日历里 bin 末格的**下一个交易日**，
    判据不抄第二份 —— 直接 import ① 的 `next_trade_day`（惰性 import：那个模块被导入时
    会把全局 socket 超时改成 90s，只在真要取日历的这一天付这个副作用）。
    下一场 <= 今天 ⇒ 起 ①；下一场 > 今天（休市/周末）⇒ 跳过 ①、②③④ 照旧以 bin 末格跑。
    日历取不到（akshare 挂了/断网）⇒ **⚠️ 明说判不出该不该补 ①**，① 不起，后面三步照跑，
    沿用「源不可达就沿用旧数据并自报」那把规矩，不把一次网络抖动报成整链失败。
    为什么换掉旧判据：`spot_*.csv` **只有 ① 会创建** ⇒ 拿产物倒推「该跑哪一场」在正常
    交易日永远倒推回昨天，链路只会说「① 跳过」然后拿昨天那场重跑 ②③④，**bin 一格也推不动**，
    而下面那四项跨步验收**照样全过**（它验的是「名单用的日期 == 日历末格」，验不出「日历该不该多一格」）。

验收放在链路里而不是靠人读日志（每个检查点都从产物派生，不写死日期）

    ① 后：bin 日历末格 == 交易所日历里那一场的场次（只在上面判定「该补」时才有这一步）
    ② 后：这一步要么说了 `wrote` 要么说了 `reuse`（两者都没说 = 认不出它干了什么，停）；
          且 `daily_pv.h5` 的 mtime 不早于 `calendars/day.txt`
    ③ 后：`meta.signal_date` == 场次、`meta.panel_end` == 场次、`meta.tradable_gate`
          == 进程档位、`meta.buy.expr` == 进程 `BUY_EXPR`
    ⇒ 这几条合起来就是「今天这份名单是用今天的数据、按现在这套判据算出来的」这句话的
      可执行形式。任一不过 ⇒ 整链停在最后一步并 exit≠0（沿用账本层「带行号 + 整轮不出账」
      的策略），不把半条链的产物留给看板。

**这条链路不含三条研究评估入口**（`run_ashare_factor_eval` / `run_ashare_portfolio_eval` /
`run_ashare_redundancy_check`）。它们默认写 `data/results/`，不带 `STOCK_*_OUT` 覆写就会用
「末格多一天」去覆盖 ⑳ 那套 board 归档基线（主表 39×25 / 分年度 468×4 / 剔除 13×17，是历轮
A/B 的对照组）⇒ 一天末格不构成重跑理由，要跑得手工带覆写。

重复跑：① 会自己拒「同一场第二次」（幂等闸），而本入口在**前置体检**里就已经用同一把
日历尺子算出「这一场已经在库里了」⇒ 直接跳过 ①（这样「① 手敲过了、剩下的交给链路」也是
通的）；② 会重写同一份内容的面板（见上），③④ 覆盖写当日产物（④ 本身只读、一个字不写），
都不产生第二个版本的账。

用法（收盘后，15:00 之后）
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_daily_chain.py
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_daily_chain.py --dry-run    # 只跑 ① 且只算不写；① 无事可做（休市/已补过/日历取不到）时也停
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_daily_chain.py --no-audit   # 跳过 ④
    # ① 写坏了要还原：
    cd stock/v1/src && /usr/bin/python3.10 data/update_qlib_bin_daily.py --rollback
"""
import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import pandas as pd

from config import (ASHARE_SIGNAL_DIR, ASHARE_TRADABLE_GATE,
                    LOG_DIR, RDAGENT_CONDA_ENV, RDAGENT_OUTPUT_DIR, RDAGENT_QLIB_PROVIDER)
from ashare_screen import BUY_EXPR

SRC = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(SRC, "..", "..", ".."))
P310 = "/usr/bin/python3.10"
PREGEN_PY = os.path.join(REPO, "common", "rdagent_docker", "pregen_source_data.py")
H5 = os.path.join(RDAGENT_OUTPUT_DIR, "git_ignore_folder",
                  "factor_implementation_source_data", "daily_pv.h5")
DAY_TXT = os.path.join(RDAGENT_QLIB_PROVIDER, "calendars", "day.txt")
# 有人按了「取消」⇒ 这个文件出现，链路在**下一步开始之前**看到它就停（看板页写它，
# 见 app.py 的 chain_panel；命令行起的同一场也认这一个文件，锁与取消都只认落盘的事实）
CANCEL_FLAG = os.path.join(LOG_DIR, "chain_cancel.request")


def check_cancel(stage):
    """步与步之间看一眼有没有人请求中止；有就删标记、退出码 130、**不再起下一步**

    为什么不是「立刻把进程杀掉」：这四步里 ① 在按票 append bin、② 在重写 0.8GB 面板、
    ③ 在覆盖当日三份名单 —— 拦腰打断留下的都是**半成品**，而这条链从 ㉔ 起的全部价值
    就是「要么整条走完、要么不把半条链的产物留给看板」。所以这里「取消」的语义是
    **不再起下一步**，正在跑的那一步会把它自己的验收跑完。等下一格的代价最长是一步的
    实测时长（② 36~120s、③ 43~79s、④ 42~61s），不是一整天。
    """
    if not os.path.exists(CANCEL_FLAG):
        return
    got = os.path.getmtime(CANCEL_FLAG)
    os.remove(CANCEL_FLAG)
    raise SystemExit(
        f"[已按请求中止] {stage} 之前看到取消请求（{time.strftime('%m-%d %H:%M:%S', time.localtime(got))}"
        f"由 {CANCEL_FLAG} 写下）⇒ 这一步**没有起**，前面已跑完的步骤产物照旧有效。\n"
        f"要继续这一场：再点一次按钮（或直接命令行跑 `{P310} {os.path.abspath(__file__)}`）；"
        "15:00 之前会被 ① 的收盘闸拒掉。")


def find_conda():
    """conda 可执行文件：PATH 优先，其次两个常见安装位（与 official 循环同一找法）"""
    c = shutil.which("conda")
    if c:
        return c
    home = os.path.expanduser("~")
    for p in (os.path.join(home, "miniconda3", "bin", "conda"),
              os.path.join(home, "anaconda3", "bin", "conda")):
        if os.path.exists(p):
            return p
    return None


def calendar_end():
    if not os.path.exists(DAY_TXT):
        return None
    with open(DAY_TXT) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    return lines[-1] if lines else None


def pending_session(cal_end):
    """该补的那一场 = 交易所日历里 `cal_end` 的**下一个交易日**（09-25 选项F）

    为什么不看快照文件名（旧判据 `latest_session()`）：那些 `spot_*.csv` **只有 ① 会创建**
    ⇒ 用产物反推「该跑哪一场」，在正常交易日永远推回昨天那一天，链路就只会说「① 跳过」、
    拿昨天那场重跑 ②③④，**bin 一格也推不动**，而四项跨步验收照样全过
    （`meta.signal_date == session` 在场次本身来自产物时没有牙）。改成问同一张交易所
    日历，① 才第一次能被这条链自己唤起。

    判据**不抄第二份**：直接 import ① 的 `next_trade_day`（惰性 import——那个模块
    被导入时会把全局 socket 超时改成 90s，只在真要取日历的这一天才付这个副作用）。
    """
    sys.path.insert(0, os.path.join(SRC, "data"))
    from update_qlib_bin_daily import next_trade_day
    return next_trade_day(cal_end)


def decide_session(cal_end, today=None):
    """⇒ (日历里的下一场 pending, 该不该由链路补这一场 advance, 取不到日历时的那句 ⚠️)

    拆成函数只为一件事：这条判据在休市的晚上**没法用真字节验**（① 的收盘闸与场次闸
    都会把它拒在第一个字节之前），但判据本身可以照实算 —— 把 `today` 换成上一个交易日、
    `cal_end` 换成再前一天，就看它会不会给出 `advance=True`。旧判据（从 `spot_*.csv`
    倒推场次）永远给不出这个读数，这正是选项F 要买回来的那一条。
    """
    today = pd.Timestamp(today if today is not None else pd.Timestamp.now().normalize())
    try:
        pending = pending_session(cal_end)
    except SystemExit as e:          # 那张表里没有末日之后的日子（表本身落后了）
        return None, False, f"交易所日历里没有 {cal_end} 之后的日子（{e}）"
    except Exception as e:           # 取不到那张表（网络/接口挂了）
        return None, False, f"取不到交易所日历 {type(e).__name__}: {e}"
    return pending, bool(pd.Timestamp(pending) <= today), ""


def run(step, cmd, env=None, cwd=SRC):
    """跑一步，**输出照抄给用户**并留在返回值里供验收解析；非零退出即整链停"""
    print(f"\n──────── {step} ────────\n$ " + " ".join(cmd))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
    print((r.stdout or "").rstrip())
    print(f"[{step}] exit={r.returncode} 用时 {time.time() - t0:.0f}s")
    if r.returncode != 0:
        raise SystemExit(f"[链路中断] {step} 非零退出 ⇒ 后面的步骤不跑（半条链的产物不留给看板）。"
                         f"上面是那一层的原始输出。")
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description="股票线日更链路：①bin → ②面板 → ③名单 → ④次日真账")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑 ① 且只算不写，然后停；若 ① 本来就无事可做也停"
                         "（②③④ 会写生产路径，排练档不会偷偷升级成真跑）")
    ap.add_argument("--no-audit", action="store_true", help="跳过 ④（次日真账）")
    a = ap.parse_args()

    have = calendar_end()
    if not have:
        raise SystemExit(f"[前置体检] 读不到 {DAY_TXT} ⇒ bin 日历是空的，链路不动字节")
    today = pd.Timestamp.now().normalize()
    print(f"[前置体检] bin 日历末格 = {have}　今天 = {today:%Y-%m-%d}　"
          f"provider = {RDAGENT_QLIB_PROVIDER}")
    if have.replace("-", "") > today.strftime("%Y%m%d"):
        raise SystemExit(f"[前置体检] bin 日历末格 {have} 晚于今天 ⇒ 有哪一步把没发生的日子写进了库，"
                         "链路一个字节都不动（先查 ① 与它的 --rollback）")
    pending, advance, unknown = decide_session(have)
    if unknown:
        print(f"[前置体检] ⚠️ {unknown}\n  ⇒ **判不出该不该补 ①**（这不是"
              "「今天没有该更的一场」，是「不知道」）⇒ ① 不起，②③④ 照旧以 bin 末格 "
              f"{have} 跑；要补这一场请手工跑一次 ①。")
    advance = pending is not None and pd.Timestamp(pending) <= today
    print("[前置体检] 交易所日历的下一场 = "
          + (pending if pending else "未知")
          + ("　⇒ ① **该补**：这一步会把 bin 往前推一格" if advance else
             "　⇒ ① **不起**：这一场还没发生（休市/周末，或 bin 末格已是最近一场）" if pending else
             f"　⇒ ① 不起，后面三步以 bin 末格 {have} 跑"))

    # ① 快照 → bin
    if advance:
        check_cancel("① 快照→bin")
        cmd = [P310, os.path.join("data", "update_qlib_bin_daily.py")]
        if a.dry_run:
            cmd.append("--dry-run")
        run("① 当日日线 append 进 qlib bin", cmd)
        if a.dry_run:
            print("\n[停在这里] --dry-run 只跑 ①：bin 没动字节，②③④ 也就没有新数据可传导。"
                  "\n真跑就去掉 --dry-run；① 写坏了用 "
                  f"`{P310} data/update_qlib_bin_daily.py --rollback` 还原。")
            return
        end = calendar_end()
        if end != pending:
            raise SystemExit(f"[验收失败] ① 之后日历末格 = {end}，不等于该补的那一场 {pending}")
        print(f"[验收 ①] 日历末格 = {end} == 交易所日历的下一场 ✅")
        session = end.replace("-", "")
    else:
        session = have.replace("-", "")
        if unknown:
            print(f"[① 跳过] 判不出该不该补（上面那条 ⚠️）⇒ ① 不起，②③④ 以 bin 末格 {have} 照跑")
        else:
            print(f"[① 跳过] 交易所日历里 {have} 的下一场是 {pending}，**还没发生** ⇒ bin 末格保持 {have}"
                  "（要手工补这一场就跑一次 ①；同一场跑第二遍会被 ① 自己的幂等闸拒掉）")
        if a.dry_run:
            # ① 没得跑不等于整条链都可以跑：②③④ 读的是已经落库的数据，照跑就会
            # 重写面板与当日名单 —— 那正是 --dry-run 承诺不动的两个字节。排练档在
            # 「没有该更的一场」这一刻就该停，而不是偷偷升级成真跑。
            print("\n[停在这里] --dry-run 且 ① 无事可做 ⇒ ②③④ 一律不跑（它们会写生产路径）。"
                  f"\n要按当前判据重出 {have} 那份名单，就直接跑 ③："
                  f"`{P310} {os.path.join(SRC, 'run_ashare_daily_signal.py')}`")
            return

    # ② bin → 面板 h5（这一步用 rdagent conda 环境：系统 python3.10 没有 qlib）
    check_cancel("② 重生成面板")
    conda = find_conda()
    if not conda:
        raise SystemExit("[② 起不来] 没找到 conda ⇒ 面板没法重生成。"
                         f"手工补：QLIB_PROVIDER_URI={RDAGENT_QLIB_PROVIDER} conda run -n "
                         f"{RDAGENT_CONDA_ENV} python {PREGEN_PY} {RDAGENT_OUTPUT_DIR}")
    env = dict(os.environ, QLIB_PROVIDER_URI=RDAGENT_QLIB_PROVIDER)
    out = run("② 重生成 daily_pv.h5（rdagent conda 环境）",
              [conda, "run", "--no-capture-output", "-n", RDAGENT_CONDA_ENV,
               "python", PREGEN_PY, RDAGENT_OUTPUT_DIR], env=env)
    if "wrote" not in out and "reuse" not in out:
        raise SystemExit("[验收失败] ② 既没写新面板也没说复用 ⇒ 认不出这一步干了什么，停")
    if os.path.exists(H5) and os.path.getmtime(H5) < os.path.getmtime(DAY_TXT):
        raise SystemExit("[验收失败] ② 之后 h5 比日历还旧 ⇒ 面板没跟着 bin 往前走，停")
    self_rep = [ln for ln in out.splitlines() if "end=" in ln]
    print("[② 自报] " + (self_rep[0].strip() if self_rep else "（这一步没自报 end=）"))

    # ③ 面板 → 日频名单（生产路径：不带任何 STOCK_* 覆写）
    check_cancel("③ 出当日名单")
    run("③ 出当日名单 signal/buy/order", [P310, os.path.join(SRC, "run_ashare_daily_signal.py")])
    mp = os.path.join(ASHARE_SIGNAL_DIR, f"meta_{session}.json")
    if not os.path.exists(mp):
        raise SystemExit(f"[验收失败] ③ 之后没有 {mp} ⇒ 当日名单没落到预期路径，停")
    meta = json.load(open(mp))
    diffs = [f"signal_date={meta['signal_date']!r}≠{session}"
             if str(meta["signal_date"]).replace("-", "") != session else "",
             f"panel_end={meta['panel_end']!r}≠{session}"
             if str(meta["panel_end"]).replace("-", "") != session else "",
             f"gate={meta.get('tradable_gate')!r}≠进程 {ASHARE_TRADABLE_GATE!r}"
             if meta.get("tradable_gate") != ASHARE_TRADABLE_GATE else "",
             f"buy.expr={meta['buy']['expr']!r}≠进程 {BUY_EXPR!r}"
             if meta["buy"]["expr"] != BUY_EXPR else ""]
    diffs = [d for d in diffs if d]
    print("[验收 ③] " + ("场次、面板末格、闸门档、排序轴四项全对 ✅" if not diffs
                         else "⚠️ " + "；".join(diffs) + "　⇒ 名单是旧数据或旧判据，别按它下单"))
    if diffs:
        raise SystemExit("[验收失败] ③ 的自报口径与当前进程不一致（上面列了差在哪）")
    n = {k: sum(1 for _ in open(os.path.join(ASHARE_SIGNAL_DIR, f"{k}_{session}.csv"))) - 1
         for k in ("signal", "buy", "order")}
    print(f"[产物 ③] " + "　".join(f"{k} {v} 行" for k, v in n.items()) +
          f"　漏斗 {meta['buy']['n_step1']}→{meta['buy']['n_traded']}→"
          f"{meta['buy']['n_traded'] - meta['buy']['n_chase']}→{meta['buy']['n_cand']}")

    # ④ 名单 → 次日真账（只读，把昨天的名单对回今天的外部快照）
    check_cancel("④ 次日真账")
    if a.no_audit:
        print("[④ 跳过] --no-audit")
    else:
        run("④ 次日真账（名单可成交性 + 近涨停闸反事实）",
            [P310, os.path.join(SRC, "run_ashare_daily_audit.py")])

    print(f"\n[链路完成] 场次 {session}　看板不用重启（streamlit 每次交互重跑 app.py），"
          f"直接看 {pd.Timestamp(f'{session[:4]}-{session[4:6]}-{session[6:]}').date()} 那份名单")


if __name__ == "__main__":
    main()
