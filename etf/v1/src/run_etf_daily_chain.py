# -*- coding: utf-8 -*-
"""ETF 线日更链路：一条命令走完「新鲜度体检 → 三个数据面贴新行 → 〔分析面九步，可选〕→ 反馈闭环」，并做跨步验收（09-28 #53，09-29 加 ③ 与 --audit）

为什么要串（现状是从产物反推的，不是猜的）
------------------------------------------
ETF 线此前有四个各自为政的时钟，`scheduler.py` 里那四条 job 又没有进程在跑：
    `common/data/etf/universe_all/`   871 只全市场镜像   ← 只靠手敲 `common/src/data/etf/update_etf_daily.py`
    `common/data/etf/cache/*_daily`   主线池逐只缓存     ← 同一个脚本第二段
    `common/data/etf/risk/`           份额/净值长表（3 张）← 同一个脚本内联调 `collect_daily()`
    `data/results/*_daily` 回测三张表              ← 只靠周一 09:00 那条 `main.py`（实测 52 分钟）
三面数据面 `update_etf_daily.py` 已经串成一条命令（本链直接复用它，不重写第二份判据），
缺的是**外面那一层**：该不该补、补完有没有真推进、失败了算谁的、能不能中途停、
重复跑会不会记双份账。这四件事在股票线的 `run_ashare_daily_chain.py` 里是同一个形状，
这里按 ETF 自己的数据面重写（不跨线 import：两线口径文字与判据按约定各自独立）。

09-29 又补了第二层缺口：`main.py`（分析面九步）今天**只能靠人单独敲**，而它是归档
三张表与看板净值的唯一来源 ⇒ 谁手敲一次 `python main.py`，数据面和反馈面就悄悄没跑；
反过来谁只敲日更，分析面就一年比一年旧。所以这里加了 **③（`--with-analysis`，默认关）**
和**只读盘点 `--audit`**：`--audit` 不需要跑任何东西，它只看产物的字节级读数
（mtime / 行数 / 末日 / 末值），当场念出"哪一块过期了、被谁的新度盖过去了"。

四步与解释器（最容易出错的地方，一次钉死）
------------------------------------------
    体检  `update_etf_daily.freshness_report()`        /usr/bin/python3.10，**只读**
          按市场报三个面的末日与「批内落后」。**这一格量的不是"落后到今天几天"**：
          它的 `end` 就是从镜像自己 871 个文件里取的最大日期
          （`data/data_loader.py:50` `_mirror_batch_end()`），所以持有批末的那个市场
          定义上报 0，镜像整体停摆一个月它同样报 0 ⇒ 它只念"沪深两面齐不齐"。
          **该不该补 ① 由 `decide_sessions()` 逐张价面判**：问 `akshare.tool_trade_date_hist_sina()`
          （交易所日历，惰性取一次），每张面各取"晚于自己末日的第一个交易日"作 pending，
          pending ≤ 今天 ⇒ 这一张该补；任一张该补 ⇒ ① 起；四张都已到位 ⇒ 停手（`--force` 才强走）；
          取不到表 ⇒ ⚠️ 明说"不知道"（这不是"没有该补的"），① 在收盘闸之后照起——它自己的判据
          不依赖日历。判据不抄产物倒推，这条是从股票线 09-25 选项F 借的**形状**，
          本线自己实现（两线口径文字与代码不互相 import）。
    ①     `data/update_etf_daily.py`                  /usr/bin/python3.10，≈110s（871 只实测）
          镜像 + 主线池缓存逐只 append，再内联 `fetch_etf_risk_panel.collect_daily()`
          （风险面板失败只打 ⚠️、不阻断，见 :297-303）。判据本体是它的 `append_tail()`：
          重叠段收盘 rel ≤ 1e-6 才贴、再加一道成交量量纲闸（手/股差 100×），
          **只贴末日之后的新行、历史一格不动**，临时文件 + `os.replace` 原子写。
    ③     `run_daily_backtest.py`（= `main.main()`，九步 [1/9]~[9/9]）**默认不起**，
          要 `--with-analysis` 才起。它是 `data/results/` 那批归档表的唯一写者：
          覆写 `equity/trades/signals/dsr` 的 `*_daily.csv` **加**同名固定文件
          （`main.py:730-758`）、`walk_forward_daily.csv`、`pbo_result.json`、
          `multi_*.csv`、`shap_timeline.csv`、`optimized_params_daily.json`、
          `equity_daily.png`；`[4/9]` 还会 upsert 因子库并打一个 `[factor-lib]`
          git commit，`[9/9]` 的 `AUTO_REMINING` 有条件踢出容器重挖（几小时级）。
          实测代价（从产物与日志反推）：09-28 那场 22:04:15→22:34:35 = **30 分 20 秒**，
          其中 [8/9] 走查+PBO+多策略 18.5 分、[9/9] 的 LLM SHAP 7.5 分；
          09-27 另一场 52 分钟 ⇒ 墙钟由"RD-Agent 前置检查过不过"决定，别拿均值排程。
          ⚠️ 10-01 补一刀：**前置检查绿 ≠ 那一腿真起了容器**——同一条链路上 b) 闸念的是
          ✅ 2/2（bin/面板都新鲜），而共享层逐项体检里的 `docker info` 单次 20s 超时
          ⇒ 那一腿还是静默产出 0、墙钟 1h17m。探测已改成 2 次 × 45s（甲-1），
          缺席则由验收 G 格把链路打成非零（甲-2）。
          **起之前链路先读三张闸**（`analysis_gate_readings()`，三条都能红）：
            a) 重挖冷却：`common/data/etf/cache/remining_state.json` 的
               `current_bar − last_remining_bar < cooldown_bars` ⇒ 不在冷却就**拒起**
               （要起得再加 `--allow-remining`），因为那一支会把 30 分钟变成几小时；
            b) RD-Agent 数据新鲜度：判据**调用官方那一把** `core.official_rdagent
               ._rdagent_data_checks`，不在这儿重抄一份 mtime 比较——09-29 重抄时就把
               方向写反过）：`bin/面板 的 mtime ≥ 行情源目录最新 mtime` 才算跟上。
               **落后 ⇒ 前置检查挡死 official 支（产出 0）⇒ ③ 只花 30 分钟**（09-28 那场）；
               跟上 ⇒ 容器循环会起 ⇒ 墙钟是小时级（#47）。
               ⇒ 这一张闸**念完之后、③ 真起之前**链路会把两块产物各重建一遍
               （`rebuild_rdagent_data_surface()`，实测 28.9 秒，并再念一次重建后的闸）：
               这道闸比的是 mtime，而 ① 每天重写 871 只镜像 ⇒ 只要不重建它就**结构性恒红**，
               official 那一支就永远产出 0（#47 定档「丙」＝常态化重建）；
            c) LLM 端点可达性：不可达不是崩，是降级（三次重试后走模板兜底）。
          **它不可幂等**：`trial_counter.json` 每场只增（DSR 的分母），SHAP 正文每次
          由 LLM 重写 ⇒ 同一份数据重跑两遍，归档**不该**逐字节相同。链路的验收因此
          不去追"和上一场一样"，只追"这一场真的写了、且历史没缩水、末值自洽"
          （`verify_analysis()`，见下）。
    ②     `run_feedback.py --auto --llm daily --self-eval --monthly-days 1`
          /usr/bin/python3.10，**同一串 argv 两次实测 268.7s（09-27）与 511s（09-28
          首跑）** ⇒ 墙钟由 LLM 冷热决定，别拿均值排程。
          写 `report/`、`data/live/feedback_report.json`，外加 `--auto` 那一支：它把
          config.py **复制**一份到 `data/config_backups/`（实测 09-25 那份 18KB、
          `versions.json` 恒 `[]`）。为什么复制了却没改：`ConfigUpdater.update_param`
          是按行首常量名正则改写源码的（`common/src/feedback/config_updater.py:34`），
          而本线 config 里**没有 `SLIPPAGE`/`COMMISSION_RATE` 这两个名字** ⇒ 匹配不到、
          返回 False、判据一个字符没动（T6 在 config 副本上正反两支都验过：负支 False
          且副本逐字节不变，正支用真实存在的 `MIN_COMMISSION` 证它不是恒 False）。
          不碰 `data/results/`、不碰因子库、不 git。
          LLM 端点不可达时是**降级不是崩**：三次重试后走模板兜底、照常落盘、退出码 0。
          这一步**故意不做硬闸**（非零只打 ⚠️）：日报与自评是"给看板多一行读数"，
          它算不出不该把已经贴好的日线打回。`--no-feedback` 跳过。

跨步验收（全部从产物反推，不写死日期）
--------------------------------------
    ① 前/后各读一次 `freshness_report()` ⇒ 任何一面的末日**不得倒退**；
       末日没变则分两种：源本来就没有更新的一行（T+1 还没出，正常）vs 该面根本
       是 0 只/读不出来（异常）。链路把两种分开念，不把"没推进"一律报成失败。
       收尾再问一次日历 ⇒ 「价面是否追平」：还差一格就明说差哪一格，不含糊。
    ③ 后（`--with-analysis` 起过时）：对那批产物（4 张核心 + 8 张副，清单就是
       `CORE_ARTIFACTS`／`AUX_ARTIFACTS`，不在这里抄第二份数字）做**字节级**对表
       （`stamp_all()` / `verify_analysis()`）。这一格的来历是 09-29 的一条教训：
       **"没跑"与"跑对"必须给出不同的读数**，否则验收是恒真的。所以每条判据都取
       mtime/行数/末值这种只有真写过才会动的量：
         A 至少 `signals/equity/trades/dsr` 四张的 mtime 晚于起链时刻
           ⇒ main.py 崩在半路时这一条会红（产物停在上一场）；
         B `equity_daily.csv` 行数不得比上一场**少**（回测区间只该长不该缩）；I（10-06 裁「补」）＝`signals`／`trades` 两张表不许塌方（覆盖的交易日少一格或行数跌破上一场一半⇒红，日常换血实测 −3.7%/−12.2% 只念不拦）；
         C `signals_daily.csv` 与 `equity_daily.csv` 末日必须**同一天**（同一次生成）；
         D 日志回显的「最终资金」必须等于 `equity_daily.csv` 的末值（跨层对表：
           一个念的是终端、一个念的是文件，写坏了必红）；
         E `trial_counter.json` 只许变大（它是累计格，缩了说明被谁重置）；
         F 有日期列的那几张表**末日不得倒退**（一张都读不出日期列时判红，不判绿——
           `all([])` 恒真是条恒真判据，09-29 加）；
         G **official 这条腿真的交出了因子**（10-01 加）：主线日志里那一行
           「official 产出 N 个因子」若 N=0 —— 无论是链路自陈「本次未运行」还是
           一声不响地交了白卷 —— 判红 ⇒ ② 不起、全场非零退出。
           来历：10-01 15:5x 那场 `docker info` 单次 20s 探测超时 ⇒ 这一腿静默产出 0、
           共享层照打 ✅、子进程与链路退出码都是 0，墙钟 1h17m（真起容器是 3~5 小时）
           ⇒ "腿没了但全场绿"这一类此前只有验收脚本在盯、链路自己没有闸。
           产出行整条不存在时判 ⚪（`--resume` 命中主线断点就不重算、不重印，不该打红）；
           确实要让这一腿缺席还往下走：`--allow-official-absent`（只把 G 降成 ⚪）。
         H **official 交回的那批里有本场新写的**（10-05 乙-3 加）：主线念了「产出 N 个」
           （N>0）而共享层那行「本场净增 M」念的是 M=0 ⇒ 判红 ⇒ ② 不起、全场非零退出。
           来历：回收读的是固定路径 `factors.json`，没有新鲜度也没有名字作用域 ⇒
           10-04 19:05 那场链路第一次全程打通（rc=0、5 个 loop、6 枚因子全对账），
           交回的 12 条却**全是上一场的旧档**（净增 0），而日志照打 ✅。
           10-05 那笔量账（只读，脚本在 `temp/bill_official_reharvest_1005.py`）：
           这种重复在试验台账上留下 36 条次，摘干净后 DSR 0.004713→0.005180，
           门槛 0.95、n_trials 退到 1 也只有 0.8565 ⇒ **判决任何一档都不翻**，实盘层这 6 条
           旧档零席位 ⇒ 这一格拦的是"旧档念成产出"这个读数骗局，不改数据流。
           没有可比对象（N 缺失或 N=0，那是 G 的活）、或那行「本场净增」压根没念
           （10-04 之前的共享层 / 起场前没有基线）⇒ 判 ⚪ 只念不拦，无读数不等于坏读数；
           确实要让"本场挖不出新的"还往下走：`--allow-official-stale`（只把 H 降成 ⚪）。
         J **回收层点名的净增候选，每一个都真的在因子库键里**（10-06 C-1 加，**只报不拦**）：
           起场前/收场后各读一次 `rdagent_output/factors.json` 的名字集合与库的键集合，
           本场新出现的候选名若不在库键里 ⇒ 这一格渲染成 ❌，但它是 `REPORT_ONLY_RED` 哨兵
           ⇒ **不进红名单、不改退出码、② 照起**（用户裁「第②步：只报」）。
           来历：10-06 那场「H 绿」而库层 official 净增 0——回收层交回 4 条 official，
           全部是库里的老名字，唯一那枚新名字重算后 |IC|＝0.0029186 死在地板甲下面。
           历史七场回放里这道闸要红 **4/7＝57%**，所以它天生不是拦停的东西；它给的是
           "官方这条腿本场到底有没有把新东西送进库"这个此前**没有任何一格在数**的读数。
           两条读数禁令（都是量出来的）：**不许**拿日志那行 `📚 因子库更新: +N` 当净增
           （N 是 `upsert` 返回 true 的行数，老名字追加历史也算 true），**不许**读库里那列
           `source`（同一键会被后写的腿盖掉，见 `factor_library.py:75`）⇒ 只能取键集合前后差。
           缺取数（候选或库键读不出）、本场候选净增 0 个名字（那一半归 H）⇒ 判 ⚪。
           与这一格同批落地的是**候选归档**（`archive_official_candidates()`，只追加不覆写，
           落 `<线>/data/archive/official_candidates/`）：没归档就没法给红的那几场定性。
       这几条只判"这场真的写了、且写出来的东西自洽"，**不判**新旧两场数值相同：
       ③ 天然不可幂等（`trial_counter` 递增、SHAP 正文由 LLM 现生成、因子库若被
       并行会话改过则因子集会换），所以"重跑一遍数字一模一样"不是这条链的判据。
    ② 后：`data/results/signals_daily.csv` 的末日 vs 镜像末日 ⇒ 报**分析面落后几个
       交易日**。这一格**默认日更链不修**：不带 `--with-analysis` 时能刷新它的只有
       单独敲 `main.py`（30~52 分钟，会覆盖归档回测三张表 + upsert 因子库 +
       `[factor-lib]` git commit + 可能踢出自动重挖），那不属于日更。只把滞后念出来，
       别让看板上的"净值止于"被读成"数据止于"。带了 `--with-analysis` 就是**当场修**，
       这一格应当归零；归不了零就说明 ③ 只写了一半（见上面 C 条）。

首跑读数（09-28 17:55 起，全场 11 分钟，exit=0）
------------------------------------------------
    ① 贴入 190 行 = **只有深市 388 只里的 190 只**到了 09-28；沪市 483 只逐只回
       `uptodate`（当日那格源里还没有）⇒ 镜像当场裂成「深@09-28 / 沪@09-24」，
       批内参差 2 个工作日。这就是 #18 那个病的**成因读数**：同一场日更里两个市场
       的源出数时间不同，所以判"该不该补/追平没追平"一律**逐张面**算（
       `price_surface_ends` / `decide_sessions`），取 max 会把它念成"已到位"。
    ② exit=0、511s：日报 + 批量自评 1 天（83.0/A，评法=LLM 实评）；写
       `report/{feedback_report.md, report_daily.md, self_eval_dims.json}` +
       `data/live/{factor_feedback.csv, feedback_report.json, *_20260928.json}`。
       `--auto` 没落任何备份（`data/config_backups/` 仍只有 09-25 那份）：影子盘
       零成交 ⇒ 没有可配对的实盘成交 ⇒ 没有 `suggested_slippage_conservative`，
       那一支根本没被调用（与本线 config 里也没有 `SLIPPAGE` 常量是两道独立保险）。
    分析面 `signals_daily.csv` 仍止于 09-24（比镜像旧 2 个工作日）——链路只念，不修。

刻意**默认不进**这条链的四样东西（09-29 起第一样改成"要就说要"，其余三样照旧）
    `main.py` / `run_daily_backtest.py`：09-28 的初版把它**整个挡在门外**，理由是
       "覆写归档 + 动辄几小时"。09-29 改口：它不能默认起（一天 30~52 分钟、天天重刷
       归档、还会打 `[factor-lib]` commit，这些都不是"贴一行日线"该顺带干的事），
       但**完全没有这一块**同样不对——它就是"单独敲脚本会丢块"的那个块。
       ⇒ 折中：`--with-analysis` 显式起，起则带 A~J 十条字节级验收（J 只报不拦）。
    `data/etf_universe.py` 的 `build()`：候选池"不每天重算"，且它的上市日期抓取是
       420s×12 轮的并发（`etf_universe.py:168-206`），日更里等它等于天天赌十几分钟。
    三条研究评估入口 `run_etf_factor_eval` / `run_etf_portfolio_eval` /
       `run_etf_redundancy_check`：默认覆写 `data/results/` 那七张判重与评估表
       （历轮 A/B 的对照组）。股票线的同类链路也是同样把它们挡在日更之外。
    `run_live.py`：它是常驻循环 + 影子盘；按约定本线**不接自动实盘**，成交一律手工录入。

重复跑：① 的 `append_tail()` 遇到"源里没有更新的行"回 `uptodate`、一行不写 ⇒ 幂等；
② 写的是定名文件与日期快照（`report_daily_YYYYMMDD.json` 同名覆盖）⇒ 同日两遍不出双份账。

用法（收盘后；15:00 之前会被收盘闸拒，见 `close_gate`）
    cd etf/v1/src && /usr/bin/python3.10 run_etf_daily_chain.py
    ... run_etf_daily_chain.py --dry-run          # 只跑体检 + ① 的 --report，一个字节不写
    ... run_etf_daily_chain.py --no-feedback      # 跳过 ②
    ... run_etf_daily_chain.py --skip-risk        # ① 不跑风险面板（透传给 update_etf_daily）
    ... run_etf_daily_chain.py --force            # 绕过收盘闸，也绕过「日历说这一格还没发生」
                                                  #   那道停手（补历史欠账、休市日强出日报时用）
    ... run_etf_daily_chain.py --with-analysis    # 加跑 ③ 分析面（30~52 分钟，覆写归档）
    ... run_etf_daily_chain.py --audit            # **只读**盘点：从产物字节级读数反推
                                                  #   三块各跑在哪一天、谁把谁的新度盖过去了
                                                  #   （= 单独敲过某个脚本、丢了别的块 的检查）
    全链一键（09-29 重跑 09-28 那场用的就是这条）：
        /usr/bin/python3.10 run_etf_daily_chain.py --audit        # 先看缺哪几块
        /usr/bin/python3.10 run_etf_daily_chain.py --with-analysis \
            --no-data --force                                     # 数据面已到位时不重贴
"""
import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

