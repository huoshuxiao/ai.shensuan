# 股票线（A 股个股）

与 ETF 轮动线（`etf/v1/`）共用的挖掘/因子/报告内核在 `common/src/`，本目录只放
股票线专属：qlib/A 股数据入口、全市场截面评估、A 股交易规则与费率。

## 现状

股票线目前只做**因子研究**：RD-Agent(Q) + 本地 LLM 产因子 → A 股全市场日线截面
评估。尚无自己的轮动策略、分钟线与实盘链路（那些仍在 ETF 线）。

## 目录

| 路径 | 说明 |
| :--- | :--- |
| `src/config/config.py` | 本线配置：`STOCK_*` 环境变量 + `stock/v1/.env`，A 股费率/T+1/整手 |
| `src/run_rdagent_loop.py` | 拉起 RD-Agent(Q) factor 循环（conda 环境 `rdagent` + `local_qlib` 容器） |
| `src/run_ashare_factor_eval.py` | 把 `factors.json` 的因子放到 A 股全市场上算两套口径 IC |
| `data/results/rdagent_output/` | RD-Agent 工作区：`.env`、提示词覆盖 `_tpl/`、`factors.json`、循环日志 |
| `data/library/` | 本线因子库（`MARKET=ashare` 戳），与 ETF 线的库互不覆盖 |

## 常用命令

```bash
# 1) 跑一轮因子循环（约 1.7-2.5 小时，独占本机 LLM 与内存，勿与 ETF 线并发）
cd stock/v1/src && /usr/bin/python3.10 run_rdagent_loop.py 1

# 只回收上一轮会话产物、不重跑循环
cd stock/v1/data/results/rdagent_output
env PYTHONNOUSERSITE=1 conda run -n rdagent python harvest_only.py

# 2) A 股全市场截面评估（只读，不写因子库）
cd stock/v1/src && /usr/bin/python3.10 run_ashare_factor_eval.py
# 冒烟：STOCK_SAMPLE=400 STOCK_EVAL_START=2024-01-01 python run_ashare_factor_eval.py
```

## 两套 IC 口径

`run_ashare_factor_eval.py` 同时输出，回答的不是同一个问题：

| 列前缀 | 口径 | 定义 |
| :--- | :--- | :--- |
| `ts_` | 主线时序口径 | 逐只标的 `mean_t IC(F_t, r_{t+1})` 再取均值（Spearman），与 ETF 线在库因子同算法，可直接横比 |
| `cs_` | 全市场截面口径 | 逐日截面 `corr_i(F_{t,i}, r_{t+1,i})`；`cs_ic`=Pearson（对齐 qlib/沙箱报表的 IC），`cs_rank_ic`=Spearman（qlib 的 Rank IC） |

已验证的结论（2010-2026，5676 只）：RD-Agent 沙箱报的 `IC=0.029` 是 100 只 debug
标的 + 2018-2019 的**实验级**数字，全市场截面上同批因子只有 -0.005~-0.019 且符号
相反；只有成交量波动族（`STD(Volume,5)` RankIC -0.057、ICIR -0.60）稳定带信号，
方向与低波动/低换手异象一致。
