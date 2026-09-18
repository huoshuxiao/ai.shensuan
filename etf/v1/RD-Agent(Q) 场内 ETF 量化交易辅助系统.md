# RD-Agent(Q) 场内 ETF 量化交易辅助系统 — 完整文档

## 第一部分：需求文档（PRD）

### 1.1 项目背景

**个人投资者在 1 万元本金规模下，做场内 ETF 交易面临三大痛点：**

- 没有研究能力：无法持续挖掘有效因子
- 没有风控纪律：容易追涨杀跌
- 没有迭代机制：策略失效后无法自我修复

*本项目基于微软 RD-Agent(Q) 框架，构建一套「研究 → 实盘 → 反馈 → 代」的自动化量化交易辅助系统。*

### 1.2 目标用户

| 用户 | 需求 |
| :--- | :--- |
| 个人投资者 | 用 1W 本金自动化交易 ETF |
| 量化爱好者 | 学习完整的量化系统架构 |
| 小型团队 | 作为生产级系统原型 |

### 1.3 核心需求

#### 需求 1：因子自动挖掘

- 支持多源因子生成：官方 RD-Agent / LLM / 内置模板 / 遗传编程
- 多 LLM 并行生成 + 投票选优
- 因子正交化 + 相关性去重 + 聚类
- 5 目标遗传编程（IC / 换手 / 相关 / 稳定 / 简洁）
- 动态目标权重（探索→收敛→精炼）
- 因子库持久化（Markdown + JSON + Git 版本控制）

#### 需求 2：策略生成与优化

- LLM 联合优化正交化参数 + 风控参数
- 风险预算加权（IC / IC_IR / RiskParity）
- 多策略并行 + 策略级 PBO 过滤
- 动态策略淘汰（生命周期管理）
- 因子衰减预测 + 可解释性（SHAP）

#### 需求 3：回测与验证

- 多频率回测支持
- DSR（Deflated Sharpe Ratio）防过拟合
- PBO（Probability of Backtest Overfitting）CSCV
- Walk-forward 验证
- 因子归因（Shapley / LOO）

#### 需求 4：实盘接入

- 统一券商接口（QMT / easytrader / 模拟盘）
- 实时行情（Sina / Tencent / QMT）
- 风控前置（11 项硬约束 + 熔断）
- 实盘归因（实盘 vs 回测偏差分解）

#### 需求 5：反馈闭环

- 滑点 / 延迟 / 换手分析
- 自动更新 config.py（带备份 + 回滚）
- 策略级 + 因子级反馈

#### 需求 6：报告体系

- 日报 / 周报 / 月报 / 季报 / 年报
- LLM 生成人话版报告
- 多 LLM 投票评估 + A/B 测试
- Prompt 自动迭代

### 1.4 非功能需求

| 维度 | 要求 |
| :--- | :--- |
| 成本 | LLM 月度成本 < $5 |
| 延迟 | 日报生成 < 30 秒 |
| 可靠性 | 单点失败自动降级 |
| 可追溯 | 所有变更可回滚 |
| 安全 | 风控前置，代码 bug 不能直接下单 |
| 隐私 | 报告不含账户 ID / 密码 |
| 性能 | 日线回测 < 5 秒；分钟线 < 60 秒 |
| 一致性 | 同一策略在两种频率下应产生同方向的信号 |
| 可对比 | 看板支持"日线 vs 分钟线"并排对比 |

### 1.5 技术约束

- **本金**：1 万元
- **标的**：场内 ETF（每次仅持 1 只）
- **频率**：分钟线（T+0 支持则 T+0，否则 T+1）
- **数据源**：akshare / Sina / Tencent
- **LLM**：OpenAI / DeepSeek / Qwen
- **日线数据**：10年
- **分钟线数据**：10年
- **日线回测不需要 T+0 判定（天然 T+1）**

## 第二部分：整体架构

### 2.1 五层架构