import pandas as pd

from config import BASE_DATA_DIR, CACHE_DIR, LOG_DIR, REPORT_DIR, RESULTS_DIR, RISK_DIR, UNIVERSE_ALL_DIR

SRC = os.path.dirname(os.path.abspath(__file__))
P310 = "/usr/bin/python3.10"
DATA_PY = os.path.normpath(os.path.join(SRC, *[".."] * 3, "common", "src", "data", "etf", "update_etf_daily.py"))
# 有人按了「取消」⇒ 这个文件出现，链路在**下一步开始之前**看到它就停。语义与股票线
# 一致：不拦腰杀正在跑的那一步（① 正在逐只贴 871 个文件、② 正在落日报），打断留下
# 的都是半成品；这里"取消"= 不再起下一步。
CANCEL_FLAG = os.path.join(LOG_DIR, "etf_chain_cancel.request")
LOCK_PATTERN = "run_etf_daily_chain.py"
CLOSE_HHMM = (15, 0)      # 收盘时刻：ETF 现货 15:00 收盘


def close_gate(now=None):
    """⇒ (放行吗, 那句要念的话)

    为什么链路要有这道闸而 `update_etf_daily.py` 自己没有：`append_tail()` 的口径
    判据是**重叠段**收盘价逐格对表，而"今天这根还没收盘的 bar"在镜像里根本没有对应
    日期 ⇒ 不在重叠段里 ⇒ 一路畅通被贴进去，之后再也不会被修正（股票线那条
    "台阶永久"的同款性质）。所以盘中抓到什么就是什么。补历史欠账是另一件事，
    带 `--force` 照走。"""
    now = now or pd.Timestamp.now()
    if (now.hour, now.minute) < CLOSE_HHMM:
        return False, (f"现在 {now:%H:%M}，未到 15:00 收盘 ⇒ 当日 bar 还没成形，"
                       "盘中源给的是未完成价，贴进镜像就永久留着"
                       "（`append_tail` 只比重叠段，新行没有可比的重叠）")
    return True, f"现在 {now:%H:%M}，已过收盘"


def find_running():
    """从 /proc 数「还有几个本链路在跑」——不记 pid 文件（进程被 kill 后文件会骗人）

    匹配不能只看命令行最后一个 token：带 `--dry-run` 起时最后一个是开关而不是脚本名，
    第一版就是这么漏判的。改成逐个 token 解析成绝对路径再比。
    再一道筛：argv[0] 必须是 python——否则 `bash -c "python run_etf_daily_chain.py"`
    这种包装行会被当成"另一个实例在跑"，把第一次起链的自己拒掉。"""
    mine = os.path.abspath(__file__)
    hits = []
    for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except Exception:
            continue
        if LOCK_PATTERN not in cmd or int(pid) == os.getpid():
            continue
        toks = cmd.split()
        if not toks or "python" not in os.path.basename(toks[0]).lower():
            continue
        if any(os.path.abspath(t) == mine or
               t.rsplit("/", 1)[-1] == LOCK_PATTERN for t in toks[1:]):
            hits.append(pid)
    return hits


def check_cancel(stage):
    if not os.path.exists(CANCEL_FLAG):
        return
    got = os.path.getmtime(CANCEL_FLAG)
    os.remove(CANCEL_FLAG)
    raise SystemExit(
        f"[已按请求中止] {stage} 之前看到取消请求（{time.strftime('%m-%d %H:%M:%S', time.localtime(got))}"
        f"由 {CANCEL_FLAG} 写下）⇒ 这一步**没有起**，前面已跑完的步骤产物照旧有效。\n"
        f"要继续这一场：再跑一次 `{P310} {os.path.abspath(__file__)}`。")


def run(step, cmd, soft=False, extra_env=None):
    """跑一步，输出**逐行**透传（不是等它跑完才吐）；非零退出默认整链停（`soft=True` 只打 ⚠️）

    为什么要逐行：③ 一步就是 30~52 分钟，而老写法把子进程 stdout 整块攒在内存里、
    跑完才 print ⇒ 重定向出去的链路日志在那段时间**一行都不涨**，看守的人只能靠
    「字节数没变」猜它是卡了还是在跑（09-28 那次 11 分钟的链路就被这么误判过一回，
    只不过那次的病根是块缓冲）。现在改成边读边打，链路日志的字节数就是活的心跳。
    """
    print(f"\n──────── {step} ────────\n$ "
          + "".join(f"{k}={v} " for k, v in (extra_env or {}).items())
          + " ".join(cmd))
    t0 = time.time()
    lines = []
    p = subprocess.Popen(cmd, cwd=SRC, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1,
                         # 子进程自己也得别块缓冲，否则我这边逐行读到的还是"一坨最后到达"
                         env=dict(os.environ, **(extra_env or {}), PYTHONUNBUFFERED="1"))
    for line in p.stdout:
        lines.append(line)
        print(line, end="", flush=True)
    rc = p.wait()
    out = "".join(lines)
    print(f"[{step}] exit={rc} 用时 {time.time() - t0:.0f}s"
          f"（{len(lines)} 行透传）")
    if rc != 0:
        msg = (f"[链路中断] {step} 非零退出 ⇒ 后面的步骤不跑。上面是那一层的原始输出。")
        if soft:
            print("[⚠️ 不阻断] " + msg.replace("[链路中断] ", ""))
            return None
        raise SystemExit(msg)
    return out


