# ai.shensuan — ETF 量化交易辅助系统（v1）

基于 RD-Agent(Q) 思路的场内 ETF 量化研究与实盘辅助系统：LLM 驱动因子挖掘 → 正交化 → 回测验证（DSR/PBO/Walk-forward）→ 实盘信号 → 反馈闭环。支持日线与分钟线双频率。

> 详细操作请阅读 [用户使用手册](用户使用手册.md)。

## 目录结构

```
etf/v1/
├── README.md                  # 本文档
├── requirements.txt           # 依赖
├── pytest.ini                 # 测试配置（testpaths=tests）
├── report/                    # 报告输出目录（自动生成，人类可读报告统一落盘于此）
├── log/                       # 运行日志（自动生成，按入口+日期分文件，保留 30 天）
├── tests/                     # pytest 测试基线（离线合成数据，见 3.5 节）
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
    ├── optimizer/             # LLM 参数/正交化/联合优化、自动重挖触发器与跨运行状态
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

# 日线回测（回测区间 2010-01-01 ~ 今天，见 config.py BACKTEST_START/END；END 留空则自动取当天）
python3.10 run_daily_backtest.py

# 分钟线回测
python3.10 run_minute_backtest.py

# 双频率结果对比 + 持仓相似度
python3.10 compare_freq.py
```

流程（9 阶段，各环节由 config.py 中 enabled 开关控制，单环节异常自动跳过不中断主回测）：构建 ETF 池（按跟踪指数分组、每指数取一只代表 ETF）→ 拉取行情（akshare，缓存于 `data/cache/`）→ 因子挖掘（注册表基线 + 多源挖掘 + 单目标/多目标 GP + LLM-GP 混合）→ 因子库入库与聚类去重 → 正交化（LLM 联合优化/调参）→ 信号 → 回测 → DSR/PBO/Walk-forward/多策略验证 → 衰减/归因/SHAP 分析与自动重挖 → 落盘结果文件。详见[用户使用手册](用户使用手册.md)第 5 节。

Walk-forward 折内用哪些引擎挖因子见 3.3 节；自动重挖的跨运行状态见 3.4 节。

> 2010 年起点的分钟线数据量很大（每条约 15 年 × 240 bar），首次拉取耗时较长且依赖东财接口稳定性；部分标的拉取失败时系统会自动降级跳过。

### 3.1 多数据源自动降级

行情与 ETF 池均配置了备用数据源（`config.py` 中 `DATA_SOURCES`）：

| 场景 | 优先级链路 |
| :--- | :--- |
| 日线行情 | 东方财富（前复权）→ 新浪 ETF → 腾讯（注意腾讯量纲换算） |
| 分钟线行情 | 东方财富 → 腾讯（仅近期数据） |
| ETF 列表 | 东财现货 → 新浪现货 → 内置兜底池（15 只主流 ETF） |

单源失败自动切换并打印 `🔁 已降级到数据源 [x]`；全部失败才跳过该标的（`❌`）。

### 3.2 日志系统

所有入口（`main.py`、反馈、月报、实盘、对比、调度）启动时经 `src/log_kit.py` 的
`setup_logging(tag)` 双写：屏幕照常输出，同时逐行加时间戳落盘到
`etf/v1/log/{tag}_{YYYYMMDD}.log`（如 `research_daily_20260919.log`、`feedback_*.log`），
自动清理 30 天前的旧日志；目录可用 `ETF_LOG_DIR` 覆盖。
排查一次运行：`grep -E "⚠️|❌|Traceback" etf/v1/log/research_daily_*.log`。

### 3.3 Walk-forward 折内引擎（`WALK_FORWARD["fold_engines"]`）

滚动验证每一折**重新挖一遍因子**，用的引擎集合由配置决定，默认
`["registry", "genetic"]`（注册表基线 + 字符串 DSL 遗传编程）：

