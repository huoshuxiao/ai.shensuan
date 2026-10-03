# -*- coding: utf-8 -*-
"""AGENT.md 引证核对：文档里每一处 `文件:行号` 都必须真的指向那个东西。

判据：逐条读指定文件的指定行区间，要求区间内出现期望关键字。任何一条不满足即退出码 1
并打印实际行内容 —— 这是**反证式**检查，不存在"没读到就算过"的恒真分支。
"""
import sys
from pathlib import Path

ROOT = Path("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git")

# (文档章节, 文件, 起始行, 结束行, 期望关键字)
CHECKS = [
    # 09-26：config.py 里插了「稳不稳」秤那一段（19 行），其后所有锚点整体 +19
    # 09-26 晚：又在 ASHARE_SCREEN_RULES 之后插了「名单独立闸」那段（11 行），
    #          其后的 config 锚点再 +11；ashare_screen 加 71~87 行，看板加 16 行
    # 09-27：chain 里插了 `def run_exposure`（⑤ 挂进链路），162 之后的四条 ①②③④ 锚点整体 +30
    # 09-28：环3 两处读数缺陷修掉（判重看 |corr|、候选侧按名字剔掉"它自己"），route() 之上
    #        累计涨了 35 行（HEAD 的 52 → 工作树 87）⇒ 锚点重挂 54-60 → 75-83 → **87-95**。
    #        中间那一次（75-83）是当天先改 docstring 时挂的，随后代码又涨了 12 行 —— 又一次
    #        印证"判据一改行号必漂，改完必须重跑这份自检"，别信上一轮写下的数字。
    # 09-29 数据层进 common：config_base.py 的 build() 里插了 BASE_DATA_DIR 那一段（+14 行，
    #        在第 80 行附近），它下面三个锚点整体 +14/+12：412-416 → 424-428（qlib_data）、
    #        323-330 → 337-344（dsr_threshold）、293-294 → 307-308（daily_stop_loss）。
    #        同一天 run_etf_daily_chain.py 被**并行会话**改掉 replay_manual_delist（−2 行），
    #        337-341 → 335-339 —— 这条红不是我挪的，只有行号跟着动。
    ("§8.2 股票判重红线", "stock/v1/src/config/config.py", 82, 82, "ASHARE_RED_BAR"),
    ("§8.2 红线数值 0.99", "stock/v1/src/config/config.py", 82, 82, "0.99"),
    ("§9.2 双强度", "stock/v1/src/config/config.py", 279, 279, "ASHARE_BUY_MIN_HITS"),
    ("§9.2 分位阈值", "stock/v1/src/config/config.py", 139, 139, "ASHARE_SCREEN_QUANTILE"),
    ("§9.2 名单独立闸开关", "stock/v1/src/config/config.py", 153, 153,
     "ASHARE_BUY_EXTRA_RULES"),
    ("§9.2 名单独立闸刀口（另一根旋钮）", "stock/v1/src/config/config.py", 172, 172,
     "ASHARE_BUY_EXTRA_QUANTILE"),
    ("§9.2 名单独立闸判据本体", "stock/v1/src/strategy/ashare_screen.py", 268, 271,
     "BUY_EXTRA_RULES"),
    ("§9.2 名单独立闸单点函数", "stock/v1/src/strategy/ashare_screen.py", 701, 703,
     "def buy_extra_block"),
    ("§9.1 均额闸门", "stock/v1/src/config/config.py", 116, 117, "ASHARE_PORT_MIN_AMOUNT"),
    ("§9.1 涨停闸三档开关", "stock/v1/src/config/config.py", 341, 342, "ASHARE_TRADABLE_GATE"),
    ("§11 近涨停余量", "stock/v1/src/config/config.py", 300, 300, "ASHARE_LIMIT_NEAR"),
    ("§9.1 名单形状开关", "stock/v1/src/config/config.py", 218, 219, "ASHARE_LIST_SCHEME"),
    ("§9.1 板块席位", "stock/v1/src/config/config.py", 230, 231, "ASHARE_LIST_QUOTA"),
    ("§9.1 名单长度 50", "stock/v1/src/config/config.py", 188, 188, "ASHARE_BUY_TOP_N"),
    ("§9.1 席位单点出口", "stock/v1/src/strategy/ashare_screen.py", 735, 735, "def apply_board_quota"),
    ("§9.1 排队取名单", "stock/v1/src/strategy/ashare_screen.py", 822, 823, "def buy_candidates"),
    ("§9.1 口径措辞单点", "stock/v1/src/strategy/ashare_screen.py", 800, 800, "def list_desc"),
    ("§11 下单层贪心扫描", "stock/v1/src/strategy/ashare_screen.py", 1064, 1065, "def order_candidates"),
    ("§7 链路不含研究三入口", "stock/v1/src/run_ashare_daily_chain.py", 59, 60, "不含"),
    ("§18.8 链路 python3.10 硬编码", "stock/v1/src/run_ashare_daily_chain.py", 94, 94, "python3.10"),
    ("§7 链路该跑哪一场=日历驱动", "stock/v1/src/run_ashare_daily_chain.py", 162, 163, "def decide_session"),
    ("§7 链路①bin 日更", "stock/v1/src/run_ashare_daily_chain.py", 257, 257, "update_qlib_bin_daily"),
    ("§7 链路②重生成面板", "stock/v1/src/run_ashare_daily_chain.py", 297, 297, "PREGEN_PY"),
    ("§7 链路③盘前名单", "stock/v1/src/run_ashare_daily_chain.py", 307, 307, "run_ashare_daily_signal"),
    ("§7 链路④次日真账", "stock/v1/src/run_ashare_daily_chain.py", 337, 337, "run_ashare_daily_audit"),
    ("§7 链路⑤仓位层前向账本（不走 run()）", "stock/v1/src/run_ashare_daily_chain.py", 195, 196,
     "def run_exposure"),
    ("§7 ⑤ 四档建议仓位的判据单点", "stock/v1/src/run_ashare_exposure_monitor.py", 160, 162,
     "def suggest_e"),
    # ↓ 09-29：重跑器 B6「复牌末格」那条豁免（判据单点 + AGENT.md 念的那根行号）
    ("§7 B6 复牌末格豁免的判据单点", "stock/v1/src/run_ashare_rerun_chain.py", 145, 147,
     "def b6_judge"),
    # ↓ 09-30：新场次（库里还没有的那一天）形态下 B4 的两层判据
    ("§7 B4 新场次账本的两层判据单点", "stock/v1/src/run_ashare_rerun_chain.py", 198, 200,
     "def ledger_rows_check"),
    # ↓ 09-29：§7 重跑器 `--from-bin` 那段引的那条规则（复牌首日涨跌幅留 NaN）
    ("§7 复牌首日 change 留 NaN 的规则落点", "common/src/data/stock/update_qlib_bin_daily.py", 540, 541,
     'cells["change"] = np.nan'),
    ("§7 面板只取六列（$change 不进面板）", "common/rdagent_docker/pregen_source_data.py", 39, 39,
     "FIELDS"),
    ("§6.3 bin 对齐率阈值", "common/src/data/stock/update_qlib_bin_daily.py", 118, 120, "ALIGN_MIN"),
    ("§6.3 15:00 收盘闸", "common/src/data/stock/update_qlib_bin_daily.py", 163, 164, "CLOSE_HM"),
    ("§6.3 未来场次闸（抓数之前）", "common/src/data/stock/update_qlib_bin_daily.py", 306, 307, "def guard_session"),
    ("§6.3 复用缓存前对表", "common/src/data/stock/update_qlib_bin_daily.py", 190, 191, "def align_against_bin"),
    ("§18.8 conda 候选路径", "common/src/core/official_rdagent.py", 25, 32, "_CONDA_CANDIDATES"),
    # 431/445 → 443/457：10-01「甲」在 FACTOR_LIBRARY 段插进 static_gate
    # 那 12 行，把这两格整体推下 12 行（同批同步，非代码语义变化）
    # 10-03「甲（模型单入口）」：config_base.py 在 RDAGENT_COSTEER_MAX_LOOP 之后插了
    # RDAGENT_LLM_MODEL 那 6 行 ⇒ 457/521 起整体 +6（443 那格在插点之上，没动）。
    # 同一天 official_rdagent.py 加了 _driver_env 的模型注入 + official_chat_model
    # 读数（+54 行）⇒ 292/327 起 +54；这两格**动手前就已红**（并行会话同日在 ③ 前置
    # 链里加过代码），本批一并重挂，不是只跟我自己挪的那部分。
    ("§5.1 容器内 provider_uri", "common/src/config_base.py", 443, 447, "qlib_data"),
    ("§5.1 容器资源 shm/mem", "common/src/config_base.py", 485, 491, "QLIB_DOCKER_SHM_SIZE"),
    ("§12.3 重挖触发阈值", "common/src/config_base.py", 344, 351, "dsr_threshold"),
    ("§12.2 风控基线", "common/src/config_base.py", 314, 315, "daily_stop_loss"),
    ("§12.3 触发判定入口", "common/src/optimizer/trigger_logic.py", 48, 50, "def check"),
    ("§12.3 单轮恶化判定", "common/src/optimizer/trigger_logic.py", 27, 27, "def _bad"),
    ("§12.3 连续 N 轮才触发", "common/src/optimizer/trigger_logic.py", 5, 5, "consecutive_rounds"),
    ("§12.1 默认 paper", "etf/v1/src/live/config_live.py", 13, 13, "paper"),
    ("§12.1 三开关", "etf/v1/src/live/config_live.py", 26, 31, "auto_order"),
    ("§12.2 live 风控基线", "etf/v1/src/live/config_live.py", 59, 60, "max_drawdown_stop"),
    ("§12.1 券商依赖被注释", "etf/v1/requirements.txt", 36, 37, "easytrader"),
    ("§7 scheduler 不排 live", "etf/v1/src/scheduler.py", 64, 67, "schedule"),
    ("§12.1 双开关才下单", "etf/v1/src/run_live.py", 239, 248, "auto_order"),
    ("§12.2 越界风控数拦截", "etf/v1/src/run_live.py", 51, 86, "RISK_BOUNDS"),
    ("§12.2 日止损实现", "etf/v1/src/backtest/backtest_daily.py", 131, 151, "daily_stop_loss"),
    ("§12.2 live 侧同一判据", "etf/v1/src/live/live_risk.py", 41, 47, "max_drawdown_stop"),
    ("§12.3 DSR 报告线 0.95", "etf/v1/src/backtest/dsr.py", 75, 75, "0.95"),
    ("§12.2 ETF 参数扫描档", "etf/v1/src/config/config.py", 203, 207, "max_drawdown_stop"),
    ("§8.2 环1 截面最小数", "etf/v1/src/etf_admission.py", 94, 96, "MIN_CS"),
    ("§8.2 环1 波动闸", "etf/v1/src/etf_admission.py", 102, 102, "MIN_ANN_VOL"),
    ("§8.2 环1 假台阶 0.35", "etf/v1/src/etf_admission.py", 107, 107, "RET_LIMIT"),
    ("§8.2 环1 主视野", "etf/v1/src/etf_admission.py", 126, 126, "HORIZONS"),
    ("§8.2 环2 ICIR 门槛", "etf/v1/src/etf_admission.py", 1151, 1151, "min_abs"),
    ("§8.2 环2 规模闸", "etf/v1/src/etf_admission.py", 151, 151, "MIN_SCALE"),
    ("§8.2 环3 三档阈值", "etf/v1/src/etf_admission.py", 177, 179, "NEAR_DUP"),
    ("§8.3 环1 候选路由护栏", "etf/v1/src/run_etf_factor_eval.py", 54, 60, "cand"),
    ("§8.3 环3 候选路由护栏", "etf/v1/src/run_etf_redundancy_check.py", 91, 99, "cand"),
    # ↓ 09-29 丁2：09-28 那次 docstring + library_counts 一共在 route() 之上加了 4 行
    #   （87 → 91），并把对角线那两段的落点第一次钉进引证表
    ("§8.2 环3 为何补在库×在库", "etf/v1/src/run_etf_redundancy_check.py", 40, 53,
     "在库 × 在库"),
    ("§8.2 环3 三口径数库", "etf/v1/src/run_etf_redundancy_check.py", 110, 127,
     "def library_counts"),
    ("§8.2 环3 在库对角线秤", "etf/v1/src/run_etf_redundancy_check.py", 157, 185,
     "def library_internal"),
    # ↓ 09-28 #53：ETF 日更链路的两个判据落点（§7 那句「逐张价面问日历」+ §6.3 的批内最大值）
    ("§7 ETF 逐张价面判该不该补", "etf/v1/src/run_etf_daily_chain.py", 335, 339,
     "def decide_sessions"),
    ("§7 ETF「落后交易日」量的是批内", "common/src/data/etf/data_loader.py", 50, 52,
     "_mirror_batch_end"),
    ("§6.3 ETF 贴新行的单点判据", "common/src/data/etf/update_etf_daily.py", 92, 96,
     "def append_tail"),
    ("§18.9 ETF 回测末日按市场裂开的判据", "common/src/data/etf/data_loader.py", 93, 95,
     "def _cache_is_stale"),
    ("§18.8 ETF bin 的 conda 绝对路径", "common/src/data/etf/dump_qlib_bin.py", 256, 256, "miniconda3"),
    # ↓ 09-30 §18.11「挖掘 IC 是逐只时序、下单是当日截面」：两把尺子各自的落点 + 三道门槛的落点
    ("§18.11 挖掘 IC=逐只时序 compute_ic", "common/src/core/llm_factor_agent.py", 111, 118, "compute_ic"),
    ("§18.11 挖掘 IC 再对标的取均值", "common/src/core/llm_factor_agent.py", 127, 127, "np.mean"),
    ("§18.11 llm 源自己的 pass 旗（0.02）", "common/src/core/llm_factor_agent.py", 132, 132,
     "IC_THRESHOLD"),
    ("§18.11 pass 决定进不进 knowledge_base", "common/src/core/llm_factor_agent.py", 161, 163,
     "knowledge_base"),
    ("§18.11 常量序列在尺子上短路成 0", "common/src/core/factor_dsl.py", 217, 217, "nunique"),
    ("§18.11 执行层=当日截面名次", "etf/v1/src/strategy/strategy.py", 105, 105,
     "def generate_signals"),
    ("§18.11 准入尺子的当日标的数地板", "etf/v1/src/etf_admission.py", 94, 94, "ETF_MIN_CS"),
    ("§18.11 逐日截面 IC 函数本体", "etf/v1/src/etf_admission.py", 468, 468, "def daily_cs_ic"),
    ("§18.11 链路真闸 0.005", "etf/v1/src/main.py", 65, 65, "min_ic=0.005"),
    ("§18.11 逐源地板（绕不过）", "common/src/core/multi_source_mining.py", 203, 203,
     "min_ic_per_source"),
    ("§18.11 0.02 的取值处（daily 档）", "common/src/config_base.py", 112, 112, "IC_THRESHOLD"),
    ("§18.11 0.01 属 FACTOR_DECAY", "etf/v1/src/config/config.py", 183, 183, "ic_min_threshold"),
    ("§18.11 0.01 的消费点在生命周期", "etf/v1/src/strategy/strategy_lifecycle.py", 141, 141,
     "ic_min_threshold"),
    ("§18.11 折内 >240 bar 闸（宽度只有 4/17/69 只的来源）", "etf/v1/src/backtest/walk_forward.py",
     93, 93, "240"),
    # ↓ 10-03「C2/C3 收尸」在该文件顶部加 import signal + 三个新函数（净 +64 行）
    #   ⇒ 346/381 与 48/53 四格整体下移，本批重挂（牙在 etf/v1/temp/check_reap_group_1003.py）
    ("§18.11 official 回收读固定目录、不分折", "common/src/core/official_rdagent.py", 415, 415,
     "RDAGENT_OUTPUT_DIR"),
    ("§18.11 超时分支直接交回既有产物", "common/src/core/official_rdagent.py", 455, 463, "harvest"),
    # ↓ 10-03 §18.13「跑一次为什么起两个本地模型」：甲只统一**配置入口**并把选型变成**读数**，
    #        四根新针钉的是正文那四个 `文件:行号`（正文归正文、这把尺子只认这张表）
    ("§18.13 两源并发＝两个模型同时常驻的那一行", "common/src/core/multi_source_mining.py",
     184, 184, "ThreadPoolExecutor"),
    ("§18.13 单入口键＝默认留空不注入", "common/src/config_base.py", 470, 470,
     'd["RDAGENT_LLM_MODEL"]'),
    ("§18.13 注入点（压过工作区 .env）", "common/src/core/official_rdagent.py", 53, 53,
     'env["LITELLM_CHAT_MODEL"]'),
    ("§18.13 读数的取数点", "common/src/core/official_rdagent.py", 62, 62,
     "def official_chat_model"),
    # ↓ 10-03 夜「C2/C3 收尸」：正文那五处 `文件:行号` 同批钉住（牙=
    #   etf/v1/temp/check_reap_group_1003.py，六臂 17 格全绿）
    ("§18.13 C3 直调环境内解释器", "common/src/core/official_rdagent.py", 99, 99,
     "def _find_env_python"),
    ("§18.13 C2 整组收尸单点", "common/src/core/official_rdagent.py", 122, 122,
     "def _reap_group"),
    ("§18.13 驱动自成一组", "common/src/core/official_rdagent.py", 470, 470,
     "start_new_session=True"),
    ("§18.13 超时那一句走收尸不是 kill 外壳", "common/src/core/official_rdagent.py", 477, 477,
     "_reap_group(proc)"),
    ("§18.13 回收只查文件存在（B6 未裁、仍是旧档）", "common/src/core/official_rdagent.py", 498, 498,
     "os.path.exists(path)"),
    # ↓ 10-03 夜「乙+ kwargs 补丁」：正文那六处 `文件:行号` 同批钉住（牙=
    #   etf/v1/temp/check_llm_kwargs_patch_1003.py，两套解释器 17/21 格全绿；破坏性自证＝
    #   把 `litellm.completion = completion` 换成 `pass` ⇒ 恰好 F5 三格 + F7 before + F8 on
    #   五格红、rc=1，该绿的 F6/F7 after/F8 off 原样绿）
    ("§18.13 乙+ 配置键＝默认空串不注入", "common/src/config_base.py", 480, 480,
     'd["RDAGENT_LLM_KWARGS"]'),
    ("§18.13 乙+ 外壳透传（非空才进子进程 env）", "common/src/core/official_rdagent.py", 56, 56,
     "extra = str(RDAGENT_LLM_KWARGS"),
    ("§18.13 乙+ 驱动解析（坏值必须抛不许静默）", "common/src/core/rdagent_driver.py", 27, 27,
     "def _parse_llm_kwargs"),
    ("§18.13 乙+ 驱动打补丁（包 litellm.completion）", "common/src/core/rdagent_driver.py", 43, 43,
     "def _patch_llm_kwargs"),
    ("§18.13 乙+ 现场核绑定（核不上就抛）", "common/src/core/rdagent_driver.py", 67, 67,
     "def _verify_llm_patch"),
    ("§18.13 乙+ 调用点：补丁必须打在 import rdagent 之前", "common/src/core/rdagent_driver.py",
     354, 361, "from rdagent.app.qlib_rd_loop.factor import"),
    # ↓ 10-01 §18.12「多角色前置假设闸（默认关）＋ 4 处 str.format 根除」：四套 prompt、
    #   确定性定稿、开关唯一生效点、三处接线、四处 fill 落点
    ("§18.12 假设生成角色 prompt", "common/src/core/hypothesis_roles.py", 38, 38,
     "PROMPT_HYPOTHESIS"),
    ("§18.12 代码实现角色 prompt", "common/src/core/hypothesis_roles.py", 53, 53, "PROMPT_CODER"),
    ("§18.12 批判者角色 prompt", "common/src/core/hypothesis_roles.py", 73, 73, "PROMPT_CRITIC"),
    ("§18.12 反思者角色 prompt", "common/src/core/hypothesis_roles.py", 100, 100, "PROMPT_REFLECT"),
    ("§18.12 fill 走哨兵不用 format", "common/src/core/hypothesis_roles.py", 90, 90, "def fill"),
    # 件1（10-01 19:4x）：10-01「乙」让 ma/std/max/min 收 Series，但算子表还写着 ma(df, n)
    # ⇒ 模型不知道能传列。这六针钉的是「五处提示词都点名了」+「自检脚本还在」；
    #    谁把这句话摘掉，这里当场红。
    ("§17 提示词点名传列·四角色代码实现", "common/src/core/hypothesis_roles.py", 58, 58, "可以直接传列"),
    ("§17 提示词点名传列·单角色原路", "common/src/core/llm_factor_agent.py", 24, 24, "可以直接传列"),
    ("§17 提示词点名传列·遗传种子", "common/src/core/llm_genetic_hybrid.py", 27, 27, "可以直接传列"),
    ("§17 提示词点名传列·融合交叉", "common/src/core/llm_crossover_operator.py", 18, 18, "可以直接传列"),
    ("§17 提示词点名传列·变异", "common/src/core/llm_mutation_operator.py", 18, 18, "可以直接传列"),
    ("§17 传列点名自检入口（抠正文真求值）", "etf/v1/temp/check_prompt_column_arg_1001.py", 68, 68, "def main"),
    ("§18.12 静态体检（10-01 移到 factor_static_check）", "common/src/core/factor_static_check.py", 64, 64, "def check_expr"),
    ("§18.12 未来函数硬拒（10-01 移到 factor_static_check）", "common/src/core/factor_static_check.py", 47, 47, "def check_lookahead"),
    ("§18.12 开关唯一生效点（活读不快照）", "common/src/core/hypothesis_roles.py", 150, 151,
     "bool(HYPOTHESIS_ROLES) and self._llm.enabled"),
    ("§18.12 开关默认空＝关", "common/src/config_base.py", 537, 537, 'd["HYPOTHESIS_ROLES"]'),
    ("§18.12 接线：闸开走四角色", "common/src/core/llm_factor_agent.py", 83, 85,
     "self.gate.enabled"),
    ("§18.12 丙：闸关原路那处 fill", "common/src/core/llm_factor_agent.py", 99, 99,
     "fill(SYSTEM_PROMPT"),
    ("§18.12 反思者顶掉旧的一行反馈", "common/src/core/llm_factor_agent.py", 146, 148,
     "gate.reflect"),
    ("§18.12 收尾打印账单", "common/src/core/llm_factor_agent.py", 166, 167, "假设闸账单"),
    ("§18.12 甲-2 genetic SEED 那处 fill", "common/src/core/llm_genetic_hybrid.py", 91, 91,
     "fill(SEED_SYSTEM_PROMPT"),
    ("§18.12 甲-3 FEEDBACK 载荷 replace", "common/src/core/llm_genetic_hybrid.py", 106, 107,
     "<<FRONT>>"),
    ("§18.12 甲-3 这支原来静默、现在出声", "common/src/core/llm_genetic_hybrid.py", 113, 113,
     "互补种子生成失败"),
    ("§18.12 甲-4 planner RESEARCH 那处 fill", "common/src/optimizer/llm_research_planner.py",
     44, 44, "fill(RESEARCH_PROMPT"),
    ("§12.2 股票线只报 max_drawdown", "stock/v1/src/run_ashare_portfolio_eval.py", 180, 180, "max_drawdown"),
    ("§8.2 0.02 只是描述性列", "stock/v1/src/run_ashare_factor_eval.py", 193, 193, "0.02"),
    # ↓ §8.4「稳不稳」秤（09-26 新增）：秤自己的四个窗口参数 + 与环1 同一把尺子的两处落点
    ("§8.4 滚动一年窗", "stock/v1/src/config/config.py", 72, 72, "ASHARE_ROLL_WINDOW"),
    ("§8.4 等分折数", "stock/v1/src/config/config.py", 74, 74, "ASHARE_ROLL_FOLDS"),
    ("§8.4 近期窗", "stock/v1/src/config/config.py", 76, 76, "ASHARE_ROLL_RECENT_YEARS"),
    ("§8.4 环1 序列交出口=可选参数", "stock/v1/src/run_ashare_factor_eval.py", 137, 137, "series_sink=None"),
    ("§8.4 环1 序列写入点", "stock/v1/src/run_ashare_factor_eval.py", 185, 185, "series_sink[name]"),
    ("§8.4 判据 A 容差 1e-15", "stock/v1/src/run_ashare_rolling_ic.py", 79, 79, "1e-15"),
    ("§8.4 判据 A 函数（序列 vs 环1 指标行，同一次运行）", "stock/v1/src/run_ashare_rolling_ic.py", 209, 211, "def check_same_ruler"),
    ("§8.4 读数 B 分叉线 5e-4", "stock/v1/src/run_ashare_rolling_ic.py", 84, 84, "ARCHIVE_DRIFT_MAX"),
    ("§8.4 读数 B 函数（跨运行过期表读数，不是判据）", "stock/v1/src/run_ashare_rolling_ic.py", 244, 246, "def report_archive_drift"),
    ("§8.4 逐年极值列=只说高低（lo/hi 不是 best/worst）", "stock/v1/src/run_ashare_rolling_ic.py", 173, 174, "lo_ic_year"),
    # 10-01 丙：看板的稳不稳那栏之上插了 9 行（`fold_n` 版本戳警告）⇒ lo/hi 投影那条锚
    #          1054-1056 → **1060-1064**；同批新增两条（警告本体、归档列的写入点）。
    ("§10 看板稳不稳那栏投影了 lo/hi 两对列", "stock/v1/src/app.py", 1060, 1064, "hi_ic_year"),
    ("§8.4 看板的「归档与配置不同版」警告（读 fold_n 现算）", "stock/v1/src/app.py", 1054, 1056,
     "ASHARE_ROLL_FOLDS"),
    ("§8.4 归档列 fold_n 的写入点（入口写、不手填）", "stock/v1/src/run_ashare_rolling_ic.py",
     178, 178, "fold_n"),
    ("§10 看板 8 tab 顺序", "stock/v1/src/app.py", 607, 609, "st.tabs"),
    ("§10 看板口径页", "stock/v1/src/app.py", 607, 609, "📖 口径"),
    # ↓ §9.1 涨停裕度 0.95 专项（09-27）：同一根阈值喂的两道判据 + 审计侧那根 0.99
    ("§9.1 裕度阈值单点 gate_vector", "stock/v1/src/strategy/ashare_screen.py", 547, 547,
     "def gate_vector"),
    ("§9.1 名单侧同日刀口（贴板不进候选）", "stock/v1/src/strategy/ashare_screen.py", 955, 955,
     "chase = traded & chg.ge(lim)"),
    ("§9.1 回放侧次日跳空（第 4 道闸）", "stock/v1/src/strategy/ashare_screen.py", 602, 602,
     "gap = mtx"),
    ("§9.1 审计 0.99 判物理封死≠执行闸 0.95", "stock/v1/src/run_ashare_daily_audit.py", 60, 60,
     "NEAR = 0.99"),
    # 09-30：第三条线 live2etf 按用户裁「不留」删档 ⇒ AGENT.md §19 整段与这里的 20 行引证一并撤下
    # （那 20 行指向的 live2etf/ 目录本机与 git 里都不存在，红账常年 17 条就是这么来的）。
    # 09-30 丙-2（用户裁）：折内停用 official 源 + 那颗开关进断点缓存指纹
    ("§18.11 丙-2 折内剔 official 那道闸", "common/src/core/multi_source_mining.py", 174, 176,
     'sources = [s for s in sources if s != "official"]'),
    ("§18.11 丙-2 fold 从入口穿到多源", "etf/v1/src/main.py", 178, 178,
     "mine_factors_multi_source(pool, fold=fold)"),
    ("§18.11 丙-2 facade 透传 fold", "common/src/core/rdagent_facade.py", 56, 60,
     "multi_source_mine(pool, fold=fold)"),
    ("§18.11 丙-2 本线开关=折内停 official", "etf/v1/src/config/config.py", 321, 321,
     'MULTI_SOURCE["official_in_fold"] = False'),
    ("§18.11 丙-2 底座默认照旧吃 official", "common/src/config_base.py", 147, 147,
     '"official_in_fold": True'),
    ("§18.11 丙-2 开关进指纹（防续传空转）", "etf/v1/src/run_checkpoint.py", 59, 59,
     '"official_in_fold"'),
    # 09-25：§3.1「qlib 的真实出场面」五处里的后四处 + 两处对拍状态（旧措辞"只用数据格式"被实测否掉）
    ("§3.1 面板生成器 import qlib", "common/rdagent_docker/pregen_source_data.py", 96, 96,
     "import qlib"),
    ("§3.1 面板生成器走 D.features", "common/rdagent_docker/pregen_source_data.py", 165, 166,
     "D.features"),
    ("§3.1 容器内 qlib 回测的指标落点", "common/src/core/rdagent_driver.py", 176, 176,
     "experiment.result"),
    ("§3.1 Alpha158 特征器来自 qlib", "stock/v1/temp/alpha158_scope_0926.py", 18, 19,
     "Alpha158DL"),
    ("§3.1 截面对拍=与 qlib 报表口径对齐 0.029", "stock/v1/src/run_ashare_factor_eval.py",
     13, 13, "0.029"),
    # 09-27：§3 技术栈 LLM 行的措辞换成了「两线都必须关思考」+「股票线其实没有调用点」。
    # 那一行里唯一的实测数字是这两个秒数 ⇒ 锚在验证日志上。重跑 `verify_stock_llm_gates_0927.py`
    # 会覆写这份日志、数字一变这条就红 —— 这是想要的行为（数字变了文档就得跟着改），不是噪音。
    ("§3 LLM 行：不注入闸门=重试到 271.5s 仍零正文", "stock/v1/temp/verify_stock_llm_gates_0927.log",
     5, 5, "271.5"),
    ("§3 LLM 行：注入 effort=none 后 10.5s 出可解析正文",
     "stock/v1/temp/verify_stock_llm_gates_0927.log", 6, 6, "10.5"),
    # 09-27：用户裁决「用 LLM 出名单」后的首次实测（判据零改动，结论用来拦住下一次接线）。
    # 同上一条口径：`probe_llm_picks_0927.py` 若带 `>` 重跑，池子/模型一变这几行就红 —— 想要的行为。
    # 但注意第 17 行那个秒数是**冷加载**档（`/api/ps` 空时才成立），热态重跑必然变数 ⇒ 这条最易红。
    ("§3 LLM 行：模型 6/6 次同时破两道分散硬闸", "stock/v1/temp/probe_llm_picks_0927.log",
     16, 16, "家电行业"),
    ("§3 LLM 行：首打 330.4s 含 6.07GB 权重装载", "stock/v1/temp/probe_llm_picks_0927.log",
     17, 17, "330.4"),
    # 10-01 乙：§17「写法坑」那行引用的四处落点。动的是两线共用的求值层，
    # 这几条钉住「归一化函数存在 + 四个算子真的走它 + 回归件与牙都在」。
    ("§17 写法坑：归一化单点 `_price_series`", "common/src/core/factor_dsl.py", 21, 21,
     "def _price_series"),
    ("§17 写法坑：DataFrame 取 close、Series 原样用", "common/src/core/factor_dsl.py", 36, 36,
     "isinstance(x, pd.DataFrame)"),
    ("§17 写法坑：`ma` 确实经过归一化", "common/src/core/factor_dsl.py", 153, 153,
     "_price_series(x).rolling"),
    ("§17 写法坑：同进程两臂对拍入口（默认该用的那条）",
     "etf/v1/temp/dsl_priceop_baseline_1001.py", 146, 146,
     "def run_both()"),
    ("§17 写法坑：跨进程 baseline/after 两遍入口（留作复核）",
     "etf/v1/temp/dsl_priceop_baseline_1001.py", 189, 189,
     "def run(phase)"),
    ("§17 写法坑：回归件正闸（两种写法都收）", "etf/v1/tests/test_factor_dsl.py", 35, 35,
     "def test_price_window_operators_accept_dataframe_and_series"),
    ("§17 写法坑：退回旧版必判红那格", "etf/v1/tests/test_factor_dsl.py", 87, 87,
     "def test_old_df_only_behaviour_would_have_rejected_series"),
    ("§17 写法坑：给对拍装牙那五表", "etf/v1/temp/dsl_priceop_teeth_1001.py", 69, 69,
     "T1 牙在"),
    ("§17 写法坑：未来函数值多少钱（三臂同池 + 标签正对照）",
     "etf/v1/temp/lookahead_price_1001.py", 40, 40, "V0 在库原样"),
    ("§17 写法坑：库对放宽的依赖读数（撤销即不可求值）",
     "etf/v1/temp/dsl_series_dep_scan_1001.py", 62, 62, "旧臂：只认 DataFrame"),
    ("§17 污染面 R1：席位取前 10 那三行", "etf/v1/src/run_live.py", 161, 162,
     "active.sort"),
    ("§17 污染面 R2：等权分母与逐标的打分循环", "etf/v1/src/run_live.py", 184, 201,
     "w_sum = sum(ws.values())"),
    ("§17 污染面：整张入口（三臂 + 两道牙 + 只读快照）",
     "etf/v1/temp/lookahead_contamination_trace_1001.py", 82, 158,
     "def _score_pool"),
    # ↓ 10-01 §17「甲：只接闸，不动库」——写库单点上的静态闸
    ("§17 接闸：尺子的新落点（写库/定稿共用）",
     "common/src/core/factor_static_check.py", 64, 64, "def check_expr"),
    ("§17 接闸：写库单点那一道", "common/src/core/factor_library.py", 54, 63,
     'self.p.get("static_gate"'),
    ("§17 接闸：空 expr 那一格豁免", "common/src/core/factor_library.py", 43, 53,
     "def _static_gate"),
    ("§17 接闸：开关与默认值", "common/src/config_base.py", 366, 367,
     'env("LIBRARY_STATIC_GATE", "1")'),
    ("§17 接闸：日更第③段的计数只数真写进去的", "etf/v1/src/main.py", 334, 342,
     "written += bool(lib.upsert"),
    ("§17 接闸：夹具（放行/判红/开关/牙）",
     "etf/v1/tests/test_library_static_gate.py", 1, 124, "def test_honest_lag_is_admitted"),
    ("§17 接闸：走真入口那格",
     "etf/v1/tests/test_library_static_gate.py", 110, 124,
     "def test_production_stage_function_blocks_it"),
    ("§17 接闸：e2e 账单（真库 45 行走真 upsert，四臂）",
     "etf/v1/temp/library_gate_e2e_1001.py", 64, 86, "def arm(tag, extra)"),
]


def main():
    bad, warn = [], []
    for sec, rel, lo, hi, needle in CHECKS:
        p = ROOT / rel
        if not p.exists():
            bad.append((sec, rel, lo, needle, "<文件不存在>"))
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if not (1 <= lo <= hi <= len(lines) + 5):
            bad.append((sec, rel, lo, needle, f"<行号越界，文件共 {len(lines)} 行>"))
            continue
        seg = "\n".join(lines[lo - 1:hi])
        if needle not in seg:
            bad.append((sec, rel, lo, needle, seg.strip()[:120].replace("\n", " ⏎ ")))
        else:
            warn.append(f"  OK  {sec:<26} {rel}:{lo}-{hi} ∋ {needle}")
    print("\n".join(warn))
    print(f"\n共 {len(CHECKS)} 条引证，通过 {len(CHECKS) - len(bad)}，不通过 {len(bad)}")
    for sec, rel, lo, needle, got in bad:
        print(f"  ✗ {sec}  {rel}:{lo} 期望 {needle!r}\n    实际: {got}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