def read_ends():
    """三面末日 ⇒ {数据面|市场: (末日, 落后工作日)}，判据单点复用 `freshness_report()`"""
    sys.path.insert(0, os.path.join(SRC, "data"))
    from update_etf_daily import freshness_report
    df = freshness_report()
    if df.empty:
        raise SystemExit("[前置体检] `freshness_report()` 一行都没读出来 ⇒ "
                         f"{UNIVERSE_ALL_DIR} 里没有 *_daily.csv？链路不动字节")
    df["末日"] = df["末日"].astype(str)
    out, key = {}, "数据面"
    for _, row in df.iterrows():
        out[f"{row[key]}|{row['市场']}"] = (row["末日"],
                                            int(row["落后交易日"]), int(row["只数"]))
    return out


def mirror_lag(ends):
    """镜像两个市场之间的**批内**参差（末日相对批末落后几个工作日，取最坏）

    ⚠️ 这不是"落后到今天几天"。`freshness_report()` 的 `end` 就是从镜像自己的 871
    个文件里取的最大日期（`data/data_loader.py:50` 的 `_mirror_batch_end()`），
    所以持有批末的那个市场**定义上**报 0，镜像整体停摆一个月它也报 0。这一格只
    用来念"沪深两面齐不齐"，**不许**拿来决定该不该补 ⇒ 那条判据在
    `decide_sessions()`，它逐张面问交易所日历。"""
    lags = [v[1] for k, v in ends.items() if k.startswith("全市场镜像")]
    return max(lags) if lags else None


_TRADE_DAYS = None


def trade_days():
    """交易所交易日历（惰性取一次，进程内共用）⇒ `DatetimeIndex`

    为什么链路要自己去问这张表（09-28，#53 实测撞出来的）：日更链的第一问是"今天
    有没有该补的一格"，而唯一的现成读数「落后交易日」量的是批内参差（见
    `mirror_lag`），拿它当闸就等于拿一个常常恒 0 的数当闸——dry-run 会在每一个
    日子都说"无事可做"。判"该不该补"必须问日历，不能问产物。

    返回 `DatetimeIndex` 而不是 Series：Series 的 `d in s` 查的是**行号**不是值。
    """
    global _TRADE_DAYS
    if _TRADE_DAYS is None:
        import akshare as ak
        _TRADE_DAYS = pd.DatetimeIndex(
            pd.to_datetime(ak.tool_trade_date_hist_sina()["trade_date"]))
    return _TRADE_DAYS


def price_surface_ends(ends):
    """每张价面（全市场镜像 / 主线池缓存，沪市与深市**各算一张**）的末日
    ⇒ [(面名, 末日)]，按面名排序

    为什么逐张算、不并成一个数（09-28 真跑撞出来的）：那一场 ① 只推进了深市 190 只
    （沪市 483 只源里还没有当日那格），镜像当场变成「深@09-28 / 沪@09-24」。取 max
    ⇒ 链路念"价面末日 09-28、已到位"，把 #18 那个按市场滞后的病**盖在判决词里**；
    取 min ⇒ 只说最坏的、说不清是谁落后。逐张问日历才既报得出"该补"又报得出"谁没补上"。

    刻意不含风险面板那三张长表：它们的时效性本来就是另一套（深市份额只回 3 个月、
    沪市是月末快照、净值只有当日），拿它当基准会把每一个工作日都判成"该补"。
    """
    return [(k, v[0]) for k, v in sorted(ends.items())
            if k.startswith(("全市场镜像", "主线池缓存"))]


def decide_sessions(ends, today=None):
    """逐张价面问交易所日历 ⇒ ({面: (该补哪一场 pending, 该不该补 advance)}, ⚠️)

    每张面自己的三支读数，缺一支就说明判据没牙：
      pending ≤ today ⇒ advance=True ⇒ **这一张该补**
      pending > today ⇒ advance=False ⇒ 这一张已到位（下一场还没发生）
      取不到日历      ⇒ 空读数 + ⚠️  ⇒ 这是"**不知道**"，不是"没有该补的"

    拆成函数只为一条测试：休市日当天没法用真字节验 advance=True（① 的收盘闸会把它
    拒在第一个字节之前），但把 `today` 和基准日期换着注两个数就能照实算出来。而
    "两张面一张到位一张落后"这一支只能靠**注入**的假 ends 验——真跑到它的那天，
    ① 恰好会把它抹平（09-28 就是这么撞上又只撞一次）。

    `pending` = 日历里**晚于**该面末日的第一个交易日，一次只补一格；欠几天就跑几天。
    """
    surfaces = price_surface_ends(ends)
    if not surfaces:
        return {}, f"读不到任何价面的末日（{list(ends)[:3]}…）"
    today = pd.Timestamp(today if today is not None
                         else pd.Timestamp.now().normalize())
    try:
        days = trade_days()
        out = {}
        for name, d in surfaces:
            after = days[days > pd.Timestamp(d)]
            if not len(after):
                return {}, (f"交易所日历里没有 {d}（{name} 的末日）之后的交易日"
                            "⇒ 那张表本身落后了")
            out[name] = (after[0].strftime("%Y-%m-%d"), bool(after[0] <= today))
        return out, ""
    except Exception as e:
        return {}, f"取不到交易所日历 {type(e).__name__}: {e}"


def backtest_lag_days(ends):
    """分析面（`signals_daily.csv`）末日比镜像末日**旧**几个工作日 ⇒ (落后, 分析面末日)

    镜像末日直接取 `ends` 里那两条「全市场镜像|沪/深」的最大值，判据单点复用
    `freshness_report()`，不在这里再扫一遍 871 个文件。
    """
    p = os.path.join(RESULTS_DIR, "signals_daily.csv")
    if not os.path.exists(p):
        return None, None
    sig = pd.read_csv(p, encoding="utf-8-sig", usecols=[0])
    sig_end = pd.to_datetime(sig[sig.columns[0]]).max()
    mirror = [pd.Timestamp(v[0]) for k, v in ends.items()
              if k.startswith("全市场镜像")]
    if not mirror:
        return None, sig_end
    mirror_end = max(mirror)
    return int(len(pd.bdate_range(sig_end + pd.Timedelta(days=1), mirror_end))), sig_end


DATA_DIR = os.path.dirname(RESULTS_DIR)   # 本线产物根（results/ library/ live/）
ANALYSIS_PY = os.path.join(SRC, "run_daily_backtest.py")   # = main.main()，freq 由 env 定
REMINE_STATE = os.path.join(CACHE_DIR, "remining_state.json")
# ③ 会写的产物（路径从 main.py 的 stage_validation :372-489、存盘块 :730-758 与
# [9/9] 的分析块反推，不是猜的）。验收只看这四张真写过才会动的量：
# mtime、字节数、行数、末日/末值。
CORE_ARTIFACTS = ["results/signals_daily.csv", "results/equity_daily.csv",
                  "results/trades_daily.csv", "results/dsr_daily.csv"]
AUX_ARTIFACTS = ["results/walk_forward_daily.csv", "results/pbo_result.json",
                 "results/multi_summary.csv", "results/shap_timeline.csv",
                 "results/optimized_params_daily.json",
                 "library/factor_library_index.json",
                 "cache/trial_counter.json",
                 # C-1 甲那份候选账本（10-06 加）：进 AUX 就是为了让「副产物 N/M 张本场被写过」
                 # 那一行数得到它——归档没落成时那一格会自己少一张，不用另加一格。
                 "archive/official_candidates/index.jsonl"]


def stamp(rel, with_tail=True):
    """一个产物的字节级读数 ⇒ dict（不存在也返回，好让验收能念"缺"而不是崩）"""
    # 09-29 起数据分两棵树：cache/ 这类抓来的行情在 BASE_DATA_DIR（common/data/etf），
    # results/ library/ live/ 这些本线结论仍在 DATA_DIR。rel 的首段决定去哪边找。
    root = BASE_DATA_DIR if rel.split(os.sep)[0] in ("cache", "risk",
                                                     "universe_all") else DATA_DIR
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        return {"exists": False}
    st = os.stat(p)
    out = {"exists": True, "mtime": st.st_mtime, "bytes": st.st_size,
           "when": time.strftime("%m-%d %H:%M:%S", time.localtime(st.st_mtime))}
    if not with_tail or not rel.endswith(".csv"):
        return out
    try:
        df = pd.read_csv(p, encoding="utf-8-sig")
        out["rows"] = int(len(df))
        first = df[df.columns[0]]
        # 首列未必是日期：`dsr_daily.csv` 的首列是 `sr_observed`（一个浮点），
        # `walk_forward_daily.csv` 是 `2012-10-26 ~ 2015-08-03 (673 bars)` 这种区间串。
        # 直接 `to_datetime` 会把数值列当 epoch 偏移解成 1970-01-01（验收表念成「末日
        # 倒退回 1970」），还会逐格 fallback 到 dateutil 刷一片 "Could not infer
        # format" 警告。所以先验一眼：认 `YYYY-MM-DD` 开头的列才当日期读。
        head = first.dropna()
        if head.empty:
            pass
        elif pd.api.types.is_numeric_dtype(first) or not re.match(
                r"^\s*\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?\s*$",
                str(head.iloc[0])):
            out["no_date_col"] = f"首列 {df.columns[0]} 不是日期列"
        else:
            dts = pd.to_datetime(first, errors="coerce")
            if dts.notna().any():
                out["last_date"], out["n_dates"] = str(dts.max().date()), int(dts.dropna().nunique())
        for col in ("equity", "dsr"):
            if col in df and len(df) and pd.notna(df[col].iloc[-1]):
                out["last_value"] = float(df[col].iloc[-1])
    except Exception as e:
        out["read_error"] = f"{type(e).__name__}: {e}"
    return out


def stamp_all(keys):
    return {k: stamp(k) for k in keys}


def analysis_gate_readings():
    """③ 起之前的三张闸 ⇒ (读数, 拦路的话)。三条都能红，不是摆设。

    为什么要有 a)：`[9/9]` 的 `AUTO_REMINING` 一旦不被冷却挡住就可能踢出容器重挖，
    那是"几小时"而不是"30 分钟"（#22 那场真触发过、也真挖出过因子），所以链路默认
    把它挡在门外；要起得自己说 `--allow-remining`。
    """
    from config import AUTO_REMINING
    r, block = {}, []
    if os.path.exists(REMINE_STATE):
        st = json.load(open(REMINE_STATE, encoding="utf-8")).get("daily", {})
        cur = int(st.get("current_bar", 0))
        last = int(st.get("last_remining_bar", -(10 ** 9)))
        rounds = int(st.get("remining_count", 0))
        gap = cur - last
        r["remining"] = (f"冷却已过={gap >= AUTO_REMINING['cooldown_bars']} "
                         f"（距上次重挖 {gap} bar / 需 {AUTO_REMINING['cooldown_bars']}）"
                         f"｜轮数 {rounds}/{AUTO_REMINING['max_remining_rounds']}"
                         f"｜上轮 DSR {st.get('last_dsr')} PBO {st.get('last_pbo')}"
                         f"｜更新 {st.get('updated_at')}")
        if (AUTO_REMINING["enabled"]
                and gap >= AUTO_REMINING["cooldown_bars"]
                and rounds < AUTO_REMINING["max_remining_rounds"]):
            block.append(f"重挖冷却**已过**（{gap} ≥ {AUTO_REMINING['cooldown_bars']}）"
                         f"且轮数未满（{rounds}/{AUTO_REMINING['max_remining_rounds']}）"
                         "⇒ ③ 的 [9/9] 有条件踢出容器重挖（几小时级）。"
                         "确认要起就加 `--allow-remining`")
    else:
        r["remining"] = f"没有 {REMINE_STATE} ⇒ 判不出冷却，③ 若 enabled 会从头记第一笔"
        block.append("读不到 re-mining 状态文件 ⇒ 判不出冷却，默认不起 ③"
                     "（确认要起就加 `--allow-remining`）")
    # b) qlib bin / daily_pv.h5 新鲜度：**直接调用官方那道判据**，不在这里重抄一遍
    #    （#47 的教训就是两处各写一份会飘；09-29 我在这一格里就把方向写反过一次）。
    #    它决定 official 支跑不跑 ⇒ 只**预告墙钟**：挡死 ⇒ 30 分钟量级（09-28 那场）；
    #    放行 ⇒ RD-Agent 容器循环起，墙钟是「小时」级。
    try:
        from core.official_rdagent import _rdagent_data_checks
        checks = _rdagent_data_checks(None)
        bad = [c for c in checks if not c[1]]
        if not checks:
            r["bin"] = "官方判据返回空（本线没有 RD-Agent 源目录）⇒ 按「可能真跑挖掘」排时间"
        elif bad:
            r["bin"] = (f"❌ {len(bad)}/{len(checks)} 道落后：" +
                        "、".join(c[0] for c in bad) +
                        "\n            ⇒ 前置检查**挡死 official 支**（产出 0）⇒ ③ 实测 30 分钟量级"
                        "\n            ⇒ 落后详情与重建命令：\n            " +
                        "\n            ".join(f"· {c[2]}" for c in bad))
        else:
            r["bin"] = (f"✅ {len(checks)}/{len(checks)} 道都跟上了行情 ⇒ 前置检查**放行 official 支**"
                        "⇒ RD-Agent 容器循环会起 ⇒ ③ 的墙钟是「小时」级，不是 30 分钟")
    except Exception as e:
        r["bin"] = f"调不到官方判据（{type(e).__name__}: {e}）⇒ 按「可能真跑挖掘」排时间"
    # c) LLM 端点：不可达**不是崩**（三次重试后走模板兜底、退出码 0），所以只念不拦
    base = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE")
            or "http://localhost:11434/v1")
    try:
        import urllib.request
        with urllib.request.urlopen(base.rstrip("/").replace("/v1", "")
                                    + "/api/tags", timeout=4) as resp:
            r["llm"] = f"{base} 可达（HTTP {resp.status}）⇒ SHAP/自评走真 LLM"
    except Exception as e:
        r["llm"] = (f"{base} 不可达（{type(e).__name__}）⇒ [9/9] 与 ② 走模板兜底降级，"
                    "产物照写、退出码照 0（这一格只念，不拦）")
    return r, block


