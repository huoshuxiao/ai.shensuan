# ai.shensuan

## 实盘配置 windows

```python

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
```

## MODELS
```python
DEFAULT_MODELS = [
    {"name": "gpt-4o-mini", "provider": "openai",
     "env_key": "OPENAI_API_KEY", "weight": 1.0},
    {"name": "gpt-4o", "provider": "openai",
     "env_key": "OPENAI_API_KEY", "weight": 1.5},
    {"name": "deepseek-chat", "provider": "deepseek",
     "env_key": "DEEPSEEK_API_KEY",
     "base_url": "https://api.deepseek.com", "weight": 1.0},
    {"name": "qwen-max", "provider": "qwen",
     "env_key": "DASHSCOPE_API_KEY",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "weight": 1.0},
]
```

---

## 运行手册

## 1 日线回测

```bash
# 方式 1：改 config.py
# FREQ = "daily"
python main.py
```

## 2 分钟线回测

```bash
# 方式 1：改 config.py
# FREQ = "1min"
python main.py
```

## 3 同时跑两种（推荐）

```bash
# 终端 1
python run_daily_backtest.py

# 终端 2
python run_minute_backtest.py

# 对比
python compare_freq.py

# 看板
streamlit run app.py
```

## 4 输出文件对照

| 频率 | 净值 | 交易 | 信号 | DSR | 参数 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| daily | equity_daily.csv | trades_daily.csv | signals_daily.csv | dsr_daily.csv | optimized_params_daily.json |
| 1min | equity_1min.csv | trades_1min.csv | signals_1min.csv | dsr_1min.csv | optimized_params_1min.json |
| 兼容 | equity.csv | trades.csv | signals.csv | dsr.csv | optimized_params.json |
