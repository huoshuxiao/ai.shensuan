# 数据 + RD-Agent(Q) 整合 · 4 周冲刺计划

> 周期：2026-09-21 ~ 2026-10-18 ｜ 方法论：4 周 Sprint ｜ 约束：本机内存 16G，不支持大规模全市场回测，迭代遵循「最小集 → 逐步扩展」，单轮循环 ≤10 次。
> 关联文档：《RD-Agent(Q) 场内 ETF 量化交易辅助系统.md》《用户使用手册.md》

---

## 0. 立项时点环境快照（2026-09-20 实测）

| 组件 | 状态 | 说明 |
|---|---|---|
| conda | ✅ 26.7.1 | `~/miniconda3/condabin/conda`，专用环境 `rdagent`（Python 3.10.21） |
| rdagent 包 | ⚠️ 0.8.0 | 经 `pip --user` 落在 `~/.local/lib/python3.10/site-packages`，与系统 python3.10 **共享 user-site**（冲突根源）；需求文档目标版本为 **v1.6.0**，存在版本落差 |
| pyqlib | ✅ 0.9.7 | 已装入 `rdagent` 环境 site-packages（今日完成） |
| qlib 行情数据 | ✅ | **09-22 落位到各线自有目录**：股票线 `stock/v1/data/qlib/qlib_data/cn_data`（社区包 `chenditc/investment_data`，末交易日 2026-09-22），ETF 线 `etf/v1/data/qlib/qlib_data/cn_data`（本线缓存 dump 自建）。容器挂载根 = `<线>/v1/data/qlib`，`qlib_data/cn_data` 两级尾巴是 rdagent 模板写死的，只能整体保留；旧的 `~/.qlib/qlib_data/qlib_bin` 已停用（未删） |
| docker | ⚠️ | 二进制与守护进程就绪（`/usr/bin/docker`，dockerd active），但当前用户不在 `docker` 组 → socket permission denied |
| LLM 端点 | ❌ | 无 API key、未装 ollama；RD-Agent(Q) 假设生成环节无驱动源 |

> **待用户执行的解锁动作**（需 sudo 交互，Agent 不代跑）：
> 1. `sudo usermod -aG docker sunwenkun` 后**重新登录**（或 `newgrp docker`）；
> 2. LLM 决策：安装 ollama 起本地 OpenAI 兼容端点，或提供合规 API key 经环境变量注入。

---

## 1. 目标目录结构（项目根数据层）

现状：数据模块、配置、缓存全部嵌在 `etf/v1/` 内，跨产品复用不可能。目标：

```
ai.shensuan.git/                    # 项目根
├─ data/                            # ← 新增：全局数据层
│  ├─ src/                          # 数据获取与清洗代码
│  │  ├─ data_loader.py             #   （自 etf/v1/src/data/ 迁移）
│  │  ├─ etf_universe.py            #   指数分组 + 最早上市代表选池
│  │  ├─ market_data.py             #   实盘行情订阅（若可解耦则保留原位引用）
│  │  └─ sources/                   #   em/sina/tencent 多源适配
│  ├─ config/
│  │  └─ data_config.py             # CACHE_DIR/DATA_ROOT/BACKTEST_START 等数据类配置拆出
│  ├─ cache/                        # 日线 CSV、etf_universe_cache.csv、etf_list_dates.json
│  └─ qlib/qlib_data/cn_data/        #   qlib 行情 bin（09-22 实为**各线一份实体**，
│                                    #   不再是符号链接：容器只认 /root/.qlib/qlib_data/cn_data）
├─ etf/v1/                          # 研究/实盘/反馈管线，import data 层
└─ docs/                            # 计划与规范类文档
```

迁移原则：`git mv` 保留历史；`_bootstrap.py` 挂载路径改为追加 `data/src`；`config.py` 中数据键转发自 `data_config.py`（单一事实源，避免双份常量）。

---

## 2. 四周计划总览

| 周 | 日期 | 主题 | 出口条件（DoD） |
|---|---|---|---|
| W1 | 09-21 ~ 09-27 | 环境定版 + 数据层骨架 | RD-Agent(Q) 前置体检 6 项全 ✅（或明确阻塞责任人）；`data/` 目录骨架落库 |
| W2 | 09-28 ~ 10-04* | 数据迁移 + 评估框架开发 | 旧路径全部改指 `data/` 且 main.py 回归通过；评估框架对现有 21 因子出报告 |
| W3 | 10-05 ~ 10-11* | RD-Agent(Q) ↔ 新数据接口打通 | 最小集循环（≤5 轮）产出 ≥3 个因子，评估框架给出 5/10/20 日 IC 全套指标 |
| W4 | 10-12 ~ 10-18 | 内存治理 + 固化迭代流程 | 16G 下整链路无 OOM；周迭代 SOP 文档化，可重复执行 |