def official_leg_evidence(log_text):
    """从 ③ 的 stdout 里拆出 official（RD-Agent(Q) 容器循环）这条腿的三处自陈。

    只读、不判（判在 `verify_analysis` 的 G 格里）。返回 dict：
      `n`          主线那行「official   产出 N 个因子」的 N；整行不存在 ⇒ None
      `net_new`    主线那几行「本场净增 M 个因子」里 M 的**最大值**（多处就取最大：只要
                   有一行报出过净增 > 0，这一腿就真交过新东西）；一行都没有 ⇒ None
      `net_no_line` 那几行压根不存在（10-04 之前的共享层不念这行）⇒ 无读数，不是坏读数
      `net_no_baseline` 念的是「本场净增无法判定（起场前没有可比基线）」⇒ 同样无读数
      `skip`       链路自陈「本次未运行（前置依赖缺失: X）」里的 X；没有这句 ⇒ None
      `red_items`  前置检查那一屏里 ❌ 的那几条（只吃检查表那一段连着的行，防容器回显混进来）
      `segment_ran` 多源那一段在本线**有没有跑**（认另一条腿的产出行或「多源挖掘追加」）
                   ⇒ 用来把「产出行整条不见」拆成两种：这一段压根没重算（`--resume` 命中，
                   判 ⚪）vs 这一段跑了、唯独这一条腿的字一行都没落（判红，别让它蒙过去）

    为什么只看主线（`--- 折 ` 之前）：折内那一条腿由 `MULTI_SOURCE["official_in_fold"]`
    单独裁（丙-2 = 折内停用，09-30）⇒ 折内缺席是**判决不是事故**，不许和本线的
    "静默 0 产出"并成一笔总账。
    """
    txt = log_text or ""
    fold_at = re.search(r"^--- 折 \d", txt, re.M)
    main = txt[: fold_at.start()] if fold_at else txt
    prod = re.search(r"official\s+产出\s*(\d+)\s*个因子", main)
    # 共享层回收那一段念的「本场净增 M 个因子」（10-04 丁落地才有这一行）；
    # 一次场里可能念多行（每个回收入口各一行），取**最大值**：只要有一行说过净增 > 0，
    # 这一腿就真交过新东西。取末行/取首行都会把"有一段是新的"念成"全是旧的"。
    nets = [int(m) for m in re.findall(r"本场净增\s*(\d+)\s*个因子", main)]
    skip = re.search(r"RD-Agent\(Q\) 本次未运行（前置依赖缺失:\s*(.+)）", main)
    at = main.find("RD-Agent(Q) 前置检查:")
    reds = []
    if at >= 0:
        # 只吃检查表那**连着**的 ✅/❌ 行：往后走一大片是 RD-Agent 容器的回显，
        # 里面的 IC 判定行也带 ❌，拿整段去抠红项会把"全绿但容器真跑了"那一场念成有红项
        for ln in main[at:].splitlines()[1:]:
            if not ln.lstrip().startswith(("✅", "❌")):
                break
            if ln.lstrip().startswith("❌"):
                reds.append(ln.lstrip()[1:].strip())
    return {"n": int(prod.group(1)) if prod else None,
            "net_new": max(nets) if nets else None,
            "net_no_line": not nets and "本场净增" not in main,
            "net_no_baseline": "本场净增无法判定" in main,
            "skip": skip.group(1) if skip else None,
            "red_items": reds, "has_preflight": at >= 0,
            "segment_ran": ("多源挖掘追加" in main
                            or bool(re.search(r"(?:llm|simple|genetic)\s+产出\s*\d+\s*个因子",
                                              main)))}


# C-1（10-06 用户裁「设」＋「第①步：丙＝两半同批」＋「第②步：只报」）的两半都住这里：
# 甲＝`archive_official_candidates()` 每场归档候选清单，乙＝验收表第十格 J 的取数。
REPORT_ONLY_RED = "只报红"      # 渲染成 ❌，但**不进**红名单 ⇒ 拦不住 ②、改不了退出码
# 为什么用哨兵而不是 `False`：全仓「红不红」的口径一律是 `ok is False`（`print_checks`、
# negctl 的 `judge`、H/I 两份夹具的红名单比对都是这一把），哨兵天生混不进去 ⇒ 一颗
# 「只报」的红不需要在任何一份老夹具里改判据，也不会被哪一处当成真红误拦 ②。
OFFICIAL_ARCHIVE_DIR = os.path.join(DATA_DIR, "archive", "official_candidates")


def official_candidates_snapshot():
    """回收层那份候选清单 ⇒ dict{path, names, n, bytes, sha256, src_mtime}，读不出 ⇒ None

    路径清单与取名口径**直接借共享层**（`core.official_rdagent._factor_artifact_paths`
    那三份文件、`name` 缺失时同一个 `official_{i}` 兜底名）⇒ 链路与回收读的是同一棵树，
    不在这儿造第二把尺子（#47 的教训：两处各抄一份就会飘）。

    ⚠️ 与共享层 `_existing_factor_names` 有一处**故意**不同：它把「文件读不出」折成空集
    （那边只影响一行读数），这里必须把 `None`（没有可比对象）与空 frozenset（清单真的是空的）
    分开——否则 J 格会把一片坏读数念成「本场净增 0」，那是把尺子数不到当成数到了。
    """
    from config import RDAGENT_OUTPUT_DIR
    from core.official_rdagent import _factor_artifact_paths
    for path in _factor_artifact_paths(RDAGENT_OUTPUT_DIR):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        return {"path": path,
                "names": frozenset(it.get("name", f"official_{i}")
                                   for i, it in enumerate(data)),
                "n": len(data), "bytes": raw,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "src_mtime": os.path.getmtime(path)}
    return None


def factor_library_keys():
    """库层键集合 ⇒ frozenset，读不到 ⇒ None（J 格第三种状态：没有可比对象，不是 0 个键）

    走 `FactorLibrary` 自己的加载器（`index_path` 从 `FACTOR_LIBRARY` 派生，不写死路径），
    但**每次现起一枚实例**、不用 `get_library()` 那个单例：③ 是子进程写的库，本进程里缓存的
    那份永远是起场前的 ⇒ 拿缓存去算「本场净增了几个键」会恒等于 0。
    """
    from core.factor_library import FactorLibrary
    lib = FactorLibrary()
    if not os.path.exists(lib.index_path):
        return None
    keys = frozenset(lib.factors)
    # `_load_index` 把「JSON 读不出」吞成空表，所以空表与坏文件在这里同一个形状 ⇒ 都当没读数
    return keys or None


