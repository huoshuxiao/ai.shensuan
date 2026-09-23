# CHANGELOG

代码与判据的变更流水。**文档口径**：`stock/v1/README.md`、`etf/v1/README.md` 等 5 份
说明文档于 2026-09-23 被并行会话从工作区删除，本仓库已决定**不从 HEAD 恢复**，改由本
文件承接「改了什么、为什么改」这条线；操作手册类内容仍以各线 `src/` 脚本顶部的
docstring 为准（那里是判据的权威出处）。

## 2026-09-23（股票线 · 准入链补齐判重环）

### 新增：因子判重预检入口 `stock/v1/src/run_ashare_redundancy_check.py`

主线准入链此前只有两环——`run_ashare_factor_eval.py`（全市场截面 IC）与
`run_ashare_portfolio_eval.py`（组合层扣费复核）。缺的第三环是 RD-Agent 侧的
`deduplicate_new_factors`：新因子与任一 SOTA 因子的逐日截面相关均值 `>= 0.99` 会被
整列丢弃，全丢则抛 `FactorEmptyError`，日志里只留一句 `Skip loop`。于是「因子不好」
（有价值的负结果）和「因子重复」（零信息）在主线看来长得一模一样，而后者已经烧掉了
一整轮 33 分钟的墙钟。此入口把一个候选提名进提示词清单**之前**先把这个判据算出来。

- 判据逐字对齐 rdagent：主列 `pearson_max` 用**原始值 Pearson**、逐日截面后再对日取
  均值，取「与最近的那条在库因子」的值；`spearman_max` 一并输出作对照（两口径的差
  本身就是该因子被少数尾部样本主导程度的读数）。
- 面板起点 `ASHARE_RED_START=2015-01-01`（不同于截面评估的 2010）：2010-2014 未上市
  标的多、逐日截面稀，相关性会虚高，那样算出的「重复」不代表循环里真实发生的事。
- 参数落在 `stock/v1/src/config/config.py`：`ASHARE_RED_BAR/START/END/OUT/DETAIL`，
  输出路径可覆写，冒烟一律带 `STOCK_RED_OUT=/tmp/...`，约定同 `ASHARE_EVAL_OUT`。
- 只读：不写因子库、不改 `factors.json`、不触发 git 提交。
- 自校验（`shell/selftest_candidates.json`）：把在库 `SMA(Volume,20)` 原样重提 →
  `pearson_max = 1.000` → 判「会被判重复」；`SMA(Volume,15)`（同族只换窗口）→ 0.9886
  → 不过线但已是同簇。这条同时证明本入口与 rdagent 的行为一致。

### 全市场实跑（338s，5658 只 / 2830 有效交易日 / 11,238,239 成对样本）

产物 `stock/v1/data/results/ashare_redundancy_check.csv` 与
`ashare_redundancy_detail.csv`（候选 × 在库 全矩阵）。

| 候选 | 最近邻在库因子 | Pearson（判据） | Spearman | 结论 |
| :--- | :--- | :--- | :--- | :--- |
| `20-day MAX of Volume`（对照） | `20-day STD of Volume` | 0.9836 | 0.9899 | 危险区，不提名 |
| `5-day MAX of Volume`（对照） | `5-day SMA of Volume` | 0.9815 | 0.9862 | 危险区，不提名 |
| `5-day MIN of Volume` | `10-day SMA of Volume` | 0.9689 | 0.9687 | 危险区，已从清单撤下 |
| `20-day MIN of Volume` | `20-day SMA of Volume` | 0.9432 | 0.9355 | 可提名（唯一剩下的非比值） |
| `5-day SMA of Price over 20-day SMA of Price` | `20-day MOM of Price` | 0.8211 | 0.8121 | 可提名 |
| `5-day STD of Volume over 20-day STD of Volume` | 库内 SMA 比值 | 0.6020 | 0.6998 | 可提名 |
| `20-day STD of Price over 20-day SMA of Price` | `20-day MOM of Price` | 0.3741 | 0.5304 | 最独立的一条 |

### 修正：`CANDIDATE LIST` 与两处口头判据（两线 `prompts.yaml` 同步）

提示词覆盖文件 `{stock,etf}/v1/data/results/rdagent_output/scenarios/qlib/prompts.yaml`
的 `factor_hypothesis_specification` 三次改动，最终形态以上表为准：

1. 「补两条非比值候选」最初补进了成交量尾部统计 4 条（`MAX/MIN of Volume`），并写下
   「它与成交量的均值/标准差是不同信息」——**这句未经实测，是错的**。
2. 用 `shell/tail_redundancy.py` 的池化秩相关改口成「`MAX(Volume,20)` = 0.991 会被判
   重复、白烧一轮」——**也不成立**：rdagent 用原始值 Pearson、逐日算再取均值，权威
   口径是 0.9836，不过 0.99 线。
3. 定稿：`MAX/MIN of Volume` 的四个数字连同口径写进 `ALREADY TESTED`（说明它们是量能
   水平簇换了个算子，不是新家族），`CANDIDATE LIST` 收窄为 `20-day MIN of Volume` +
   3 条比值，并在每条后标注实测冗余度。

配套改了「至多一条比值」这条老约束的落地方式：清单里只剩一条非比值，所以另一格必须
给比值，同时明确禁止为了凑非比值去现编名字——现编的单字段名几乎总是某个已测因子换
了个算子写法。

### 来路说明

`shell/tail_redundancy.py` 保留但**数字不可再引用**（池化近似与新入口的逐日均值口径在
0.99 阈值附近给出相反结论），它只作为第 2 步那次改错的存档。判重一律以
`run_ashare_redundancy_check.py` 的输出为准。一次性脚本按约定归 `shell/`，不再写 `/tmp`。