\* 国庆周（10-01~10-05）为低强度周，任务已按可中断设计（W2 迁移与 W3 打通互不阻塞）。

---

## 3. 任务分解 · 交付物 · 验收标准

### 工作包 1.1：数据模块迁移 → `data/`

**任务分解**
1. `data/` 骨架 + `data_config.py`（0.5d）
2. `git mv` etf/v1/src/data 模块与缓存目录（0.5d）
3. `_bootstrap.py`/import 重写、config 转发（1d）
4. 回归：日线管线 + 实盘信号 + 反馈链三路冒烟（1d）
5. 文档同步：README、使用手册路径章节（0.5d）

**交付物**：`data/` 目录、迁移 commit 链、回归日志（`log/migration_regression_*.log`）

> **09-22 状态变更**：本工作包的落地形态与实际方案不同——工程已按
> 「物理分树 + 共享内核」重排为 `etf/v1/src`（ETF 轮动线）、`stock/v1/src`
> （A 股个股研究线）、`common/src`（两线共用的挖掘/因子/报告内核 +
> `config_base.build()` 底座）。RD-Agent(Q) 官方循环与其 `rdagent_output`
> 工作区随 A 股数据归入股票线，沙箱镜像构建目录移到 `common/rdagent_docker`；
> ETF 线在自有 `daily_pv.h5` 就绪前默认停用 official 源。下文的 `src/` 相对
> 路径按此理解。
**验收标准**
- `grep -rn "etf/v1/data" src/` 无残留硬编码（符号链接除外）；
- main.py 9 阶段跑通、绩效表与迁移前一致（总收益/夏普/交易数误差 = 0）；
- run_live.py 冒烟仍取到 20 只池缓存并出信号。

### 工作包 1.2：RD-Agent(Q) 整合接入

**任务分解**
1. 版本决策：0.8.0（现状可用）vs 升级 1.6.0（需求口径）。W1 内完成升级评估：依赖 diff、py 版本要求、破坏性变更清单，产出结论 commit（1d）
2. 冲突治理：rdagent 从 user-site 迁入 conda 环境独立安装（`PYTHONNOUSERSITE=1` 验证纯净导入），`~/.local` 卸载，管线进程与环境彻底隔离（0.5d）
3. 执行通道改造（已开工）：`official_rdagent.py` 前置体检改为「conda 定位 → 环境内 `import qlib/rdagent` 实测 → docker info → cn_data 存在 → LLM 端点」6 项；factor 循环经 `conda run -n rdagent python rdagent_driver.py` 子进程执行，输出回收入 `data/results/rdagent_output/`（2d）
4. LLM 配置：RD-Agent 自身 `.env`（`CHAT_MODEL`/`EMBEDDING_MODEL`/`OPENAI_BASE_URL`），与管线共用 ETF_LLM_* 约定（0.5d）
5. 首轮最小集循环：1 个场景、≤5 轮、单标的组（沪深300ETF 代表池）（2d）

**交付物**：`core/rdagent_driver.py`、体检 6 项日志、`.env` 模板、首轮 factor 循环产物
**验收标准**
- 体检日志 6 项全 ✅（docker/LLM 允许挂人跟踪，但需在 W1 结束前为 ✅）；
- 官方循环产出 ≥1 个因子表达式，可被 `factor_dsl.safe_eval` 解析；
- 循环期间 `free -h` 峰值可用内存 ≥4G（不触发 swap 风暴）。

### 工作包 1.3：因子评估框架（作用于 RD-Agent(Q) 产出之后）

**任务分解**
1. 指标核心：横截面 **rank IC**（Spearman）@ 5/10/20 日前向收益、ICIR、t 值、多空分层收益（2d）
2. 单调性：5 分组年化收益 + Spearman 组序相关（组1<组2<…<组5 判定）（1d）
3. 换手率：Top 组合日/周换手、费前费后差异（成本敏感性）（1d）
4. 报告器：`report/factor_eval_<name>.md` + CSV 汇总，接入 main.py 第 9 阶段与独立入口 `run_factor_eval.py`（1d）