def archive_official_candidates(snap=None):
    """每场把官方候选清单整份归档（只追加、永不覆写）⇒ dict{file, sha256, n, skipped, error}

    为什么要这一半（10-06 量 C-1 误杀率时现撞的墙）：七场回放里红 4 场，可其中 3 场
    **分不了类**——那几场的 `factors.json` 已被下一场覆写，全仓只剩一枚快照
    （`temp/snapshot_ding_1005/`）⇒ 没归档就没法说清「红的那几场里哪几场是冤案」。
    闸与归档是一件事的两半，只做前半就上线，那 57% 的误杀率永远查不下去。

    形状：`<线>/data/archive/official_candidates/official_candidates_<起场时刻>_<sha8>.json`
    ＋同目录 `index.jsonl` 每场一行 `{stamp, src_mtime, file, sha256, n, names}`。
    同一份内容（sha 相同）不重写第二份 ⇒ 一场跑两遍不记双份账。**失败不拦路**：
    归档写不成只让 J 的证据行念出 `error`，验收照走（这一半坏了不该把另一半也拖停）。

    ⚠️ `stamp` 是落盘时刻、`src_mtime` 才是那场写完清单的时刻 ⇒ 追账按 `src_mtime` 认场次。
    进 git：`.gitignore:270` 只放行本目录直接子文件，(:257) 的 `*/v1/data/**` 仍挡着其它邻居。
    """
    snap = snap or official_candidates_snapshot()
    if snap is None:
        return {"skipped": "回收层那三份候选文件一份都不在（或读不出）⇒ 无可归档"}
    try:
        os.makedirs(OFFICIAL_ARCHIVE_DIR, exist_ok=True)
        idx = os.path.join(OFFICIAL_ARCHIVE_DIR, "index.jsonl")
        prev = []
        if os.path.exists(idx):
            with open(idx, encoding="utf-8") as f:
                prev = [ln for ln in f.read().splitlines() if ln.strip()]
        for ln in prev:
            try:
                if json.loads(ln).get("sha256") == snap["sha256"]:
                    return {"skipped": f"与已归档的 `{json.loads(ln).get('file')}` 逐字节相同"
                                       f"（sha {snap['sha256'][:8]}）⇒ 不重写",
                            "sha256": snap["sha256"], "n": snap["n"]}
            except Exception:
                continue            # 索引里有一行坏了不影响归档：那是账本，不是判据
        stamp_s = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        fname = f"official_candidates_{stamp_s}_{snap['sha256'][:8]}.json"
        dst = os.path.join(OFFICIAL_ARCHIVE_DIR, fname)
        seq = 0
        while os.path.exists(dst):      # 同一秒里两场：改名字，不覆写
            seq += 1
            dst = os.path.join(OFFICIAL_ARCHIVE_DIR,
                               fname.replace(".json", f"_{seq}.json"))
            fname = os.path.basename(dst)
        tmp = dst + ".tmp"
        with open(tmp, "wb") as f:
            f.write(snap["bytes"])
        os.replace(tmp, dst)            # 原子落盘：与 ① 的 `append_tail` 同一把规矩
        with open(idx, "a", encoding="utf-8") as f:
            f.write(json.dumps({"stamp": stamp_s,
                                "src_mtime": time.strftime(
                                    "%Y-%m-%d %H:%M:%S",
                                    time.localtime(snap["src_mtime"])),
                                "file": fname, "sha256": snap["sha256"],
                                "n": snap["n"], "names": sorted(snap["names"])},
                               ensure_ascii=False) + "\n")
        return {"file": fname, "sha256": snap["sha256"], "n": snap["n"],
                "dir": OFFICIAL_ARCHIVE_DIR, "index_lines": len(prev) + 1}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def verify_analysis(before, after, t_start, log_text,
                    allow_official_absent=False, allow_official_stale=False,
                    official_candidates=None):
    """③ 的字节级跨步验收 ⇒ [(判据名, 过不过, 证据)]，十条 A–J。

    每条都必须"没跑/跑一半"时给不出同一个读数（09-29 的教训：可失败的那一步
    最会藏恒真判据）。**不判**与上一场数值相同——③ 天然不可幂等。

    `allow_official_absent=True` 只把 G 从 ❌ 降成 ⚪（A–F、I 一条不动）：那一腿缺席时
    仍然把字念出来，但不拦 ②。
    `allow_official_stale=True` 只把 H 从 ❌ 降成 ⚪（A–G、I 一条不动）：交回的全是旧档时
    仍然把字念出来，但不拦 ②。
    `official_candidates=None`（默认）⇒ J 判 ⚪；传 dict 才有点名的对象，见下面 J 那一段。
    """
    checks = []
    fresh = [k for k in CORE_ARTIFACTS
             if after[k]["exists"] and after[k]["mtime"] >= t_start]
    checks.append(("A 核心四张产物本场真的被写过",
                   len(fresh) == len(CORE_ARTIFACTS),
                   f"{len(fresh)}/{len(CORE_ARTIFACTS)} 张 mtime 晚于起链时刻："
                   + "、".join(f"{k.split('/')[-1]} {after[k].get('when', '缺表')}"   # 缺表要念「缺」：原先 ['when'] 硬取 ⇒ 一张核心表不见了就 KeyError，整张验收表（含 I 那条「整张表不见了」）根本没机会打出来（10-06 夹具 C5 抓到）
                               for k in CORE_ARTIFACTS)))
    eq_b, eq_a = before["results/equity_daily.csv"], after["results/equity_daily.csv"]
    rows_ok = (eq_a.get("rows") or 0) >= (eq_b.get("rows") or 0)
    checks.append(("B 净值表行数不得比上一场少", rows_ok,
                   f"{eq_b.get('rows')} → {eq_a.get('rows')} 行"))
    same_day = (after["results/signals_daily.csv"].get("last_date")
                == eq_a.get("last_date"))
    checks.append(("C 信号表与净值表末日必须同一天（同一次生成）", same_day,
                   f"signals {after['results/signals_daily.csv'].get('last_date')}"
                   f" vs equity {eq_a.get('last_date')}"))
    m = re.search(r"最终资金\s*:\s*([\d.,]+)", log_text or "")
    lv = eq_a.get("last_value")
    cross = (m is not None and lv is not None
             and abs(float(m.group(1).replace(",", "")) - lv) < 0.01)
    checks.append(("D 日志回显的最终资金 == 净值表末值（跨层对表）", cross,
                   f"日志 {m.group(1) if m else '没抓到'} vs 表 {lv}"))
    tb, ta = before["cache/trial_counter.json"], after["cache/trial_counter.json"]
    grew = (ta.get("exists") and (ta.get("mtime") or 0) >= t_start
            and (ta.get("bytes") or 0) >= (tb.get("bytes") or 0))
    checks.append(("E 试验计数只增不缩（累计格）", grew,
                   f"{tb['bytes']}B → {ta['bytes']}B（{ta['when']}）"))
    # 只比「首列真是日期」的那几张：`dsr_daily.csv` 首列是数值，没有末日可比
    # （09-29 修：原先它被 `to_datetime` 解成 1970-01-01，念起来像"倒退到 1970"）
    dated = [k for k in CORE_ARTIFACTS if before[k].get("last_date")]
    # 一张都没有日期列时判**红**而不是绿：`all([])` 恒真是条恒真判据
    no_regress = bool(dated) and all(
        (after[k].get("last_date") or "0") >= before[k]["last_date"] for k in dated)
    checks.append((f"F 末日不倒退（{len(dated)}/{len(CORE_ARTIFACTS)} 张有日期列的表）",
                   no_regress,
                   "一张表都读不出日期列 ⇒ 无可比对象" if not dated else
                   "、".join(f"{k.split('/')[-1]} {before[k]['last_date']}"
                             f"→{after[k].get('last_date') or '缺表'}" for k in dated)))
    # G official 这条腿：**没跑不许读成跑对**。10-01 15:5x 那场实测——`docker info` 单次
    # 20s 探测超时 ⇒ 前置检查判红 ⇒ 这条腿一个因子没产、共享层还照打「✅ official 产出 0 个因子」、
    # 子进程与整条链的退出码都是 0 ⇒ 墙钟 1h17m（真起容器是 3~5 小时），只有翻日志才发现腿没了。
    # 重试已在共享层加过（甲-1，2 次 × 45s），这一格补的是**退出码**那一半。
    ev = official_leg_evidence(log_text)
    if ev["n"] is None and ev["segment_ran"]:
        g_ok, g_ev = False, ("多源那一段在本线跑了（有另一条腿的产出行或「多源挖掘追加」），"
                             "**唯独 official 那行一个字没落** ⇒ 这一腿是整段失踪，不是产出 0，"
                             "更不是没重算")
    elif ev["n"] is None:
        # 产出行整条不存在 **且** 这一段也没跑：`--resume` 命中主线断点时属正常
        g_ok, g_ev = None, ("主线日志里没有多源那一段的任何产出行 ⇒ 这一段没重算"
                           "（`--resume` 命中主线断点时正常）⇒ 这一格判不出，只念不拦")
    elif ev["n"] == 0:
        g_ok = False
        g_ev = (f"official 产出 0 个因子（前置检查红项 {len(ev['red_items'])} 条"
                + (f"：{ '；'.join(ev['red_items'])[:160]}" if ev["red_items"] else "")
                + (f"；链路自陈未运行＝{ev['skip']}" if ev["skip"] else
                   "；且**没有任何一句自陈** ⇒ 共享层打了 ✅ 却交了白卷"
                   "（真·静默，比上一支更难发现）")
                + f"；本场 llm 等其余各腿照写、子进程退出码照 0 ⇒ 只有这一格在拦）")
    elif ev["skip"]:
        g_ok, g_ev = False, (f"自陈「本次未运行（{ev['skip']}）」却又报产出 {ev['n']} 个"
                             "⇒ 两处对不上，有一处在说谎")
    elif ev["red_items"]:
        g_ok, g_ev = False, (f"前置检查有 {len(ev['red_items'])} 条 ❌"
                             f"（{ev['red_items'][0][:120]}）却产出 {ev['n']} 个 ⇒ 对不上")
    else:
        g_ok, g_ev = True, f"official 产出 {ev['n']} 个因子，前置检查无 ❌、无自陈缺席"
    if g_ok is False and allow_official_absent:
        g_ev = ("--allow-official-absent 已给 ⇒ 只念不拦。" + g_ev)
        g_ok = None
    checks.append(("G official 这条腿真的交出了因子（不是静默 0）", g_ok, g_ev))
    # H official 交回的那 N 个里**有没有本场新写的**（10-05 乙-3，只加读数、不改数据流）。
    # 来历：10-04 19:05→22:21 那场是链路第一次全程打通（rc=0、5 个 loop 走完、6 枚因子的
    # h5 与代码全对账），但 `factors.json` 的**名字净增 0**——共享层 `_recover_factors`
    # 读的是固定路径（factors.json / result.json），既没有新鲜度也没有名字作用域，
    # 所以它交回的是**累计**那批：「official 产出 12 个因子」这 12 条全是上一场留下的旧档。
    # 10-05 量账（只读，`temp/bill_official_reharvest_1005.py`）：这种"同一批名字被反复当新产出"
    # 在试验台账上留下了 36 条次重复试验；把这 36 条摘干净，DSR 只从 0.004713 变成 0.005180，
    # 而门槛是 0.95 —— n_trials 从 429 一路退到 1 也只到 0.8565，**判决在任何一档都不翻**；
    # 实盘层那 6 条旧档按 |IC| 排 15/25/29/49/56/60，一个席位都没占到。
    # ⇒ 这一格拦的不是"数字被污染了多少"，是**"把旧档念成产出"这个读数骗局**本身；
    #   不改返回值、不改写库、不改试验计数（那是乙-2/乙-4，还没裁）。
    # 三态：没有可比对象（G 已在拦）⇒ ⚪；有产出行但净增那行压根没念 / 自陈无法判定 ⇒ ⚪
    # （无读数不等于坏读数）；净增念得出且为 0 ⇒ ❌。
    if ev["n"] is None or ev["n"] == 0:
        h_ok, h_ev = None, (f"official 产出 {ev['n'] if ev['n'] is not None else '（无产出行）'}"
                            " ⇒ 这一格没有可比对象（腿缺席/交白卷那一半由 G 拦，H 不重复拦）")
    elif ev["net_new"] is None and ev["net_no_baseline"]:
        h_ok, h_ev = None, ("共享层自陈「本场净增无法判定（起场前没有可比基线）」"
                           "⇒ 起场前 factors.json 本来就不存在（第一场的形状）⇒ 判不出，只念不拦")
    elif ev["net_new"] is None:
        h_ok, h_ev = None, (f"主线念了产出 {ev['n']} 个，但日志里**一行「本场净增」都没有**"
                           "⇒ 无读数（10-04 之前的共享层不念这行，或这一段被截断了）"
                           "⇒ 判不出，只念不拦；这一格要从 ⚪ 变成有牙，得先有那行字")
    elif ev["net_new"] == 0:
        h_ok, h_ev = False, (f"official 产出 {ev['n']} 个因子，可「本场净增」念的是 **0**"
                            "（全是起场前旧档，本场 0 写入）⇒ 这一腿交回的是**上一场的存货**，"
                            "不是本场挖出来的；它照进 ②、照打 ✅、试验计数照加"
                            "（10-05 量账：这类重复在台账上留了 36 条次）")
    else:
        h_ok, h_ev = True, (f"official 产出 {ev['n']} 个，本场净增 {ev['net_new']} 个"
                            "⇒ 这一腿本场真的写了新名字")
    if h_ok is False and allow_official_stale:
        h_ev = ("--allow-official-stale 已给 ⇒ 只念不拦。" + h_ev)
        h_ok = None
    checks.append(("H official 交回的因子本场净增 > 0（旧档不算产出）", h_ok, h_ev))
    # I `signals`／`trades` 两张表的「塌方」闸（10-06 用户裁「补」）。
    # 来历：10-06 那场真日更里 signals 27,002→25,996（−1,006）、trades 24,583→21,592（−2,991），
    # 而 B 只盯净值表（4,067→4,067）⇒ 这两张表少掉三千行链路上没有任何一格在数。
    # 口径先说死：**减行本身不是事故**。③ 天然不可幂等，逐日换血是设计内的（本文件顶部就写着
    # 「同一份数据重跑两遍，归档不该逐字节相同」），实测那场也确实是换血：日期一张没丢、
    # 每日行数有增有减（signals 905 天少／689 天多，最大 ±9 行；trades 反而多了 9 个日期）。
    # ⇒ 这一格只拦两种**事故形状**：①覆盖的交易日少了一格（尾巴被截／某段崩在半路）；
    #   ②行数跌破上一场的一半（只剩骨架）。
    # ⚠️ 界的来历要说清：那两场对照（−3.7%／−12.2%）是**单场观测**，不是多样本分位数。
    #   留的是"腰斩才算"这一档余量——它拦得住截断，拦不住温和缩水；要收紧要另起一场量分布。
    # 三态：两张表都没有上一场（首场或表原本不存在）⇒ ⚪ 无基线不拦。
    bad_i, parts_i, no_base_i = [], [], []
    for key_i in ("results/signals_daily.csv", "results/trades_daily.csv"):
        nm_i = key_i.split("/")[-1]
        b_i, a_i = before.get(key_i) or {}, after.get(key_i) or {}
        if not b_i.get("exists"):
            no_base_i.append(nm_i)
            continue
        if not a_i.get("exists"):
            bad_i.append(f"{nm_i} 上一场有、本场整张表不见了")
            parts_i.append(f"{nm_i} {b_i.get('rows')}→缺表")
            continue
        parts_i.append(f"{nm_i} {b_i.get('rows')}→{a_i.get('rows')} 行、"
                       f"日期 {b_i.get('n_dates')}→{a_i.get('n_dates')} 格")
        if b_i.get("n_dates") is not None and a_i.get("n_dates") is not None \
                and a_i["n_dates"] < b_i["n_dates"]:
            bad_i.append(f"{nm_i} 覆盖的交易日少 {b_i['n_dates'] - a_i['n_dates']} 格"
                         f"（{b_i['n_dates']}→{a_i['n_dates']}）")
        if b_i.get("rows") and a_i.get("rows") is not None \
                and a_i["rows"] * 2 < b_i["rows"]:
            bad_i.append(f"{nm_i} 行数跌破上一场的一半（{b_i['rows']}→{a_i['rows']}）")
    if bad_i:
        i_ok, i_ev = False, "、".join(bad_i) + "｜" + "；".join(parts_i)
    elif len(no_base_i) == 2:
        i_ok, i_ev = None, ("两张表都没有上一场可比基线（首场或表原本不存在）"
                           "⇒ 判不出，只念不拦")
    else:
        i_ok, i_ev = True, ("没撞到「丢交易日」或「行数腰斩」两种事故形状；" + "；".join(parts_i)
                            + ("；无基线跳过：" + "、".join(no_base_i) if no_base_i else ""))
    checks.append(("I 信号表/成交表没有塌方（交易日不丢格、行数不腰斩）", i_ok, i_ev))
    # J 库层这道闸（10-06 用户裁「C-1：设」→「第①步：丙＝两半同批／第②步：只报」）。
    # 口径先说死：**这一格只报**。它的红是 `REPORT_ONLY_RED` 哨兵 ⇒ `print_checks` 渲染成 ❌
    # 却不写进红名单 ⇒ ② 照起、退出码照 0。历史回放里它要红 4/7＝57%，接成拦停就是每天
    # 多一道门；那三场冤案能不能定性，靠的是甲那一半（`archive_official_candidates`）。
    # 读数两条禁令（都是量出来的，不是设计的）：
    #   · **不许**念日志那行 `📚 因子库更新: +N`——N 是逐条 `upsert` 返回 true 的行数，
    #     老名字追加历史也算 true（`factor_library.py:91-94`）；10-06 那场实测日志念
    #     +4／+3／+11，而库层真净增＝10 个键、其中新增自 official 的 0 个 ⇒ 只能取键集合的前后差。
    #   · **不许**读库里那一列 `source`——同一个键的 source 会被后写的那条腿盖掉
    #     （`main.py:337` 的 `extra={"source": src}` → `factor_library.py:75`），而
    #     `batch_upsert` 根本不传 extra ⇒ source 记的是「最后谁碰过它」，不是「谁造的」。
    # 「不在库里」不等于坏：候选要连过地板甲／判重／聚类三道闸才进库（10-06 复刻：17 条候选
    # 死 4／5／4、只 4 条交回）。这一格点名的正是**这道漏斗把回收层自认的新东西吃掉了几个**。
    oc = official_candidates or {}
    cb, ca = oc.get("before"), oc.get("after")
    lib_after, lib_before, arc = oc.get("library_after"), oc.get("library_before"), oc.get("archive")
    if not isinstance(arc, dict):
        arc_ev = "候选归档：本场没试（调用方没给取数）"
    elif arc.get("error"):
        arc_ev = f"候选归档：❌ 写坏了（{arc['error']}）⇒ 这一场的候选清单过后没法复刻"
    elif arc.get("skipped"):
        arc_ev = f"候选归档：{arc['skipped']}"
    else:
        arc_ev = (f"候选归档：`{arc['file']}`（sha {arc['sha256'][:8]}／{arc['n']} 条"
                  f"／index 第 {arc['index_lines']} 行）")
    if cb is None or ca is None or lib_after is None:
        j_ok, j_ev = None, (
            "这一格缺取数（" + "、".join(
                f"{lab}={len(val) if val is not None else '缺'}"
                for lab, val in (("候选前", cb), ("候选后", ca), ("库键", lib_after))
                if val is None) +
            f"）⇒ 判不出，只念不拦（缺读数不等于坏读数，与 H 同一口径）；{arc_ev}")
    else:
        net = sorted(set(ca) - set(cb))
        keys_net = ("" if lib_before is None else
                    f"；同期库键净增 {len(set(lib_after) - set(lib_before))} 个")
        if not net:
            j_ok, j_ev = None, (f"回收层候选本场净增 0 个名字（{len(cb)}→{len(ca)} 条）"
                                "⇒ 这一格没有点名的对象（「交回的全是旧档」那一半由 H 拦）"
                                f"；库共 {len(lib_after)} 键{keys_net}；{arc_ev}")
        else:
            missing = [n for n in net if n not in set(lib_after)]
            listed = "、".join(f"`{n}`" for n in missing[:5])
            if len(missing) > 5:
                listed += f"…共 {len(missing)} 个"
            if missing:
                j_ok, j_ev = REPORT_ONLY_RED, (
                    f"回收层点名的净增 {len(net)} 个候选里 **{len(missing)} 个不在库键**"
                    f"（{listed}）⇒ 只报，不拦 ②。不在库里不等于坏：它们多半死在地板甲／判重／"
                    f"聚类三道闸里（10-06 复刻 17 条候选死 4／5／4），要定性就复刻下面那份归档"
                    f"；候选本场 {len(cb)}→{len(ca)} 条，库共 {len(lib_after)} 键{keys_net}；{arc_ev}")
            else:
                j_ok, j_ev = True, (
                    f"回收层点名的净增 {len(net)} 个候选**全部在库键里**"
                    f"（{'、'.join(f'`{n}`' for n in net[:5])}）；候选本场 {len(cb)}→{len(ca)} 条，"
                    f"库共 {len(lib_after)} 键{keys_net}；{arc_ev}")
    checks.append(("J 回收层点名的净增候选，每一个都在因子库键里（只报，不拦 ②）", j_ok, j_ev))
    return checks


