# -*- coding: utf-8 -*-
"""实盘配置

模拟盘/实盘总开关（默认全部关闭，安全侧默认走模拟盘）：
- ACCOUNT["broker"]："paper"（模拟盘，默认）| "qmt" | "easytrader"；
- MODE["live_trading"]：是否实盘账户；
- MODE["auto_order"]：False 时一切订单只记 DRY_RUN，不触真实委托；
两者需同时为 True 才会经券商接口真实下单。"""

from config import LIVE_DATA_DIR

ACCOUNT = {
    "broker": "paper",   # paper | qmt | easytrader（默认模拟盘）
    "account_id": "test_001",
    "initial_capital": 10_000,
    "qmt": {
        "mini_qmt_path": r"C:\国金QMT\userdata_mini",
        "account": "888888888",
        "account_type": "STOCK"},
    "easytrader": {
        "broker": "ths", "user": "your_username",
        "password": "your_password",
        "exe_path": r"C:\同花顺\xiadan.exe"},
}

MODE = {
    "live_trading": False,
    "auto_order": False,
    "dry_run": True,
    "notify_only": True,
}

MARKET_DATA = {
    "source": "sina",
    "freq": "1min",
    "reconnect_seconds": 5,
    "max_reconnect": 10,
    "stale_seconds": 60,
    "use_cache_on_disconnect": True,
}

LIVE_RISK = {
    "enabled": True,
    "max_position_ratio": 0.95,
    "min_cash_reserve": 100,
    # 换手闸门按「Σ成交金额/净值」累计（买卖都计，不折半）。截面组合一次完整
    # 调仓是「清掉出名单的 + 买入新名单的」≈ 2×在仓比例，取 1.0 会在换仓当天
    # 拒掉后半数订单、留下一个半成品组合——闸门必须容得下形态本身要做的动作。
    # 研究侧单次调仓上限是 Σ|Δ金额|/(2·净值) ≤ PORTFOLIO["max_turnover"]=0.60，
    # 折到本口径 = 1.2，此处留 2.0 覆盖「同日多次小步调仓」的累计量。
    # 真正的频控由 max_orders_per_day + max_orders_per_minute + cooldown 承担。
    "max_daily_turnover": 2.0,
    "max_order_amount": 9000,
    "min_order_amount": 100,
    "max_orders_per_day": 10,
    "max_orders_per_minute": 3,
    "max_slippage": 0.003,
    "price_limit_ratio": 0.099,
    "daily_stop_loss": -0.03,
    "max_drawdown_stop": -0.10,
    "cooldown_minutes": 30,
    "require_double_check": True,
    "double_check_amount": 5000,
}

LIVE_ATTRIBUTION = {
    "enabled": True,
    "snapshot_interval_minutes": 5,
    "compare_with_backtest": True,
    "save_dir": LIVE_DATA_DIR,
}

NOTIFY = {
    "enabled": True,
    "channel": "console",
    "webhook": "",
    "on_order": True,
    "on_risk": True,
    "on_error": True,
}

STORAGE = {
    "dir": LIVE_DATA_DIR,
    "orders": "live_orders.csv",
    "positions": "live_positions.csv",
    "signals": "live_signals.csv",
    "risk_events": "live_risk_events.csv",
    "equity": "live_equity.csv",
    "attribution": "live_attribution.csv",
}