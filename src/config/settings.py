"""全局配置：因子权重、阈值、风控参数、标的池"""
from pathlib import Path

# 项目根目录（src/config/settings.py -> 根目录）
BASE_DIR = Path(__file__).resolve().parents[2]

# 输出报告目录
REPORT_DIR = BASE_DIR / "output" / "reports"

# 状态文件路径
STATE_FILE = BASE_DIR / "storage" / "position_state.json"

# 数据缓存目录
CACHE_DIR = BASE_DIR / "data_cache"

# ETF 基础数据文件（第一列代码，第二列名称）
ETF_BASE_CSV = BASE_DIR / "data" / "base" / "etf_base.csv"


class Settings:
    """策略全局参数（可按需调整）"""

    # ---- 资金 ----
    initial_capital: float = 10000.0  # 初始资金（元）

    # ---- 数据 ----
    history_days: int = 120        # 日线历史数据天数
    rate_limit_interval: float = 0.8   # 数据请求最小间隔（秒），合规限频
    max_retries: int = 3           # 请求失败最大重试次数
    # 数据源配置（按顺序尝试，前面的优先）：akshare=东财/新浪，tencent=腾讯行情
    data_sources: list = ["akshare", "tencent"]

    # ---- 标的池（可配置：候选代码列表，名称自动从 data/base/etf_base.csv 解析）----
    etf_base_csv: str = str(ETF_BASE_CSV)   # ETF 基础数据文件路径
    etf_candidates: list = [               # 参与评分的候选池
        "510300", "510050", "510500", "512100", "159915", "159949", "588000",
        "512480", "515030", "515790", "512010", "159928", "512690", "512660",
        "512800", "159819", "512880",
    ]
    wide_base_codes: list = [              # 宽基代码集合（其余候选归为行业）
        "510300", "510050", "510500", "512100", "159915", "159949", "588000",
    ]

    # ---- 因子组合（可配置：键=因子名，值=权重；权重<=0 则不启用该因子）----
    factor_weights: dict = {
        "sentiment": 0.20,         # 市场情绪因子（逆向）
        "oversold": 0.25,          # 超跌反弹因子
        "timing": 0.20,            # 择时因子
        "position": 0.15,          # 仓位控制因子
        "downtrend_guard": 0.20,   # 防越低越买（趋势保护）因子
    }

    # ---- 评分与选择 ----
    score_threshold: float = 60.0   # 综合评分阈值，低于该值空仓
    score_range: tuple = (0.0, 100.0)  # 因子得分范围

    # ---- 风控 ----
    take_profit_pct: float = 0.05   # 止盈线 +5%
    stop_loss_pct: float = -0.03    # 止损线 -3%
    max_position_pct: float = 1.0   # 每次只持有一只ETF，单票仓位上限（占计划资金比例）
    cooldown_days: int = 1          # 连续亏损后的冷却天数

    # ---- 数据校验 ----
    max_daily_price_jump: float = 0.20  # 单日价格跳变超过 20% 视为异常
    min_history_bars: int = 30      # 少于 30 根K线不参与评分
    max_missing_pct: float = 0.05   # 缺失比例超过 5% 跳过该标的

    # ---- 回测 ----
    backtest_start: str = "2026-01-01"     # 回测开始日期
    backtest_end: str = "2026-09-12"       # 回测结束日期（非交易日自动截止到最后交易日）
    backtest_history_days: int = 300       # 回测数据窗口（自然日，覆盖因子预热期：MA60 需 65 根K线）
    commission_pct: float = 0.0003         # 单边交易佣金（万3），买卖成交时扣除


# 全局单例
settings = Settings()