def rebuild_rdagent_data_surface():
    """③ 起之前把 RD-Agent 的两块数据面（qlib bin + `daily_pv.h5`）重建一遍 ⇒ 让新鲜度闸有机会绿

    为什么要链路来干（#47，09-29 定档「丙」＝常态化重建）：`core.official_rdagent
    ._rdagent_data_checks` 比的是 **mtime**，而 ① 每天把 871 只镜像 csv 整批重写一遍 ⇒
    哪怕行情末日一格没变，mtime 一翻新这道闸就判红；判红不是崩，是**静默跳过** official
    那一支（打印「本次未运行」后产出 0 个因子）。等这一支长期跑不了 —— 09-27 起连场产出 0，09-29 那场 4 个折全部「产出 0 个因子」。

    实测代价（09-29 15:25 本机一场真跑）：a) `dump_qlib_bin.py` **14 秒**（871 只 /
    950,589 行，日历推到 09-28）+ b) `pregen_source_data.py` **15 秒**（含 conda 冷启动；
    面板 951,589 行 × 12 列，末行 09-28）⇒ 合计 **28.9 秒**，换来 official 支从
    "恒产出 0"变成"真起容器循环"（墙钟从 30 分钟跳到小时级）。

    为什么放在 ③ 之前而不是 ① 之后：不带 `--with-analysis` 的日常日更根本不跑
    RD-Agent，没必要天天烧这 35 秒；而 pregen 写的正是 `rdagent_output/` 底下的面板，
    必须赶在 ③ 那批容器起来**之前**写完，并行会互相盖产物。

    失败不拦路：dump 挂了（比如基准指数抓不到）或 conda 不在，都由紧接着那张 b) 闸
    照实念红，链路不替它遮。**判据仍是官方那一把**，这里不造第二把尺子。
    """
    from config import RDAGENT_SOURCE_DIR
    if not RDAGENT_SOURCE_DIR or not os.path.isdir(RDAGENT_SOURCE_DIR):
        print("[重建 RD-Agent 数据面] 本线没有行情源目录 ⇒ 官方判据整组跳过，一个字节不动")
        return
    import core.official_rdagent as off
    # 两块产物的重建入口都从**官方那份源码同一批常量**里取，不写死路径：
    # 写死过一次就在 #47 上栽过（两处各抄一份会飘）。
    dump_py = os.path.join(os.path.dirname(DATA_PY), "dump_qlib_bin.py")
    pregen_py = os.path.normpath(os.path.join(
        SRC, *[".."] * 3, "common", "rdagent_docker", "pregen_source_data.py"))
    run("③ 前置重建 a) qlib bin（全市场镜像 → calendars/features/instruments）",
        [P310, dump_py], soft=True)
    conda = off._find_conda()
    if not conda:
        print("[⚠️ 不阻断] PATH 与常见安装位都没有 conda ⇒ `daily_pv.h5` 造不出来，"
              "official 支会被新鲜度闸挡死（产出 0）")
        return
    run("③ 前置重建 b) 源数据面板 daily_pv.h5（rdagent 环境，写进 rdagent_output/）",
        [conda, "run", "--no-capture-output", "-n", off.RDAGENT_CONDA_ENV,
         "python", pregen_py, off.RDAGENT_OUTPUT_DIR],
        soft=True, extra_env={"QLIB_PROVIDER_URI": off.RDAGENT_QLIB_PROVIDER})
    # 重建之后**照实再念一遍**：念的还是官方那一把尺子，不自己下结论。
    # 为什么不能省：pregen 有一条"面板内容已经够新就整块跳过、连名都不换"的复用规则
    # （`pregen_source_data.py:143-149`），跳过 ⇒ h5 的 mtime 不翻新 ⇒ 这道 mtime 闸照红。
    # 09-29 实测面板日历只到 09-24 而镜像已贴到 09-28，所以本场走的是全量重算；
    # 真要撞上"日历 == bin 末交易日"，这里会念出来，而不是让调用侧以为重建成功了。
    try:
        checks = off._rdagent_data_checks(None)
        bad = [c for c in checks if not c[1]]
        if not checks:
            print("  [重建之后] 官方判据返回空 ⇒ 无从判定，③ 会按「可能真跑挖掘」排时间")
        elif bad:
            print(f"  [重建之后] ⚠️ 仍落后 {len(bad)}/{len(checks)} 道："
                  + "、".join(c[0] for c in bad)
                  + "\n            ⇒ 这一场 official 支**产出仍会是 0**，上面两段子进程的输出就是原因")
        else:
            print(f"  [重建之后] ✅ {len(checks)}/{len(checks)} 道跟上 ⇒ official 支放行，"
                  "③ 的墙钟按「小时」排，不是 30 分钟")
    except Exception as e:
        print(f"  [重建之后] 调不到官方判据（{type(e).__name__}: {e}）⇒ 以 ③ 自己那行前置检查为准")


def newest_mtime(dirpath, suffixes=None):
    """一个目录（含一层子目录）里最新的 mtime ⇒ float 或 None（只读盘点用）"""
    best = None
    if not os.path.isdir(dirpath):
        return None
    for dp, _, fs in os.walk(dirpath):
        for f in fs:
            if suffixes and not f.endswith(tuple(suffixes)):
                continue
            try:
                mt = os.path.getmtime(os.path.join(dp, f))
            except OSError:
                continue
            if best is None or mt > best:
                best = mt
    return best


def feedback_evidence():
    """② 自己的产物 ⇒ (最新 mtime, 那一枚的文件名)。

    为什么不用「整个 report/ 目录里最新的一份」：09-28 实测 `report/llm_shap_report.md`
    是 ③ 的 `[9/9]` 写的（22:34），拿目录最大值会把 ③ 的新度算到 ② 头上 ⇒ 盘点表念成
    「反馈面刚跑过」而它其实没跑。这里只认 ② 自己落笔的那几枚。
    """
    paths = [os.path.join(REPORT_DIR, "report_daily.md"),
             os.path.join(REPORT_DIR, "self_eval_dims.json"),
             os.path.join(REPORT_DIR, "feedback_report.md"),
             os.path.join(DATA_DIR, "live", "feedback_report.json")]
    paths += sorted(glob.glob(os.path.join(DATA_DIR, "live", "report_daily_*.json")))[-1:]
    got = [(os.path.getmtime(p), p) for p in paths if os.path.exists(p)]
    if not got:
        return None, "（② 的产物一枚都没有）"
    mt, p = max(got)
    return mt, os.path.relpath(p, os.path.join(DATA_DIR, ".."))


