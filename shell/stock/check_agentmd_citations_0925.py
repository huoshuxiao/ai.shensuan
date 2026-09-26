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
    ("§8.2 股票判重红线", "stock/v1/src/config/config.py", 82, 82, "ASHARE_RED_BAR"),
    ("§8.2 红线数值 0.99", "stock/v1/src/config/config.py", 82, 82, "0.99"),
    ("§9.2 双强度", "stock/v1/src/config/config.py", 260, 260, "ASHARE_BUY_MIN_HITS"),
    ("§9.2 分位阈值", "stock/v1/src/config/config.py", 139, 139, "ASHARE_SCREEN_QUANTILE"),
    ("§9.2 名单独立闸开关", "stock/v1/src/config/config.py", 153, 153,
     "ASHARE_BUY_EXTRA_RULES"),
    ("§9.2 名单独立闸判据本体", "stock/v1/src/strategy/ashare_screen.py", 268, 271,
     "BUY_EXTRA_RULES"),
    ("§9.2 名单独立闸单点函数", "stock/v1/src/strategy/ashare_screen.py", 701, 703,
     "def buy_extra_block"),
    ("§9.1 均额闸门", "stock/v1/src/config/config.py", 116, 117, "ASHARE_PORT_MIN_AMOUNT"),
    ("§9.1 涨停闸三档开关", "stock/v1/src/config/config.py", 322, 323, "ASHARE_TRADABLE_GATE"),
    ("§11 近涨停余量", "stock/v1/src/config/config.py", 281, 281, "ASHARE_LIMIT_NEAR"),
    ("§9.1 名单形状开关", "stock/v1/src/config/config.py", 199, 200, "ASHARE_LIST_SCHEME"),
    ("§9.1 板块席位", "stock/v1/src/config/config.py", 211, 212, "ASHARE_LIST_QUOTA"),
    ("§9.1 名单长度 50", "stock/v1/src/config/config.py", 169, 169, "ASHARE_BUY_TOP_N"),
    ("§9.1 席位单点出口", "stock/v1/src/strategy/ashare_screen.py", 731, 731, "def apply_board_quota"),
    ("§9.1 排队取名单", "stock/v1/src/strategy/ashare_screen.py", 818, 819, "def buy_candidates"),
    ("§9.1 口径措辞单点", "stock/v1/src/strategy/ashare_screen.py", 796, 796, "def list_desc"),
    ("§11 下单层贪心扫描", "stock/v1/src/strategy/ashare_screen.py", 1056, 1057, "def order_candidates"),
    ("§7 链路不含研究三入口", "stock/v1/src/run_ashare_daily_chain.py", 59, 60, "不含"),
    ("§18.8 链路 python3.10 硬编码", "stock/v1/src/run_ashare_daily_chain.py", 94, 94, "python3.10"),
    ("§7 链路该跑哪一场=日历驱动", "stock/v1/src/run_ashare_daily_chain.py", 162, 163, "def decide_session"),
    ("§7 链路①bin 日更", "stock/v1/src/run_ashare_daily_chain.py", 227, 227, "update_qlib_bin_daily"),
    ("§7 链路②重生成面板", "stock/v1/src/run_ashare_daily_chain.py", 267, 267, "PREGEN_PY"),
    ("§7 链路③盘前名单", "stock/v1/src/run_ashare_daily_chain.py", 277, 277, "daily_signal"),
    ("§7 链路④次日真账", "stock/v1/src/run_ashare_daily_chain.py", 307, 307, "run_ashare_daily_audit"),
    ("§6.3 bin 对齐率阈值", "stock/v1/src/data/update_qlib_bin_daily.py", 118, 120, "ALIGN_MIN"),
    ("§6.3 15:00 收盘闸", "stock/v1/src/data/update_qlib_bin_daily.py", 163, 164, "CLOSE_HM"),
    ("§6.3 未来场次闸（抓数之前）", "stock/v1/src/data/update_qlib_bin_daily.py", 306, 307, "def guard_session"),
    ("§6.3 复用缓存前对表", "stock/v1/src/data/update_qlib_bin_daily.py", 190, 191, "def align_against_bin"),
    ("§18.8 conda 候选路径", "common/src/core/official_rdagent.py", 21, 26, "conda"),
    ("§5.1 容器内 provider_uri", "common/src/config_base.py", 412, 416, "qlib_data"),
    ("§5.1 容器资源 shm/mem", "common/src/config_base.py", 438, 456, "4"),
    ("§12.3 重挖触发阈值", "common/src/config_base.py", 323, 330, "dsr_threshold"),
    ("§12.2 风控基线", "common/src/config_base.py", 293, 294, "daily_stop_loss"),
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
    ("§8.3 环3 候选路由护栏", "etf/v1/src/run_etf_redundancy_check.py", 54, 60, "cand"),
    ("§18.8 ETF bin 的 conda 绝对路径", "etf/v1/src/data/dump_qlib_bin.py", 256, 256, "miniconda3"),
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
    ("§10 看板稳不稳那栏投影了 lo/hi 两对列", "stock/v1/src/app.py", 1048, 1050, "hi_ic_year"),
    ("§10 看板 8 tab 顺序", "stock/v1/src/app.py", 606, 608, "st.tabs"),
    ("§10 看板口径页", "stock/v1/src/app.py", 606, 608, "📖 口径"),
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