```python
WALK_FORWARD = {"enabled": True, "n_splits": 3, "train_ratio": 0.7,
                "embargo_bars": ..., "fold_engines": ["registry", "genetic"]}
```

- 折内若只跑注册表基线，样本外结果就完全覆盖不到实际入库的 GP/DSL/RD-Agent 因子，
  等于验了一批没人用的东西——因此折内与主链共用 `main.mine_with_engines()`；
- 可选引擎：`registry` / `genetic` / `multi_source` / `mogp` / `hybrid`。
  `multi_source` 含 RD-Agent/LLM 外部调用（每折一次），默认关闭；
- 折内产物按 `path_prefix` 落盘，不覆写主链结果：`data/results/walk_forward_fold{i}_genetic_history.csv`；
- `walk_forward_{FREQ}.csv` 新增「折内因子数」「折内因子来源」两列审计信息，
  试验次数由 `walk_forward_run` 逐折累加进 `TrialCounter`，供折内 DSR 复算使用。

### 3.4 自动重挖触发与跨运行状态（`data/cache/remining_state.json`）

「连续 N 轮指标恶化才重挖」这类判断必须建立在**上一次运行**的基础上，
而触发器对象每次进程都新建，纯内存计数等于每轮从零开始。现在状态落盘：

- `src/optimizer/remining_state.py` 的 `ReminingState` 按频率分桶（日线与分钟线的
  bar 量纲不同，混用会让冷却判断失真），原子写（`tmp` + `os.replace`），损坏文件容错重建；
- 路径 `REMINING_STATE_FILE`（默认 `data/cache/remining_state.json`，可用 `ETF_REMINING_STATE_FILE` 覆盖）；
- `DualIndicatorTrigger`（DSR/PBO 联动）与 `AutoReminingLoop`（重挖节流/冷却/最大轮数）
  **共用同一个 state 对象**，DSR/PBO 判定结果作为 `indicator` 直接传入重挖触发器，
  不再是互不通气的两道门；Δ 基准（上一轮的 DSR/PBO）同样从盘上取。
- 日志可观测：`DSR/PBO 联动: 本轮恶化=… 连续 x/N 轮 → 触发=…（原因）`、
  `未触发自动重挖：…（冷却中 / 已达最大重挖轮数 …）`。
- 真实全量跑已确认：`data/cache/remining_state.json` 按频率落桶
  （`{"daily": {"current_bar": 4060, "consecutive_bad_rounds": 0, "last_dsr": …,
  "last_pbo": …, "indicator_history": […]}}`），下一进程从该文件续算。

### 3.5 自动化测试与 CI

```bash
cd etf/v1
python3.10 -m pytest tests -q      # 95 项，全部离线（合成行情，不触网），约 8 秒
```

- `tests/conftest.py` 在导入任何项目模块**之前**把 `ETF_DATA_DIR/ETF_REPORT_DIR/ETF_LOG_DIR`
  指向临时目录、固定 `ETF_FREQ=daily`、清空 LLM 密钥，并关闭因子库 git 自动提交，
  所以测试不会污染 `data/`、也不会往仓库提交 `[factor-lib]`；
- `tests/synth.py` 提供确定性 GBM 合成池（代码用 `51030x`，避开 513/511/518 的 T+0 分支）；
- 覆盖范围：DSR/PBO/walk-forward 公式已知解、交易成本与 T+1 与风控档位、
  因子 DSL 与因子库读写；第 3.3/3.4 节两处改造的接线回归（折内引擎、重挖状态跨进程
  连续计数）；第 3.6 节四处衔接的回归见 `tests/test_orphan_wiring.py`；
- `.github/workflows/ci.yml`：改动 `etf/v1/**` 时以 Python 3.10 安装 `requirements.txt` 并跑同一套测试。

### 3.6 研究 → 实盘 / 反馈的四处衔接

代码里曾有一批「写好了但没人调用」的模块——结论停在原地，传不到执行端。现已全部接进入口：

