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
| `src/run_ashare_portfolio_eval.py` | **组合层**验证：把截面已确认的因子做成可交易多头篮子与「剔除最差五分位」规则，看扣费后还有没有超额 |
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

# 2) A 股全市场截面评估（只读，不写因子库）。这个脚本要 pandas，必须用 conda
#    环境的解释器；/usr/bin/python3.10 只够跑 run_rdagent_loop.py 那个启动器
cd stock/v1/src && PYTHONNOUSERSITE=1 \
  /home/sunwenkun/miniconda3/envs/rdagent/bin/python run_ashare_factor_eval.py
# 冒烟：STOCK_SAMPLE=400 STOCK_EVAL_START=2024-01-01 \
#       STOCK_EVAL_OUT=/tmp/smoke.csv python run_ashare_factor_eval.py
#  （STOCK_EVAL_OUT / STOCK_PORT_OUT 必带：落点默认是正式结论文件，冒烟会盖掉它）

# 3) 组合层验证（送验清单写死在脚本里的 SIGNALS 表，按 expr ∈ factors.json 过滤）
cd stock/v1/src && PYTHONNOUSERSITE=1 STOCK_PORT_OUT=/tmp/port.csv \
  /home/sunwenkun/miniconda3/envs/rdagent/bin/python run_ashare_portfolio_eval.py
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

09-23 复跑（21 个因子、5677 只、4050 个截面日、697s）新增两条硬结论：

1. **沙箱那个 IC 压根不是因子的 IC**。qlib 报表里的 `IC`/`Rank IC` 取自
   `contrib/report/analysis_model/analysis_model_performance.py` 的 `pred_label`
   入参 —— 是**模型预测 vs label**，而 label = `Ref($close,-2)/Ref($close,-1)-1`
   （见 rdagent `factor_template/conf_combined_factors_sota_model.yaml`）。所以
   沙箱报 0.0288 说的是"新因子与 SOTA 库合成后那个模型"的排序力，与本表逐因子的
   `cs_ic` 不是同一个量，**两者符号相反既不构成矛盾也不构成印证**。逐因子准入只能看本表。
2. **Pearson 与 Spearman 可以反号**，此时只能按排序读数：`5-day MOM of Price`
   `cs_ic` +0.0173 而 `cs_rank_ic` -0.0472 —— 极端暴涨票把线性相关拉正了。同理
   价格水平族（SMA/VWAP/MAX）的 `ts_ic` -0.046~-0.054 远强于 `cs_rank_ic`
   -0.013~-0.016，是同一个"低价股效应"在时序口径里的放大，不是更强的信号。

## 组合层：截面 IC 之后还要过这一关

`run_ashare_portfolio_eval.py` 的可执行口径：信号日 `s` 收盘算 `F_s` → `s` 的下一
交易日以**开盘价**建仓（收盘竞价实际买不到）→ 持有 5 个交易日、开盘价平仓；单边
15bp（佣金万 2.5 + 印花税万 5 按卖出摊半 + 滑点万 10）；三道闸门 = 20 日均成交额
≥2000 万、已有行情 ≥60 日、开盘涨幅 ≥9.5% 视为买不进。基准取**同一可投资域的等权**
（用全市场等权会把大量买不到的冷门票算进基准，超额被系统性压低），另附 SH000300。

结论（09-23，2015-01-01 起，12 条 × 3 档规模；产物 `data/results/ashare_portfolio_eval.csv`
与 `_yearly.csv`，`excl_worst_vs_pool_ann` = 去掉因子值最高五分位相对全池的年化增益）：

- **量能族做多头几乎全塌，换手是主因**。只有 `STD(Volume,20)` 扣费后为正
  （超额 +0.0231 / IR 0.22，200 只时 +0.0371 / 0.42）；`MOM(Volume,5)` 每次调仓
  换掉 99.8% 的仓位、`STD(Volume,5)` 换 70%，多头腿年化 -0.10 与 -0.05。
- **量能族做"剔除爆量"规则成立，且证据从 1 个构造扩到 4 个互相独立的构造**：
  剔除最差五分位年化增益 `STD(Volume,5)` +0.0436 > `STD(Volume,20)` +0.0433 >
  `SMA(Volume,5)` +0.0406 > `SMA(Volume,10)` +0.0389 > `MOM(Volume,20)` +0.0285 >
  `SMA(Vol,5)/SMA(Vol,20)` +0.0235 > `MOM(Volume,5)` +0.0231，全部高于价格族对照的
  +0.015~+0.017。剔除是减法等权、不产生新买入，费率近零，与做多不是一回事。
- **量能动量/比值是倒 U 形，只能单侧用**：`MOM(Volume,5)` 五分位年化
  Q1 +0.124 → Q3 +0.173 → Q5 +0.042。截面负 IC 会诱使人去做多"缩量端"，但极端
  缩量档是流动性枯竭/停牌/退市前缩量，并不好；**只有爆量端差**，所以这三条进剔除
  规则、不进多头。
- **价格水平族不入库为独立因子**：新回收的 `VWAP(Price,5)`/`MAX(Price,5)`/
  `MAX(Price,20)` 与已在库的 `ma(df,5)` 在多头超额（+0.023~+0.031）、换手
  （0.08~0.10）、剔除增益（+0.015~+0.017）上重合到小数点后第二三位 —— 同一个低
  价格暴露的又几种写法。这条超额按**暴露**记账，不算因子发现。
