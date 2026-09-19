# ai.shensuan — ETF 量化交易辅助系统（v1）

基于 RD-Agent(Q) 思路的场内 ETF 量化研究与实盘辅助系统：LLM 驱动因子挖掘 → 正交化 → 回测验证（DSR/PBO/Walk-forward）→ 实盘信号 → 反馈闭环。支持日线与分钟线双频率。

## 目录结构

```
etf/v1/
├── README.md                  # 本文档
├── requirements.txt           # 依赖
├── report/                    # 报告输出目录（自动生成，人类可读报告统一落盘于此）
└── src/                       # 全部代码，运行入口均在 src/ 下
    ├── _bootstrap.py          # 将 10 个子目录挂到 sys.path（裸模块名导入的前提）
    ├── main.py                # 回测主流程（研究 1→7 全 pipeline）
    ├── run_daily_backtest.py  # 日线回测入口
    ├── run_minute_backtest.py # 分钟线回测入口
    ├── compare_freq.py        # 日线 vs 分钟线对比 + 持仓相似度
    ├── run_live.py            # 实盘/纸面盘
    ├── run_feedback.py        # 实盘反馈闭环分析
    ├── run_monthly.py         # 月度复盘
    ├── run_multi_gen.py       # 多 LLM 生成日报并投票
    ├── scheduler.py           # 定时调度
    ├── app.py                 # 研究看板（streamlit）
    ├── app_feedback.py        # 反馈看板（streamlit）
    ├── app_live.py            # 实盘看板（streamlit）
    ├── config/                # config.py 全局配置、config_live.py 实盘配置
    ├── data/                  # akshare 数据获取、ETF 池筛选
    ├── core/                  # 因子计算、DSR、PBO、归因
    ├── strategy/              # 策略构建、多策略、生命周期
    ├── backtest/              # 日线/分钟线回测器
    ├── optimizer/             # LLM 参数/正交化/联合优化
    ├── live/                  # 实盘下单与风控
    ├── feedback/              # 滑点/延迟/换手反馈
    ├── report/                # 日报/月报/季报、自评、A/B 测试、多 LLM 投票
    └── view/                  # 聚类/归因/进化动画等可视化
```

## 1. 环境安装

要求 **Python ≥ 3.10**（akshare 新版不支持 3.8）。

```bash
pip install -r requirements.txt
```

核心依赖：pandas / numpy / scipy / akshare / scikit-learn / plotly / streamlit / openai / tenacity。
可选依赖：lightgbm、shap、statsmodels（衰减预测与归因增强）、kaleido + imageio（动画导出 GIF/MP4）。

LLM 功能（因子挖掘、报告生成、自评）需要配置 API Key 环境变量：

```bash
export OPENAI_API_KEY=***        # gpt-4o / gpt-4o-mini
export DEEPSEEK_API_KEY=***  # deepseek-chat
export DASHSCOPE_API_KEY=***     # qwen-max
```

未配置 Key 时研究 pipeline 会降级为规则/随机变异路径，仍可完成回测。

## 2. 频率机制（ETF_FREQ）

`src/config/config.py` 中 `FREQ = os.environ.get("ETF_FREQ", "daily")`，支持
`daily | 1min | 5min | 15min | 30min | 60min`，并据此自动推导 `LOOKBACK_BARS`、
IC 阈值、风控间隔等参数。

- **推荐方式**：直接运行包装入口脚本，它们会先设置 `ETF_FREQ` 再执行主流程；
- 手动方式：`ETF_FREQ=5min python main.py`（在 `src/` 目录下执行）；
- 输出文件名自动带频率后缀（见第 5 节对照表）。

所有脚本都必须在 **`etf/v1/src/` 目录下**运行（项目使用裸模块名导入，由 `_bootstrap.py` 挂路径）。

## 3. 回测运行

```bash
cd etf/v1/src

# 日线回测（回测区间 2010-01-01 ~ 2024-12-31，见 config.py BACKTEST_START/END）
python run_daily_backtest.py

# 分钟线回测
python run_minute_backtest.py

# 双频率结果对比 + 持仓相似度
python compare_freq.py
```

流程：拉取 ETF 池日线/分钟线（akshare，缓存于 `src/data_cache/`）→ LLM 假设生成与因子挖掘（含遗传编程/多目标 GP）→ 正交化 → walk-forward 组合优化 → 回测 → DSR/PBO 显著性检验 → 落盘结果文件。

> 2010 年起点的分钟线数据量很大（每条约 15 年 × 240 bar），首次拉取耗时较长且依赖东财接口稳定性；部分标的拉取失败时系统会自动降级跳过。

