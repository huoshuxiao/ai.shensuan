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
| `data/qlib/qlib_data/cn_data/` | 本线 qlib 行情 bin（A 股全市场，~850M，已 gitignore）。容器挂载根是 `data/qlib/`，`qlib_data/cn_data` 两级由 rdagent 模板写死 |
| `data/results/rdagent_output/` | RD-Agent 工作区：`.env`、提示词覆盖 `_tpl/`、`factors.json`、循环日志 |
| `data/library/` | 本线因子库（`MARKET=ashare` 戳），与 ETF 线的库互不覆盖 |

## 常用命令

```bash
# 0) 更新行情：qlib bin 换最新版（上游 chenditc/investment_data 每个工作日发）
cd stock/v1/data/qlib
curl -sSL -o /tmp/qlib_bin.tar.gz \
  https://github.com/chenditc/investment_data/releases/download/<tag>/qlib_bin.tar.gz
tar -xzf /tmp/qlib_bin.tar.gz -C qlib_data        # 解出 qlib_data/qlib_bin
mv qlib_data/cn_data qlib_data/cn_data.old        # 先改名再换入，出错可回滚
mv qlib_data/qlib_bin qlib_data/cn_data
rm -rf qlib_data/cn_data.old                      # 确认新 bin 可用后才删
# 循环要读的 daily_pv.h5 随之重算（脚本按日历末日期自动判过期）
cd ../results/rdagent_output
QLIB_PROVIDER_URI=$PWD/../../qlib/qlib_data/cn_data env PYTHONNOUSERSITE=1 \
  conda run -n rdagent python ../../../../../common/rdagent_docker/pregen_source_data.py .

# 1) 跑一轮因子循环（09-22 实测股票线 33 分钟、ETF 线 51 分钟；旧估计 1.7-2.5h
#    是修掉 prompt 尾逗号与 IC 取值 bug 之前的）。两线共用一个本地 LLM，可以并发
#    （实测都跑完），但各自耗时约翻倍，且全市场评估同开会挤内存）
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

已验证的结论（09-22 重跑：2010-01-04~2026-09-22、5677 只、4057 个交易日，
产物 `data/results/ashare_factor_eval.csv`，753s）：RD-Agent 沙箱报的 `IC=0.029`
是 100 只 debug 标的 + 2018-2019 的**实验级**数字，同批 12 个因子放到全市场截面
只剩 `cs_ic` -0.019~+0.002、`cs_rank_ic` -0.057~-0.013，**方向与沙箱相反**；且
12 个因子在 `ts_` / `cs_` 两套口径下**全为负**（价格均线族 `ts_ic` -0.046~-0.054
最强、RankIC 却最弱 -0.013~-0.015），说明这批"追涨/放量"型定义在 A 股是反向指标
而非 alpha。真正稳定带信号的只有成交量波动族，且窗口越短越强：
`STD(Volume,5)` RankIC **-0.0570** / RankICIR **-0.599** > `STD(Volume,10)`
-0.0494/-0.492 > `SMA(Volume,5)` -0.0474/-0.447 > `STD(Volume,20)` -0.0425/-0.409，
日胜率 37-40%（即六成以上交易日为负），方向与低波动/低换手异象一致。
`run_ashare_factor_eval.py` 只读，不写因子库、不触发 git 提交。