**交付物**：`core/factor_eval.py`、`run_factor_eval.py`、对现有 21 活跃因子的基线报告
**验收标准**
- 对已知因子（如 `reversal_5`）指标与既有 signals 统计交叉验证一致（±1e-6）；
- 5/10/20 日三窗口 × {rank IC, ICIR, 单调性, 换手} 矩阵一次跑出；
- RD-Agent(Q) 产物自动进入该框架评估（无需手工搬运）。

### 上线门槛（三包合并验收）

| # | 标准 | 度量方法 |
|---|---|---|
| A1 | 新因子入选线 | 20 日 rank IC 均值 \|IC\|≥0.02 且 ICIR≥0.3，且分层单调性 Spearman≥0.8 |
| A2 | 换手可控 | 周换手 ≤ 现行阈值（config 换手预算），费后不为负 |
| A3 | 回归稳定 | 连续 2 次日线全管线运行零 Traceback、日志含 🎉 |
| A4 | 迭代可重复 | 按周 SOP 冷启动一遍：迁移后数据 → RD-Agent(Q) 最小集 → 评估报告，全程 ≤4h |

---

## 4. 风险登记及处理方案

| ID | 风险 | 概率 | 影响 | 缓解（预防） | 应急（发生处置） | 责任 |
|---|---|---|---|---|---|---|
| R1 | **双 Python 环境冲突**：rdagent 装 user-site，系统/conda 两份 3.10 互相污染（今日已确认） | 高 | 高 | W1 迁入 conda 环境 + `PYTHONNOUSERSITE=1` 自检；管线只经子进程调用 | 体检失败即降级本地管线（现机制），不阻塞主流程 | Agent |
| R2 | **16G 内存不足**：qlib 全量 cn_data + docker 容器 + LLM 并发 | 高 | 高 | 最小集起步：单场景、≤5 轮、Top-N 标的白名单；子进程 timeout 硬顶（`RDAGENT_TIMEOUT_SEC`） | 监控 `docker stats`/RSS，超限即 terminate + 记录；标的池缩至 20 只代表 | Agent |
| R3 | **版本落差**：环境 0.8.0 vs 需求 v1.6.0 | 中 | 中 | W1 升级评估给出结论；接口层（driver）抽象隔离版本细节 | 留 0.8.0 跑通链路，升级列为 W3 后可选项 | 用户决策 |
| R4 | **docker 组权限**：需 sudo + 重新登录 | 中 | 高 | 计划首日完成 usermod（用户）；文档写明验证命令 `docker info` | 无法解决时评估 rootless/podman 替代 | 用户 |
| R5 | **LLM 端点缺失**：RD-Agent(Q) 假设环节无驱动 | 中 | 高 | 环境变量注入 key（安全立场：不从网络抓取共享 key）或本地 ollama；两案 W1 拍板 | 无 LLM 期间官方循环挂起，本地模板管线保底产出 | 用户 |
| R6 | **qlib 数据与 ETF 错位**：cn_data 以股票为主，场内 ETF 日线可能缺失 | 中 | 中 | 评估框架同时支持 qlib 回测与自有 cache（akshare）双轨对照 | 以自有日线为准出指标，qlib 侧仅作 RD-Agent 内部评估 | Agent |
| R7 | 迁移引入隐性回归（路径、缓存失效、git 历史断） | 低 | 中 | `git mv` + 三路冒烟 + 绩效数值零误差验收 | 按 commit 粒度回滚，缓存目录可再生 | Agent |
| R8 | 因子库自动提交与迁移 commit 混杂 | 低 | 低 | 迁移周内冻结 `[factor-lib]` 提交（或 rebase 分离） | 人工分拣，保留双 tag | Agent |
| R9 | akshare 限速/断连拖垮评估批算 | 中 | 低 | 全走本地 cache（已有冷却机制） | 降采样窗口，跳过失败标的并留痕 | Agent |
| R10 | **沙箱镜像每次重建**：0.8.0 `prepare()` 只要 `build_from_dockerfile=True` 且目录存在就无条件重跑 docker build（官方 Dockerfile 走 CUDA 基础镜像 + 直连 pypi），单轮白烧 1.5-2h 且产出 ~9GB 镜像；docker hub 直连不通、`pytorch/pytorch` 不在 daocloud/1ms 代理白名单 | 高 | 中 | ✅ 已解（09-21）：不动 site-packages，改用官方配置项 `QLIB_DOCKER_DOCKERFILE_FOLDER_PATH` 指向 `data/results/rdagent_docker`（`python:3.10-slim` + tsinghua pypi + qlib 锁定 commit tarball；factor 回测实为 `LGBModel` 纯 CPU，torch 用不到），命中层缓存后重建约 3 分钟 | 需要彻底免重建时置 `QLIB_DOCKER_BUILD_FROM_DOCKERFILE=false`（体检已能识别镜像是否存在） | Agent |
| R11 | **ollama 被 GPU 拖崩**：GTX 965M 经 Vulkan(llvmpipe) 被识别为可用 GPU，offload 6/29 层后 llama-server 段错误，表现为 LLM 调用 500/EOF | 中 | 高 | systemd drop-in 置 `OLLAMA_VULKAN=0`（`OLLAMA_NUM_GPUS`/`CUDA_VISIBLE_DEVICES` 挡不住 Vulkan 路径），已固化入项目记忆 | 若复发：先 `journalctl -u ollama` 看 segfault，再确认 drop-in 生效 | 用户 |
| R12 | **mlflow 3.x 封掉 qlib 的 file-store 后端**：容器内 `qrun` 在 `R.start()` 即抛 `MlflowException: filesystem tracking backend ... maintenance mode`，factor 循环 running 步骤整体失败（09-21 实测） | 高 | 高 | ✅ 已解：沙箱镜像 `ENV MLFLOW_ALLOW_FILE_STORE=true`（宿主变量虽透传进容器，但镜像内固化最可靠），`qrun conf_baseline.yaml` 全市场复跑 exit 0 | 若日后 qlib 迁移 sqlite 后端可移除该 ENV | Agent |
| R13 | **cvxpy 与 qlib 的 scipy 锁定互斥**：镜像不钉 cvxpy 时装到 ≥1.4，其 canonicalizer 引用 `scipy.sparse.eye_array`（scipy ≥1.14 才有），而 qlib 钉 `scipy==1.11.4`，回测策略 import 即 `ImportError`（09-21 实测） | 高 | 中 | ✅ 已解：沙箱镜像钉 `"cvxpy<1.4"`（实装 1.3.4） | 若必须用新 cvxpy，则需同步放宽 scipy 钉法并重验 qlib | Agent |
| R14 | **生成代码列名口径 / running 出 IC**：列名维度（`df['close']` vs `$close`）已解，但 running 步骤要稳定产出官方 IC，还需 LLM 可靠满足 rdagent 因子 I/O 契约 | 中 | 中 | ✅ 列名维度已解（09-21）：`pregen_source_data.py` 为两份 `daily_pv.h5` 的每个 `$col` 追加去 `$` 别名列（幂等迁移），`KeyError:'close'` 整轮 0 命中、真实 `factor.py` 对 405M 全量 h5 `execute` rc=0。⚠️ running 稳定出 IC 仍未达成：换 `qwen2.5-coder:7b` 后 `pd.np` 类语法 bug 归零，但暴露新错误（把 `(datetime,instrument)` MultiIndex 当普通列、退化元因子假设 `SMA(factor)`），回收反降为 0。收口方式＝回收因子定义（name+LaTeX/DSL）+ 管线侧自算 IC（工作包③），产出不受影响 | 官方 running 出指标需 API 级模型（用户暂无 key）或 14B+（16G 装不下）；7B 同档换模型只把 bug 换位复发 | 待用户决策 |

