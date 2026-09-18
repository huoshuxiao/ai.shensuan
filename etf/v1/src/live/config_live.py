# -*- coding: utf-8 -*-
"""实盘配置"""

ACCOUNT = {
    "broker": "paper",
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
    "max_daily_turnover": 0.5,
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
    "save_dir": "live_data",
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
    "dir": "live_data",
    "orders": "live_orders.csv",
    "positions": "live_positions.csv",
    "signals": "live_signals.csv",
    "risk_events": "live_risk_events.csv",
    "equity": "live_equity.csv",
    "attribution": "live_attribution.csv",
}