| 衔接点 | 生产者 → 消费者 | 作用 |
| :--- | :--- | :--- |
| 调参产物 | `main.py` 写 `data/results/optimized_params_{FREQ}.json` → `run_live.py` 启动时 `load_optimized_config()` | 研究的 `risk_params` 按语义映射覆盖 `LIVE_RISK`（`single_position_max`→`max_position_ratio`、`max_trades_per_day`→`max_orders_per_day`、`cooldown_days`×24×60→`cooldown_minutes`，实盘熔断按挂钟时间判定故整日计入），因子 ICIR 权重驱动实盘打分，不再两套参数各跑各的 |
| 时点取数 | `data_loader.PointInTimeData` → 日线/分钟回测器 `_price()`、策略 `_score_one()` | 「只允许看到 `date` 及之前的 bar」收口到一个类，防未来函数只需审它 |
| 逐笔归因 | `live_attribution.decompose_live_vs_backtest()` → `run_feedback.py` 第 [2/6] 步 | 实盘成交与回测成交按**时间最近**配对，给出时滞 `lag_days`，用于区分分歧来自成交价还是成交时点；明细 `data/live/live_vs_backtest.csv` |
| 配置族 PBO | `strategy_pbo.build_risk_config_family()` × `pbo.collect_config_equities()` → `strategy_level_pbo()` | 见下 |

策略级 PBO 此前用「同一条净值的波动率降权伪变体」跑 CSCV，只衡量结论对微扰的敏感性，
不含「从搜索空间里挑了这档参数」的选择偏差。现在配置族取**真实回测过的参数组合**：
终态因子集（含研究侧 ICIR 权重）× 风控参数的 OFAT（一次只动一维）邻域
（`1 + Σᵢ(|RISK_SEARCH_SPACEᵢ|-1)` 档，上限 `STRATEGY_PBO["max_family_configs"]`，
按维度轮换截断，保证每维至少贡献一档）。基线档 `base` 必定在族内——它就是即将投产的那条净值。
日志与 `data/results/pbo_result.json` 会标出用的是哪一族：`配置族=real×11` 或 `配置族=pseudo×6`；
拿不到配置族（净值过短、回测全失败）时自动退化，不影响主流程。
真实池实测（20 只 ETF / 4060 bar / 11 档 OFAT）：`配置族=real×11 → PBO=0.2727 通过=True`，
同一净值的伪变体族给 `0.0000`——伪变体族把"挑参数"的成本完全漏计。

## 4. 报告目录（etf/v1/report）

所有人类可读报告与评估产物统一输出到 `etf/v1/report/`，可用环境变量 `ETF_REPORT_DIR` 覆盖。机器数据（回测产物、行情缓存、实盘原始记录、因子库）统一存放在 `etf/v1/data/`（见下节），`src/` 下只有代码。

| 文件 | 生产者 |
| :--- | :--- |
| `feedback_report.md` | `python3.10 run_feedback.py` |
| `report_daily.md` | LLM 日报（`run_multi_gen.py` 多模型投票产出胜出版） |
| `report_monthly.md` / `monthly_metrics.json` | `python3.10 run_monthly.py` |
| `self_eval_summary.json` | 报告自评（self_evaluator） |
| `voting_eval_summary.json` | 多 LLM 投票评估 |
| `ab_test_report.md` | 提示词 A/B 测试 |
| `multi_gen/` | 各 LLM 候选日报与盲评中间件 |

## 5. 数据目录（etf/v1/data）

所有运行产物统一写入 `etf/v1/data/`，可用环境变量 `ETF_DATA_DIR` 覆盖根路径；`src/` 目录只保留代码。