def audit_report():
    """只读盘点：从产物的字节级读数反推「三块各跑在哪、谁把谁的新度盖过去了」

    这一格是为 09-29 那个诉求造的：`main.py`、`update_etf_daily.py`、`run_feedback.py`
    谁都能单独敲，敲完没人知道别的块有没有跟着走。这里不跑任何一步、不动一个字节，
    只把三块自己的"我跑过"的证据（mtime + 末日）摆成一张表，并给出缺块/过期判定。
    """
    print("\n──────── 只读盘点：链路三块各跑在哪（不跑任何东西）────────")
    ends = read_ends()
    mirror = [pd.Timestamp(v[0]) for k, v in ends.items()
              if k.startswith("全市场镜像")]
    mirror_end = max(mirror) if mirror else None
    blocks = []
    data_mt = max(filter(None, [
        newest_mtime(UNIVERSE_ALL_DIR, (".csv",)),
        newest_mtime(CACHE_DIR, (".csv",)),
        newest_mtime(RISK_DIR, (".csv",))]), default=None)
    ana = stamp("results/signals_daily.csv")
    fb_mt, fb_which = feedback_evidence()
    blocks.append(("① 数据面", data_mt,
                   f"价面末日 {max(v[0] for k, v in sorted(ends.items()) if k.startswith('全市场镜像'))}"
                   f"（沪/深分面见前置体检）" if mirror_end else "读不到镜像"))
    blocks.append(("③ 分析面", ana.get("mtime"),
                   f"signals_daily.csv {ana.get('rows', 0):,} 行、末日 {ana.get('last_date')}"))
    blocks.append(("② 反馈面", fb_mt, f"只认 ② 自己写的文件，最新一枚 = {fb_which}"))
    for name, mt, note in blocks:
        print(f"  {name:<8} 最后动笔 "
              + (time.strftime("%m-%d %H:%M:%S", time.localtime(mt)) if mt else "（查无此项）")
              + f"｜{note}")
    def _later(a, b):
        """两块谁更晚动笔 ⇒ (晚的那块, 差多少小时)；谁都不晚就返回 (None, 0)"""
        return (a, (a - b) / 3600.0) if a >= b else (b, (b - a) / 3600.0)

    if data_mt and ana.get("mtime"):
        who, hrs = _later(data_mt, ana["mtime"])
        print(f"  ⇒ ① vs ③：{'数据面' if who == data_mt else '分析面'}晚动笔 {hrs:.1f} 小时"
              + ("　⚠️ 有人只敲了 ①（或 ① 之后数据又推过一天），分析面没跟上"
                 if who == data_mt and hrs > 1 else
                 "　✅ 顺序正常（日更链就是 ① 之后走 ③）" if who == ana["mtime"] else ""))
    if ana.get("mtime") and fb_mt:
        who, hrs = _later(ana["mtime"], fb_mt)
        print(f"  ⇒ ③ vs ②：{'分析面' if who == ana['mtime'] else '反馈面'}晚动笔 {hrs:.1f} 小时"
              + ("　⚠️ 归档刚被重写，日报还是上一版的口径（② 吃的是 data/live 与 report，"
                 "重跑 ③ 之后该跟着跑一遍 ②）" if who == ana["mtime"] and hrs > 1
                 else "　✅ 日报跟着归档走"))
    lag_bt, sig_end = backtest_lag_days(ends)
    print(f"  ⇒ 分析面比镜像**旧 {lag_bt} 个工作日**（{sig_end:%Y-%m-%d} vs "
          f"{mirror_end:%Y-%m-%d}）" if lag_bt is not None and mirror_end else "")
    for k in CORE_ARTIFACTS + AUX_ARTIFACTS:
        s = stamp(k, with_tail=False)
        print(f"     · {k.split('/')[-1]:<32}"
              + (f"{s['when']}  {s['bytes']:>9,}B" if s["exists"] else "（不存在）"))
    return blocks


def verify_data(before, after, skipped=False):
    """① 的字节级验收 ⇒ [(判据名, 过不过, 证据)]；第三态 `None` = **本场无信息**

    G1「末日不倒退」是真判据：`append_tail` 只贴晚于末日的行，倒退只可能是写坏了
    （09-28 首次真跑就是靠它排掉一次误读）。
    G2 刻意给**三态**而不是 ✅/❌：`--no-data` 时这一步根本没起、① 真跑了但源里没有
    更新的一行（T+1 还没出）时，读数与"跑对"长得一模一样 ⇒ 把它印成绿线就是拿一条
    无信息的路冒充心跳（09-29 的教训：可失败的那一步最会藏恒真判据）。
    """
    regress = [f"{k}: {before[k][0]} → {after.get(k, ('缺失',))[0]}"
               for k in before if k not in after
               or str(after[k][0]) < str(before[k][0])]
    moved = [k for k in before if after[k][0] != before[k][0]]
    checks = [
        ("G1 任何一面的末日不得倒退", not regress,
         f"{len(before)} 个面全部持平或前移" if not regress else f"倒退的面：{regress}"),
        ("G2 本场有没有真的往前贴行", None if skipped or not moved else True,
         ("跳过（`--no-data`）：数据面一个字节未动 ⇒ 这条读数**无信息**" if skipped
          else f"推进了 {len(moved)} 个面：{moved}" if moved else
          "0 个面：源里本来没有更新的一行（逐只 `uptodate`）⇒ **无信息**，不是跑对了")),
    ]
    return checks


def print_checks(title, checks):
    """打一张验收表 ⇒ 红的判据名列表。三态：True=✅ / False=❌ / None=⚪ 无信息

    第四种 `REPORT_ONLY_RED`：渲染成 ❌（只报的那道红要看得见，不能糊成 ⚪），
    **但不进返回值** ⇒ 拿返回值决定 ② 起不起的那一处（`main()` 里 `if failed: raise SystemExit`）
    看不见它。口径与各处「红不红」的 `ok is False` 判断一致，老夹具不用改一把尺。
    """
    print(f"\n──────── {title} ────────")
    failed = []
    for name, ok_c, ev in checks:
        mark = "✅" if ok_c is True else ("❌" if ok_c is False else
                                        ("❌ 只报" if ok_c == REPORT_ONLY_RED else "⚪ 无信息"))
        print(f"  [{mark}] {name}：{ev}")
        if ok_c is False:
            failed.append(name)
    return failed