---

## 5. 每周迭代节奏（16G 下的最小集策略）

- **周一** 计划对齐（30min）：从风险登记出发定本周退出条件；
- **每日** 短运行验证：冒烟不超过 15min 时间盒，监视器**只观察不杀进程**（被动 `kill -0` 循环）；
- **周三** 内存审计：记录 RD-Agent 子进程 RSS 峰值 / docker 容器数曲线，超预算即收窄循环规模；
- **周五** 演示 + 回顾：跑一遍「迁移数据 → RD-Agent(Q) 最小集 → 评估报告」链路，产物归档 `report/`；
- **循环规模阶梯**：W1 体检通过（0 轮）→ W3 ≤5 轮 → W4 ≤10 轮 → 冲刺后另行评审再放大；严禁一步到全市场。

## 6. 里程碑

| 日期 | 里程碑 |
|---|---|
| 09-27 | M1 环境定版：体检 6 项绿 + 数据层骨架入库 |
| 10-04 | M2 迁移完成：管线指向 `data/` 回归零误差；评估框架对基线因子出报告 |
| 10-11 | M3 闭环打通：RD-Agent(Q) 最小集 → 评估框架全指标，一键链接 |
| 10-18 | M4 冲刺出口：上线门槛 A1-A4 全过，周迭代 SOP 冻结 |