## 4. 报告目录（etf/v1/report）

所有人类可读报告与评估产物统一输出到 `etf/v1/report/`，可用环境变量 `ETF_REPORT_DIR` 覆盖。机器输入数据（`live_data/feedback_report.json`、`live_orders.csv` 等原始记录）仍保留在 `src/live_data/`。

| 文件 | 生产者 |
| :--- | :--- |
| `feedback_report.md` | `python run_feedback.py` |
| `report_daily.md` | LLM 日报（`run_multi_gen.py` 多模型投票产出胜出版） |
| `report_monthly.md` / `monthly_metrics.json` | `python run_monthly.py` |
| `self_eval_summary.json` | 报告自评（self_evaluator） |
| `voting_eval_summary.json` | 多 LLM 投票评估 |
| `ab_test_report.md` | 提示词 A/B 测试 |
| `multi_gen/` | 各 LLM 候选日报与盲评中间件 |

## 5. 输出文件对照（src/ 下）

| 频率 | 净值 | 交易 | 信号 | DSR | 参数 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| daily | equity_daily.csv | trades_daily.csv | signals_daily.csv | dsr_daily.csv | optimized_params_daily.json |
| 1min | equity_1min.csv | trades_1min.csv | signals_1min.csv | dsr_1min.csv | optimized_params_1min.json |
| 兼容（最近一次运行） | equity.csv | trades.csv | signals.csv | dsr.csv | optimized_params.json |

对比类：`freq_comparison.csv`（双频率绩效对比）、`holdings_similarity.json`（持仓相似度）。

## 6. 持仓相似度指标

`compare_freq.py` 在双频率回测均完成后运行：

- 从 `signals_daily.csv` / `signals_1min.csv` 提取**每日收盘持仓**（当天最后一个非空 `target_code`）；
- 按日期对齐后计算：
  - **总相似度**：全部对齐日中两频率持仓一致的比例（含双方都空仓的日子）；
  - **持仓日相似度**：仅统计至少一个频率持仓的日子；
  - **双边持仓日相似度**：仅统计两个频率同时持仓的日子（衡量"选股是否一致"）；
  - **持仓占比_a / _b**：各自的持仓时间占比（衡量"仓位节奏是否一致"）；
  - **不一致示例**：最多 20 条分歧日明细。
- 结果打印并保存到 `holdings_similarity.json`，研究看板「📊 频率对比」页直接展示。

用途：分钟线相对日线应只有更细的执行粒度；若双边持仓日相似度很低，说明频率差异改变了信号本身（因子对 bar 粒度敏感），需要复核策略的可迁移性。

## 7. 看板

```bash
cd etf/v1/src
streamlit run app.py            # 研究看板：净值/交易/DSR/信号持仓/聚类/归因/衰减/GP/频率对比（含持仓相似度）
streamlit run app_feedback.py   # 反馈看板：滑点/延迟/换手/因子淘汰/日报月报/自评/投票/A-B 测试
streamlit run app_live.py       # 实盘看板：持仓、订单、风控事件
```

## 8. 实盘与反馈闭环

```bash
cd etf/v1/src
python run_live.py        # 实时信号 + 下单（paper 模式无需券商接口）
python run_feedback.py    # 成交回写 → 滑点/延迟/换手/因子反馈 → report/feedback_report.md
python run_monthly.py     # 月度复盘报告
python run_multi_gen.py   # 多 LLM 并发生成日报 → 盲评投票 → 胜出版写入 report/report_daily.md
python scheduler.py       # 定时调度以上任务
```

### 实盘配置（Windows，`src/live/config_live.py`）

```python
ACCOUNT = {
    "broker": "paper",            # paper | qmt | easytrader
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

`paper` 模式使用内置纸面撮合，无需券商环境，适合在 Linux/macOS 验证全流程。

### 多 LLM 模型池（`src/report/multi_llm_voter.py` 中 `DEFAULT_MODELS`）

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

## 9. 常见问题

- **ImportError / 找不到模块**：确认当前目录是 `etf/v1/src`，且经由入口脚本（内部 `import _bootstrap`）运行。
- **akshare 安装失败**：Python 需 ≥ 3.10；`pip install -U akshare`。
- **行情拉取超时/连接重置**：东财接口偶发波动，重跑即可；已拉取数据缓存在 `src/data_cache/`，不会重复请求。
- **pandas FutureWarning（factor_orthogonal stack）**：无害提示，不影响结果。
- **修改回测区间/费率/风控**：集中在 `src/config/config.py`（`BACKTEST_START/END`、`COMMISSION_RATE`、`RISK_CONTROL` 等）。