```text
┌──────────────────────────────────────────────────────┐
│  L5: 决策层（人）                                     │
│  读报告 → 做决策 → 应用更新                          │
└──────────────────────────────────────────────────────┘
                    ↑↓
┌──────────────────────────────────────────────────────┐
│  L4: 报告层                                           │
│  日报 / 周报 / 月报 / 季报 / 年报                    │
│  多 LLM 生成 + 投票 + 自我评估 + Prompt 迭代          │
└──────────────────────────────────────────────────────┘
                    ↑↓
┌──────────────────────────────────────────────────────┐
│  L3: 实盘层                                           │
│  行情 → 信号 → 风控前置 → 券商                       │
│  实盘归因 → 反馈 → config 修正                       │
└──────────────────────────────────────────────────────┘
                    ↑↓
┌──────────────────────────────────────────────────────┐
│  L2: 研究层                                           │
│  多源因子挖掘 → 正交化 → 聚类 → 风险预算              │
│  多策略并行 → PBO → 生命周期 → 衰减预测              │
└──────────────────────────────────────────────────────┘
                    ↑↓
┌──────────────────────────────────────────────────────┐
│  L1.5: 回测层（新）                                   │
│  ┌────────────────┬────────────────────┐             │
│  │  日线回测       │   分钟线回测        │             │
│  │ DailyBacktester │ MinuteBacktester   │             │
│  └────────────────┴────────────────────┘             │
│  统一接口：BacktesterInterface                        │
└──────────────────────────────────────────────────────┘
                    ↑↓
┌──────────────────────────────────────────────────────┐
│  L1: 数据层                                           │
│  ┌────────────────┬────────────────────┐             │
│  │ 日线数据        │  分钟线数据         │             │
│  │ fund_etf_hist  │  fund_etf_hist_min │             │
│  └────────────────┴────────────────────┘             │
│  统一接口：DataLoader（freq 参数）                    │
│  ETF 池 / 日线·分钟线 / 因子库 / Git 版本                │
└──────────────────────────────────────────────────────┘
```

### 2.2 数据流

```text
配置 FREQ = "daily" 或 "1min"
    ↓
DataLoader(freq) → 统一格式 DataFrame → ETF 池（带上市日期）
    ↓
RD-Agent 多源挖掘 → 因子库
    ↓
正交化 + 聚类 → 精简因子集
    ↓
风险预算加权 → 单 ETF 信号
    ↓
联合优化（正交化 + 风控）→ 最优参数
    ↓
分钟回测 → DSR / PBO / Walk-forward
    ↓
多策略并行 → 组合净值
    ↓
实盘引擎 → 订单 → 券商
    ↓
反馈闭环 → 修正 config
    ↓
LLM 报告 → 决策者
```

## 第三部分：系统架构设计

### 3.1 模块清单