| 子目录 | 内容 |
| :--- | :--- |
| `data/results/` | 回测/研究产物：`equity_*` `trades_*` `signals_*` `dsr_*` `optimized_params_*` `walk_forward_*`（含折内产物 `walk_forward_fold{i}_*`）、因子聚类/归因/衰减、GP 进化历史、PBO、动画（html/gif/mp4/frames）、`freq_comparison.csv`、`holdings_similarity.json` |
| `data/live/` | 实盘原始记录（`live_orders.csv`、`live_attribution.csv`、`feedback_report.json`、`slippage_detail.csv` 等，反馈程序读入） |
| `data/cache/` | 行情缓存（`*.csv`）、`etf_universe_cache.csv`、`trial_counter.json`、`remining_state.json`（自动重挖跨运行状态，按频率分桶，见 3.4 节） |
| `data/library/` | 因子库：`factor_library.md` / `.csv` / `_index.json` / `archive/` |

按频率结果（`data/results/` 下）：

| 频率 | 净值 | 交易 | 信号 | DSR | 参数 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| daily | equity_daily.csv | trades_daily.csv | signals_daily.csv | dsr_daily.csv | optimized_params_daily.json |
| 1min | equity_1min.csv | trades_1min.csv | signals_1min.csv | dsr_1min.csv | optimized_params_1min.json |
| 固定名副本（最近一次运行） | equity.csv | trades.csv | signals.csv | dsr.csv | — |

对比类：`freq_comparison.csv`（双频率绩效对比）、`holdings_similarity.json`（持仓相似度）。

### 5.1 中文名与指标口径

- **因子**：`src/core/factor_naming.py` 统一维护中文名映射（内置因子、LLM 模板、GP/多目标/混合/RD-Agent 系列名及 DSL 表达式翻译）。`data/library/factor_library.md` 的 Top 表与详情、看板"因子空间/因子贡献/衰减"页、LLM SHAP 报告均展示中文名。
- **策略**：`src/strategy/strategy_naming.py` 将 `S3_gram_schmidt_sl0.02_ic_ir` 解析为"策略3·GS正交化·日止损2%·ICIR加权"；`multi_summary.csv` 附"中文名称"列。
- **指标解释**：`factor_library.md` 与各报告内置"指标说明"表（IC/ICIR/换手率/最大相关性/稳定性/简洁度/DSR/PBO 口径）；`data/results/multi_summary_指标说明.md` 解释策略表各列。

## 6. 持仓相似度指标

`compare_freq.py` 在双频率回测均完成后运行：

- 从 `signals_daily.csv` / `signals_1min.csv` 提取**每日收盘持仓**（当天最后一个非空 `target_code`）；
- 按日期对齐后计算：
  - **总相似度**：全部对齐日中两频率持仓一致的比例（含双方都空仓的日子）；
  - **持仓日相似度**：仅统计至少一个频率持仓的日子；
  - **双边持仓日相似度**：仅统计两个频率同时持仓的日子（衡量"选股是否一致"）；
  - **持仓占比_a / _b**：各自的持仓时间占比（衡量"仓位节奏是否一致"）；
  - **不一致示例**：最多 20 条分歧日明细。
- 结果打印并保存到 `data/results/holdings_similarity.json`，研究看板「📊 频率对比」页直接展示。

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
python3.10 run_live.py        # 实时信号 + 下单（paper 模式无需券商接口）
python3.10 run_feedback.py    # 成交回写 → 滑点/延迟/换手/因子反馈 → report/feedback_report.md
python3.10 run_monthly.py     # 月度复盘报告
python3.10 run_multi_gen.py   # 多 LLM 并发生成日报 → 盲评投票 → 胜出版写入 report/report_daily.md
python3.10 scheduler.py       # 定时调度以上任务
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
- **行情拉取超时/连接重置**：东财接口偶发波动，重跑即可；已拉取数据缓存在 `data/cache/`，不会重复请求。
- **pandas FutureWarning（factor_orthogonal stack）**：无害提示，不影响结果。
- **修改回测区间/费率/风控**：集中在 `src/config/config.py`（`BACKTEST_START/END`、`COMMISSION_RATE`、`RISK_CONTROL` 等）。