def main():
    # 手跑时看得见走到哪一步：stdout 不是终端（重定向进日志）时 Python 默认**块**缓冲，
    # 09-28 实测整链跑完之前日志一直 0 字节——11 分钟的链路看起来像卡死。
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(
        description="ETF 线日更链路：体检 → ① 三个数据面贴新行 → 〔③ 分析面九步，"
                    "要 --with-analysis〕→ ② 反馈闭环（软）；或 --audit 只读盘点")
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑体检 + ① 的 `--report`（只读），然后停；"
                         "日历说没有该补的一格时也停（② 会写生产路径，排练不该升级成真跑）")
    ap.add_argument("--no-feedback", action="store_true", help="跳过 ②")
    ap.add_argument("--skip-risk", action="store_true",
                    help="① 不带风险面板采集（透传 `update_etf_daily.py --skip-risk`）")
    ap.add_argument("--force", action="store_true",
                    help="绕过 15:00 收盘闸 + 「这一格还没发生」的日历闸（补欠账时用）")
    ap.add_argument("--codes", nargs="*", default=None,
                    help="只处理这些代码（冒烟，透传给 ①）")
    ap.add_argument("--with-analysis", action="store_true",
                    help="加跑 ③ 分析面（`run_daily_backtest.py` 九步）：覆写 data/results/ "
                         "归档 + upsert 因子库 + 打一个 [factor-lib] git commit，"
                         "实测 30~52 分钟。起之前先念三张闸（重挖冷却 / RD-Agent 数据新鲜度 / LLM 端点）")
    ap.add_argument("--no-data", action="store_true",
                    help="跳过 ①（数据面一个字节不动）：价面已到位、只想补后面几块时用；"
                         "这时 15:00 收盘闸不再拦——那道闸量的是「盘中贴半根 bar」的风险，"
                         "不贴就不归它管")
    ap.add_argument("--allow-remining", action="store_true",
                    help="允许 ③ 在「重挖冷却已过」时照跑（默认这时**拒起**，"
                         "因为 [9/9] 可能踢出几小时级的容器重挖）")
    ap.add_argument("--allow-official-absent", action="store_true",
                    help="允许 ③ 在 official 这条腿**一个因子没交**时照走 ②（默认这时"
                         "验收 G 判红 ⇒ ② 不起、链路非零退出）。这一腿靠 RD-Agent 容器循环，"
                         "前置检查任何一项红（docker 探测超时 / bin 或面板不新鲜 / 无 conda）"
                         "都会让它静默产出 0 而子进程退出码照 0。只在这场本来就没打算起容器"
                         "（例如明知 daemon 没起、只要 llm 那几条）时用")
    ap.add_argument("--allow-official-stale", action="store_true",
                    help="允许 ③ 在 official 交回的那批**全是起场前旧档**（本场净增 0）时照走 ②"
                         "（默认这时验收 H 判红 ⇒ ② 不起、链路非零退出）。来历：回收读的是固定"
                         "路径 factors.json，没有新鲜度与名字作用域，所以 10-04 那场链路第一次"
                         "全程打通、也照样是「产出 12 个 / 净增 0 个」——这一腿把上一场的存货"
                         "念成了本场产出。只在这场**明知不会挖出新东西**（例如只想刷归档、"
                         "或已知 9b 那几个想法会被相似闸判退）时用。与 --allow-official-absent "
                         "是两颗独立的闸：前者管腿没跑，后者管跑了但交旧档")
    ap.add_argument("--resume", action="store_true",
                    help="③ 开段级断点续传（env ETF_RUN_RESUME=1）：主线多源与样本外"
                         "各折各自把「挖出来的因子」落盘，重启/被杀后从已完成的那一段"
                         "接着算，不再重起容器循环。**默认不带 = 九步全部重算，"
                         "与这套机器存在前逐字节同行为**。作废闸是**逐条**的：每条条目"
                         "带自己的指纹作用域（折内段读折几何与折内开关，主线段不读），"
                         "拨折配置那几颗键只作废各折条目、主线那 ≈1h40m 留着；数据一推进"
                         "才整批失效。详见 src/run_checkpoint.py")
    ap.add_argument("--audit", action="store_true",
                    help="**只读**盘点：不动任何字节，从产物 mtime/末日/行数反推三块各跑在哪天、"
                         "谁把谁的新度盖过去了（= 查「单独敲了一个脚本」的后遗症）")
    a = ap.parse_args()
    # 每一块到底是"跑了"还是"被谁关掉了"，收尾要打成一张表念出来。
    # 09-29 的诉求就是这张表：链路块丢失最阴的地方不是崩，是**默默没跑**还 exit 0。
    block_ran = {}

    others = find_running()
    if others:
        raise SystemExit(f"[已经在跑了] 检测到另一个本链路进程 pid={others} ⇒ 不动字节。"
                         "要看它在做什么：`ps -o pid,etime,cmd -p "
                         f"{','.join(others)}`，它的输出去了它的 stdout 重定向处")
    print(f"[复跑闸] /proc 里没有别的 {LOCK_PATTERN}（本进程 {os.getpid()}）")
    if a.audit:
        audit_report()
        return
    check_cancel("前置体检")

    print("\n──────── 前置体检（只读）────────")
    before = read_ends()
    for k, (d, lag, n) in sorted(before.items()):
        print(f"  {k:<22} 末日 {d}  批内落后 {lag} 个工作日  {n} 只")
    base = price_surface_ends(before)
    if not base:
        raise SystemExit("[前置体检] 读不到价面（镜像/池缓存）的末日 ⇒ 链路不动字节")
    spread = mirror_lag(before)
    print(f"[前置体检] 镜像沪/深两面之间的批内参差最坏 {spread} 个工作日"
          "（这一格念的是「沪深齐不齐」，见 `mirror_lag`；不是「落后到今天几天」）")
    readings, unknown = decide_sessions(before)
    if unknown:
        print(f"[前置体检] ⚠️ {unknown}\n  ⇒ **判不出该不该补 ①**（这不是「今天没有该补的」，"
              "是「不知道」）。① 的判据本身不依赖日历（`append_tail` 只贴晚于末日的行、"
              "收盘口径对不上就跳过），所以收盘闸放行时它照起，最坏逐只回 `uptodate`；"
              "`--dry-run` 则照旧只读后停。")
    else:
        ends_map = dict(base)
        print(f"[前置体检] 逐张价面 vs 交易所日历（今天 {pd.Timestamp.now():%Y-%m-%d}）：")
        for name, (p, adv) in readings.items():
            print(f"  {name:<14} 末日 {ends_map[name]}  下一场 {p}  "
                  + ("⇒ **该补**" if adv else "⇒ 已到位（那场还没发生）"))
    advance = any(adv for _, adv in readings.values())
    print("[前置体检] ⇒ ① "
          + ("**该起**：有价面还差当日那一格" if advance else
             "不起：所有价面都停在最近一场" if readings else "不起：判不出"))

    ok, why = close_gate()
    if ok:
        print(f"[收盘闸] {why}")
    elif a.no_data:
        # 这道闸量的是「盘中把半根 bar 贴进日线」的风险。--no-data 时 ① 压根不起，
        # ②③ 只读已经落定的日线 ⇒ 写不了半根 bar，闸不拦（补欠账常在晚上/周末敲）。
        print(f"[收盘闸] {why}　⇒ **不拦**：带了 `--no-data`，① 不起 ⇒ 没有半根 bar 可贴，"
              "这一道闸不归它管")
    elif a.force:
        print(f"[收盘闸] {why}　⇒ `--force` 绕过，① 照起（风险：可能贴进半根 bar）")
    else:
        raise SystemExit(f"[收盘闸挡住] {why} ⇒ 一个字节都没动。"
                         "（只想补 ②③ 就加 `--no-data`；要强贴日线加 `--force`）")

    if not advance and not unknown and not a.force and not a.no_data:
        print(f"\n[停在这里] 日历说 {len(readings)} 张价面的下一场**都还没发生**"
              f"（{sorted({p for p, _ in readings.values()})}）⇒ ① 没有该补的一格，"
              "② 也不起（它写 report/ 与 data/live/，没有新 bar 的日报只是把昨天的数字"
              "钉上一个新日期）。\n要强行走完：" + f"`{P310} {os.path.abspath(__file__)} --force`\n"
              "价面本来就到位、只想补分析面/反馈面：加 `--no-data`"
              "（① 一个字节不动，后面几块照跑）")
        return

    if a.dry_run:
        run("① 排练（`update_etf_daily.py --report`，只读）", [P310, DATA_PY, "--report"])
        print("\n[停在这里] --dry-run 只到体检与排练：三面一个字节没写，② 也没起。"
              f"\n真跑就去掉 --dry-run；风险面板是 append-only、镜像只贴新行，"
              "写坏了不用回滚（历史一格未动）。")
        return

    if a.no_data:
        print("\n[① 跳过] `--no-data`：三个数据面（镜像/池缓存/风险长表）一个字节不动，"
              "后面的块吃它们现有的末日")
        out, bad = "", []
        after = before
        block_ran["① 数据面"] = "跳过（--no-data）"
    else:
        check_cancel("① 三个数据面贴新行")
        cmd = [P310, DATA_PY]
        if a.skip_risk:
            cmd.append("--skip-risk")
        if a.codes:
            cmd += ["--codes", *a.codes]
        block_ran["① 数据面"] = time.strftime("%H:%M:%S 起", time.localtime())
        out = run("① 全市场镜像 + 主线池缓存 + 风险长表", cmd) or ""
        bad = re.findall(r"⚠️ (\d+) 只因口径对不上未追加", out)
        check_cancel("① 之后的验收")
        after = read_ends()
    failed = print_checks("验收 ①（从产物反推，不写死日期）",
                          verify_data(before, after, skipped=a.no_data))
    if failed:
        raise SystemExit(f"[验收 ① 失败] {failed} ⇒ 后面的块不起：不可能只贴新行却倒退，"
                         "先查这一步写了什么")
    if bad:
        print(f"[验收 ①] ⚠️ {sum(int(x) for x in bad)} 只因源与镜像口径对不上被跳过"
              "（收盘 rel > 1e-6 或成交量量纲跳 100×）⇒ 这些代码的历史行仍在旧一天，"
              "看板的新鲜度表会把它念成落后")

    # ───────── ③ 分析面（默认关；--with-analysis 才起）─────────
    if not a.with_analysis:
        print("\n[③ 未起] 没带 `--with-analysis` ⇒ `data/results/` 那批归档表保持原样。"
              "看板「日线净值止于」会念成上一场那天")
        block_ran["③ 分析面"] = "未起（没带 --with-analysis）"
    else:
        check_cancel("③ 分析面起之前的三张闸")
        gates, blockers = analysis_gate_readings()
        print("\n──────── ③ 起之前的三张闸（只读）────────")
        for k, v in gates.items():
            print(f"  {k:<9} {v}")
        if blockers and not a.allow_remining:
            raise SystemExit("[③ 拒起] " + "；".join(blockers)
                             + "\n⇒ 链路一个字节没动（①② 若已跑就留在原处，"
                               "这是刻意留的手动闸，不是崩）。确认要起：加 `--allow-remining` 重敲")
        elif blockers:
            print("[③ 闸] ⚠️ " + "；".join(blockers) + "\n⇒ `--allow-remining` 已给 ⇒ 照起，"
                  "**墙钟可能是几小时**，不是 30 分钟")
        # 重建放在闸 a) 之后：上面"拒起"那条路径要保住它念的「链路一个字节没动」，
        # 而走到这一行就是 ③ 真要起 ⇒ official 支能不能跑，取决于这两块产物新不新。
        rebuild_rdagent_data_surface()
        t_analysis = time.time()
        before_a = stamp_all(CORE_ARTIFACTS + AUX_ARTIFACTS)
        # J 格（C-1）的两半在这里取数：**起场前**读候选清单与库键，**收场后**再读一遍。
        # 基线必须自己现读，不能拿归档的倒数第二行当基线——那假设"每场都成功归档过"，
        # 一场崩掉就整条错位；`official_candidates_snapshot()` 与共享层 `_existing_factor_names`
        # 同一份路径清单，所以这就是回收层那句话点名的同一棵树。
        cand_before, lib_before = official_candidates_snapshot(), factor_library_keys()
        block_ran["③ 分析面"] = time.strftime("%H:%M:%S 起", time.localtime(t_analysis))
        try:
            out3 = run("③ 分析面九步（覆写 data/results/ 归档 + upsert 因子库 + [factor-lib] commit）"
                       + ("｜断点续传已开" if a.resume else ""),
                       [P310, ANALYSIS_PY],
                       extra_env={"ETF_RUN_RESUME": "1"} if a.resume else None) or ""
        finally:
            after_a = stamp_all(CORE_ARTIFACTS + AUX_ARTIFACTS)
            # 归档放进 finally：崩在半路的那一场**正是**最需要留下候选清单的那一场
            # （10-06 那三场分不了类的冤案，缺的就是这份字节）。写坏了不拦路，J 会念 error。
            archive = archive_official_candidates()
        cand_after, lib_after = official_candidates_snapshot(), factor_library_keys()
        failed = print_checks(
            "验收 ③（全部从产物反推；③ 天然不可幂等，所以不判「和上一场一样」）",
            verify_analysis(before_a, after_a, t_analysis, out3,
                            allow_official_absent=a.allow_official_absent,
                            allow_official_stale=a.allow_official_stale,
                            official_candidates={
                                "before": None if cand_before is None else cand_before["names"],
                                "after": None if cand_after is None else cand_after["names"],
                                "library_before": lib_before, "library_after": lib_after,
                                "archive": archive}))
        if failed:
            # ② 吃的是 ③ 写出来的归档：分析面半口血就往 ② 走，日报会把一个坏掉的净值当今天的成绩
            raise SystemExit(f"[验收 ③ 失败] {failed} ⇒ **② 不起**：归档是三张表的唯一来源，"
                             "它没写全就进日报，等于把坏净值发上看板。快照在 "
                             "`etf/v1/temp/`，先看日志再决定重跑")
        # `[factor-lib]` 只存在于 commit message 里，子进程日志念的是「📝 git commit: 因子库更新…」
        commits = re.findall(r"git commit:\s*因子库更新:\s*(\d+) 总 / (\d+) 活跃", out3)
        print(f"  [附] 本场因子库提交 {len(commits)} 次"
              + ("：" + " → ".join(f"{t}总/{a}活跃" for t, a in commits) if commits
                 else "（一次没有 ⇒ `[4/9]` 没判出净变化，`git log -1 --oneline` 应当还是上一场那条）"))
        # `.get` 不是凑数：`stamp()` 对不存在的表返回的是 `{"exists": False}`，**没有 mtime 键**。
        # 副产物里现在有一张（候选归档账本）是"归档写坏了就没有"的 ⇒ 硬取键会把整张验收表之后的
        # 附报打死，与 10-06 A 格那次 `KeyError` 同病（取证层不许比判据先崩）。
        print(f"  [附] 副产物 {sum(1 for k in AUX_ARTIFACTS if after_a[k].get('mtime', 0) >= t_analysis)}"
              f"/{len(AUX_ARTIFACTS)} 张本场被写过")

    if a.no_feedback:
        print("[② 跳过] --no-feedback")
        block_ran["② 反馈面"] = "跳过（--no-feedback）"
    else:
        check_cancel("② 反馈闭环")
        block_ran["② 反馈面"] = time.strftime("%H:%M:%S 起", time.localtime())
        run("② 反馈闭环（日报 + 单份自评，只写 report/ 与 data/live/）",
            [P310, os.path.join(SRC, "run_feedback.py"),
             "--auto", "--llm", "daily", "--self-eval", "--monthly-days", "1"],
            soft=True)

    print("\n──────── 链路块盘点（这一格就是「单独敲一个脚本会不会丢块」的答案）"
          "────────")
    fb_mt, fb_which = feedback_evidence()
    sig = stamp(CORE_ARTIFACTS[0])
    evidence = {
        "① 数据面": "价面末日 " + max((str(v[0]) for k, v in after.items()
                                     if k.startswith("全市场镜像")), default="?"),
        "③ 分析面": f"signals_daily.csv {sig.get('rows', 0):,} 行、末日 "
                    f"{sig.get('last_date') or '缺表'}",
        "② 反馈面": f"② 自己的产物最新一枚 = {fb_which}"
                    f"（{time.strftime('%m-%d %H:%M', time.localtime(fb_mt)) if fb_mt else '无'}）",
    }
    for name in ("① 数据面", "③ 分析面", "② 反馈面"):
        print(f"  {name:<8} 本场：{block_ran.get(name, '本场未走到这一步'):<24}"
              f" 字节证据：{evidence[name]}")
    missing = [n for n, m in block_ran.items() if "跳过" in m or "未起" in m]
    print("  ⇒ " + ("三块本场都走了" if not missing else
                   f"**有块没走**：{'、'.join(missing)} —— 这是命令行**有意**关掉的"
                   "（`--no-data` / 没带 `--with-analysis` / `--no-feedback`），不是链路崩；"
                   "归档与看板会留下对应的旧一天，`--audit` 随时能把它念出来"))

    lag_bt, sig_end = backtest_lag_days(after)
    print("\n──────── 收尾读数 ────────")
    for k, (d, l, n) in sorted(after.items()):
        print(f"  {k:<22} 末日 {d}  批内落后 {l} 个工作日  {n} 只")
    r2, unk2 = decide_sessions(after)
    print(f"[价面是否追平] 补完后逐张价面 vs 交易所日历（今天 {pd.Timestamp.now():%Y-%m-%d}）：")
    if unk2:
        print(f"  ⚠️ {unk2}")
    else:
        ends2 = dict(price_surface_ends(after))
        still = []
        for name, (p, adv) in r2.items():
            print(f"  {name:<14} 末日 {ends2[name]}  下一场 {p}  "
                  + ("⇒ **还差这一格**" if adv else "⇒ 已到位"))
            if adv:
                still.append(name)
        print("[价面是否追平] ⇒ " + (
            f"{len(still)}/{len(r2)} 张价面还差当日那一格（{still}）"
            "⇒ 源 T+1 才出、或口径不合被跳过；下一场日更接着补这一格" if still
            else f"{len(r2)}/{len(r2)} 张价面全部到位"))
    print(f"[分析面滞后] `signals_daily.csv` 止于 "
          + (f"{sig_end:%Y-%m-%d}" if sig_end is not None else "（没有这张表）")
          + (f"，比镜像**旧** {lag_bt} 个工作日 ⇒ 回测三张表不在日更链里刷新："
             "刷它只有周线 `main.py`（实测 52 分钟，且会覆盖归档回测产物 + upsert "
             "因子库 + `[factor-lib]` git commit）。看板上的「日线净值止于」念的是"
             "这句话，不是「数据止于」。" if lag_bt else "，与镜像同一天。"))
    print("\n[链路完成] 看板不用重启（streamlit 每次交互重跑 `app.py`）")


if __name__ == "__main__":
    main()