```text
etf_quant/
│
├── 【核心配置】
│   ├── config.py                     # 全局配置
│   ├── live/config_live.py           # 实盘配置
│   └── feedback/generation_config.py # 生成配置
│
├── 【数据层】
│   ├── etf_universe.py               # ETF 池管理
│   ├── data_loader.py                # 数据加载
│   └── live/market_data.py           # 实时行情
│
├── 【研究层】
│   ├── factor_dsl.py                 # 因子 DSL
│   ├── factors.py                    # 因子库
│   ├── llm_factor_agent.py           # LLM 因子生成
│   ├── rdagent_facade.py             # RD-Agent 统一入口
│   ├── official_rdagent.py           # 官方 RD-Agent
│   ├── multi_source_mining.py        # 多源并行
│   ├── factor_genetic.py             # 单目标 GP
│   ├── genetic_multi_objective.py    # 5 目标 GP
│   ├── genetic_extended_objectives.py# 扩展目标
│   ├── llm_genetic_hybrid.py         # LLM+GP 混合
│   ├── llm_mutation_operator.py      # LLM 变异
│   ├── llm_crossover_operator.py     # LLM 交叉
│   ├── adaptive_mutation.py          # 自适应变异
│   ├── dynamic_objective_weights.py  # 动态权重
│   ├── rl_weight_scheduler.py        # RL 权重
│   ├── factor_orthogonal.py          # 因子正交化
│   ├── orthogonal_optimizer.py       # LLM 调正交化
│   ├── factor_clustering.py          # 因子聚类
│   ├── factor_attribution.py         # 因子归因
│   ├── factor_decay_predict.py       # 衰减预测
│   ├── decay_explain.py              # 衰减可解释
│   ├── llm_shap_explainer.py         # LLM SHAP 解释
│   ├── shap_timeline.py              # SHAP 时序
│   ├── risk_budget.py                # 风险预算
│   └── factor_library.py             # 因子库
│
├── 【策略层】
│   ├── strategy.py                   # 单 ETF 策略
│   ├── multi_strategy.py             # 多策略并行
│   ├── strategy_lifecycle.py         # 生命周期
│   └── strategy_pbo.py               # 策略级 PBO
│
├── 【回测层】
│   ├── backtest.py                   # 分钟回测
│   ├── dsr.py                        # DSR
│   ├── pbo.py                        # PBO
│   ├── pbo_timeline.py               # PBO 演化
│   └── walk_forward.py               # Walk-forward
│
├── 【优化层】
│   ├── joint_optimizer.py            # 联合优化
│   ├── auto_remining.py              # 自动重挖
│   ├── trigger_logic.py              # 联动触发
│   ├── llm_research_planner.py       # LLM 研究计划
│   ├── factor_library_git.py         # 因子库 Git
│   └── plot.py                       # 绘图
│
├── 【实盘层】
│   ├── live/broker_interface.py      # 券商接口
│   ├── live/qmt_broker.py            # QMT
│   ├── live/easytrader_broker.py     # easytrader
│   ├── live/paper_broker.py          # 模拟盘
│   ├── live/live_engine.py           # 实盘引擎
│   ├── live/live_risk.py             # 风控前置
│   └── live/live_attribution.py      # 实盘归因
│
├── 【反馈层】
│   ├── feedback/feedback_engine.py   # 反馈引擎
│   ├── feedback/slippage_analyzer.py # 滑点分析
│   ├── feedback/latency_analyzer.py  # 延迟分析
│   ├── feedback/turnover_analyzer.py # 换手分析
│   ├── feedback/config_updater.py    # 配置更新
│   ├── feedback/strategy_feedback.py # 策略反馈
│   ├── feedback/factor_feedback.py   # 因子反馈
│   └── feedback/blind_spot_detector.py # 盲点检测
│
├── 【报告层】
│   ├── report/report_prompts.py    # 报告 Prompt
│   ├── report/report_templates.py  # 报告模板
│   ├── report/llm_report_generator.py # LLM 报告
│   ├── report/monthly_review.py    # 月报
│   ├── report/quarterly_review.py  # 季报/年报
│   ├── report/self_evaluator.py    # 自评
│   ├── report/eval_prompts.py      # 评估 Prompt
│   ├── report/eval_metrics.py      # 评估指标
│   ├── report/prompt_optimizer.py  # Prompt 优化
│   ├── report/multi_llm_voter.py   # 多 LLM 投票
│   ├── report/ab_test.py           # A/B 测试
│   ├── report/multi_llm_generator.py # 多 LLM 生成
│   ├── report/multi_llm_blind_eval.py # 盲评
│   └── report/report_merger.py     # 报告融合
│
├── 【入口脚本】
│   ├── main.py                       # 研究主流程
│   ├── run_live.py                   # 实盘启动
│   ├── run_feedback.py               # 反馈批处理
│   ├── run_monthly.py                # 月报
│   ├── run_multi_gen.py              # 多 LLM 生成
│   ├── app.py                        # 研究看板
│   ├── app_live.py                   # 实盘看板
│   └── app_feedback.py               # 反馈看板
│
└── 【依赖】
    └── requirements.txt
```

### 3.2 关键技术选型

| 层 | 组件 | 选型 | 理由 |
| :--- | :--- | :--- | :--- |
| 数据 | 行情 | akshare + Sina | 免费、易用 |
| 因子 | 生成 | RD-Agent + LLM + GP | 多源互补 |
| 评估 | 防过拟合 | DSR + PBO | 学术级 |
| 回测 | 引擎 | 自研分钟级 | 支持 T+0/T+1 |
| 实盘 | 接口 | QMT 主 + easytrader 备 | 稳 |
| 报告 | LLM | 多模型投票 | 稳健 |
| 看板 | Web | Streamlit | 快速 |
| 存储 | 版本 | Git + CSV + JSON | 可追溯 |