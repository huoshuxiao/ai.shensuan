# -*- coding: utf-8 -*-
"""挖掘那把 IC 尺子的**作用域**：它看得见择时，看不见静态截面排序；
而下单那一层是按截面名次发钱的。

钉住的四件事（09-29 已在真数据上实测；此处只钉「尺子本身的性质」，
所以全部用合成面板，不依赖任何外部数据源，也不动生产产物）：

1. 机制：`factor_dsl.safe_spearman` 对常量列短路返回 0.0（:194-196）。
2. 后果：挖掘口径 = 逐只标的各算一个**时序** Spearman、再对标的取均值
   （`common/src/core/llm_factor_agent.py:97-114`）。静态截面因子在每只标的上
   都是一条直线 ⇒ 逐只全是 0 ⇒ 均值恰为 0.0。**正着排 0、反着排也 0**，
   ⇒ 这是「一整类因子」的性质，不是"这一枚太弱"。
3. 正对照：同一枚静态截面因子，在准入层那把尺子（逐日截面 RankIC，
   `etf_admission.daily_cs_ic`，`MIN_CS=30`）下读出接近 +1 的强信号，
   且反号时精确反号 ⇒ 两把尺子对同一份数据给出相反判决。
   没有这一条，第 2 条只是"两把尺子都说没信号"，什么也没证。
4. 反对照（本文件把自己此前一句错话钉住了）：会随时间动、且**标的之间持续一致**
   的因子（AR(1) ρ=0.9 的持续性）在挖掘口径远高于 0.02，在逐日截面 RankIC 下
   实测也高达 0.88 ⇒ 两把尺子的分野**不是「时序 vs 截面」**，而是**动 vs 静**：
   只有「每只标的各自一条直线、彼此只剩高低」这一整类静态因子，挖掘口径读作 0。

为什么不能就地换尺子（第 5 节）：准入口径有 `MIN_CS=30` 的地板，
walk-forward 折 1 那种 4 只标的的训练段上，逐日截面 IC **一天都不剩** ⇒ 全 NaN。
窄面板上「换成截面尺子」不是选项，这条测试就是防止有人这么改。

第 6 节钉三个数字各是各的闸（此前把它们混成"IC 门槛 0.01"过）：
  0.005 = `main.select_factors` 默认 min_ic（三处调用全用默认值）+ 逐源地板
          `MULTI_SOURCE["min_ic_per_source"]`（`multi_source_mining.py:190`）——
          链路日志「候选 N，过 IC 门槛 M」判的是它；official/simple/genetic **只**吃这道；
  0.02  = `IC_THRESHOLD`（`common/src/config_base.py:112` 按频率给值，daily 档 0.02），
          LLM agent 用它筛自己的 `knowledge_base`（`llm_factor_agent.py:143`）⇒
          **llm 那一源实际门槛是 0.02，比别的源严 4 倍**，不是装饰；
  0.01  = `FACTOR_DECAY["ic_min_threshold"]`（config.py:180）—— 因子**衰减/生命周期**
          用的（`strategy_lifecycle.py:85,141`），与挖掘准入无关。
          ⚠️ 别拿 `DECAY_PREDICT` 当它：那是另一个字典（main.py:542 只读它的
          `enabled`），里面没有 `ic_min_threshold` 这个键。

所有 oracle 都是**用标签反造的合成因子**，只用来量尺子的灵敏度边界，
不构成任何信号证据，也不进任何判据。
"""

import inspect

import numpy as np
import pandas as pd
import pytest

from config import DECAY_PREDICT, FREQ, IC_THRESHOLD, MULTI_SOURCE
from config import FACTOR_DECAY
from etf_admission import MIN_CS, daily_cs_ic
from factor_dsl import compute_ic, safe_spearman

N_CODES = 40          # 必须 > MIN_CS，否则准入口径整日丢弃、第 3/4 节无从对照
N_DAYS = 400
IDX = pd.bdate_range("2015-01-05", periods=N_DAYS)
CODES = [f"5103{i:02d}" for i in range(N_CODES)]


# ───────────────────────── 合成面板 ─────────────────────────
def _cross_sectional_panel(seed=11):
    """截面有信息、时序无信息：每只一个**整段不变**的期望次日收益 mu_i
    （按代码序单调），实现值 = mu_i + 独立噪声，噪声压得足够小
    ⇒ 逐日截面名次几乎天天复现 mu 的名次 ⇒「按 mu 排序」是一枚近乎完美的
    **静态截面**因子（它对时间一条直线，所以挖掘口径必然读 0）。"""
    rng = np.random.default_rng(seed)
    mu = np.linspace(-0.004, 0.004, N_CODES)
    fwd = pd.DataFrame(mu[None, :] + rng.normal(0, 0.0015, (N_DAYS, N_CODES)),
                       index=IDX, columns=CODES)
    fac = pd.DataFrame(np.tile(mu, (N_DAYS, 1)), index=IDX, columns=CODES)
    return fwd, fac


def _timing_panel(seed=29):
    """时序有信息、截面无信息：每只标的自己的标签是一个 AR(1)（ρ=0.9），
    标的之间互相独立、且 mu 恒为 0 ⇒ 同一天的横剖面上没有任何名次信息。
    因子取该标的自己的上一期标签 ⇒ 逐只时序 IC 很高，逐日截面 IC 贴 0。"""
    rng = np.random.default_rng(seed)
    rho, sd = 0.9, 0.01
    eps = rng.normal(0, sd * np.sqrt(1 - rho ** 2), (N_DAYS, N_CODES))
    x = np.zeros((N_DAYS + 1, N_CODES))
    for t in range(1, N_DAYS + 1):
        x[t] = rho * x[t - 1] + eps[t - 1]
    fwd = pd.DataFrame(x[1:], index=IDX, columns=CODES)          # 标签 = x_t
    fac = pd.DataFrame(x[:-1], index=IDX, columns=CODES)         # 因子 = x_{t-1}
    return fwd, fac


def _mining_ic(fac, fwd):
    """挖掘口径：逐只时序 IC ⇒ 对标的取算术均值（判据看的就是这个数）。
    逐只那一步用的是**生产的** `compute_ic`，这里只补上那句 np.mean。"""
    ics = [compute_ic(fac[c], fwd[c]) for c in fac.columns]
    return float(np.mean(ics)), ics


# ───────────────────────── 1. 机制 ─────────────────────────
def test_safe_spearman_shorts_constant_to_zero():
    """常量列 ⇒ 0.0（失明链条的第一环，改这行等于改判据的作用域）"""
    y = pd.Series(np.arange(100.0) % 7)
    for const in (3.0, -1.0, 0.0):
        x = pd.Series(np.full(100, const))
        assert safe_spearman(x, y) == 0.0
        assert safe_spearman(y, x) == 0.0            # 两侧任一为常量都短路
    assert safe_spearman(y, y) == pytest.approx(1.0)  # 反证：非常量时尺子是活的


# ───────────────────────── 2+3. 失明与正对照 ─────────────────────────
def test_static_cross_sectional_factor_scores_exactly_zero_in_mining_ruler():
    fwd, fac = _cross_sectional_panel()
    m, ics = _mining_ic(fac, fwd)
    assert set(ics) == {0.0}            # 逐只**全部**为 0，不是"平均完事"
    assert m == 0.0
    m_neg, ics_neg = _mining_ic(-fac, fwd)            # 反着排也还是 0
    assert set(ics_neg) == {0.0} and m_neg == 0.0


def test_same_factor_reads_near_perfect_under_admission_cross_section_ruler():
    """正对照：静态截面因子在准入口径下必须**大而非 0**。"""
    fwd, fac = _cross_sectional_panel()
    _, ric, n_valid = daily_cs_ic(fac, fwd)
    assert (n_valid >= MIN_CS).all()                  # 宽面板上这把尺子有定义
    assert ric.mean() > 0.5                           # 不是恒真的宽松阈值
    _, ric_neg, _ = daily_cs_ic(-fac, fwd)
    assert float((ric + ric_neg).abs().max()) < 1e-9  # 秩相关反对称 ⇒ 精确反号
    assert _mining_ic(fac, fwd)[0] == 0.0             # 同一份数据，另一把尺子是 0


def test_mining_gate_rejects_a_factor_the_admission_ruler_calls_strong():
    """端到端：把上面那枚近乎完美的截面因子按挖掘口径的读数交给**生产的**
    `select_factors` ⇒ 它被判出局。这条链路现在不可能留下静态截面因子。"""
    from main import select_factors
    fwd, fac = _cross_sectional_panel()
    m, _ = _mining_ic(fac, fwd)
    _, ric, _ = daily_cs_ic(fac, fwd)
    assert select_factors([{"name": "static_cs_oracle", "mean_ic": m}]) == []
    assert ric.mean() > 0.5                           # 而它其实"很准"
    # 反证：闸本身没坏 —— 同一个因子若带准入口径的读数送进来，闸放行
    assert len(select_factors([{"name": "x", "mean_ic": float(ric.mean())}])) == 1


# ───────────────────────── 4. 反对照：时序腿 ─────────────────────────
def test_pure_timing_oracle_passes_mining_gate_and_leaks_into_cross_section():
    """挖掘口径的正对照：会随时间动的因子它打得出分（证明上一节的 0 不是"尺子坏了"）。

    ⚠️ 顺手把一句错话钉在这里：我一度以为"时序型"因子在逐日截面 RankIC 下会贴 0。
    实测 0.8786 —— 只要每只标的自己持续（AR(1) ρ=0.9），昨天名次高的那只今天
    名次还是高 ⇒ 持续性和截面名次**不是对立的**，截面尺子同样看得见。
    ⇒ 两把尺子的差别不是「时序 vs 截面」，而是**动 vs 静**：
      只有"每只标的各自一条直线、彼此只有高低"这一整类因子，挖掘口径读作 0。
    所以"把判据换成截面 IC"救不回这一类之外的东西，还会在窄折上整段失效（第 5 节）。"""
    fwd, fac = _timing_panel()
    m, ics = _mining_ic(fac, fwd)
    assert abs(m) > 0.5 and all(v > 0.3 for v in ics)     # 逐只时序 IC 很高
    assert abs(m) >= MULTI_SOURCE["min_ic_per_source"]
    assert abs(m) >= IC_THRESHOLD                         # 两道闸都过
    _, ric, _ = daily_cs_ic(fac, fwd)
    assert ric.mean() > 0.5                               # 已实测：也漏进截面


# ───────────────────────── 5. 换尺子不是免费午餐 ─────────────────────────
def test_admission_ruler_is_undefined_on_narrow_fold_panel():
    """折 1 那种 4 只标的的训练段：MIN_CS=30 把每一天都丢掉 ⇒ 全 NaN。
    「把判据换成截面 IC」在窄面板上不是可选项，这条挡住这个改法。"""
    fwd, fac = _cross_sectional_panel()
    narrow = CODES[:4]
    _, ric, n_valid = daily_cs_ic(fac[narrow], fwd[narrow])
    assert (n_valid < MIN_CS).all()
    assert ric.isna().all()
    # 反证：同一张窄面板、只把地板从 30 降到 4 ⇒ 立刻处处有定义。
    # 少了这一格，上面的全 NaN 可能来自任何别的原因为恒真判据留了后门。
    _, ric_lo, n_lo = daily_cs_ic(fac[narrow], fwd[narrow], min_cs=4)
    assert (n_lo == 4).all() and ric_lo.notna().all()
    # 地板拿掉之后尺子的样子：4 个点的名次只能落在 ±0.2/±0.6/±1.0 这几档上，
    # 均值从宽面板的 >0.5 塌到 0.20 ⇒ MIN_CS=30 不是摆设，这正是它存在的理由
    # （etf_admission.py:94 注释：30 只以下相关系数标准误已 >0.18）
    _, ric_wide, _ = daily_cs_ic(fac, fwd)
    assert ric_wide.mean() > 0.5
    assert ric_lo.mean() < 0.5 * ric_wide.mean()
    # 同一张窄面板上挖掘口径照样给出非 0 判决 ⇒ 折内确实是在这把尺子上选因子
    fwd2, fac2 = _timing_panel()
    assert _mining_ic(fac2[narrow], fwd2[narrow])[0] > 0.5


# ───────────────────────── 6. 三个数字三道闸 ─────────────────────────
def test_the_three_ic_numbers_are_three_different_knobs():
    """混过一次（把 config 的 0.01 当成挖掘门槛），这里钉死各自的身份。"""
    from main import select_factors
    default_min_ic = inspect.signature(select_factors).parameters["min_ic"].default
    assert default_min_ic == 0.005                   # 链路真用的那道
    assert MULTI_SOURCE["min_ic_per_source"] == 0.005
    assert IC_THRESHOLD == (0.02 if FREQ == "daily" else 0.015)
    assert FACTOR_DECAY["ic_min_threshold"] == 0.01
    # 那个 0.01 只在 FACTOR_DECAY 里；同名的 DECAY_PREDICT 没这个键 ⇒ 拿错字典
    # 当场 KeyError，不会被当成"门槛本来就是 0.01"
    assert "ic_min_threshold" not in DECAY_PREDICT
    assert len({default_min_ic, IC_THRESHOLD,
                FACTOR_DECAY["ic_min_threshold"]}) == 3


def test_the_005_gate_admits_what_the_02_flag_rejects():
    """两个数不只是不相等，判决真的相反：mean_ic=0.006 进得了池、进不了
    agent 自己那句"过 IC 门槛"的语义。

    ⚠️ 那句 0.02 **不是装饰**：`llm_factor_agent.py:143` 用 `if r["pass"]` 决定
    因子能否进 `knowledge_base`，而该函数返回的就是 `knowledge_base` ⇒
    LLM 那一源的因子在离开 agent 之前就被 0.02 拦掉，根本到不了 0.005 那道闸。
    09-29 链路日志实证：折 2 模板 `vol_20 IC=-0.0102 ❌` ⇒「✅ llm 产出 2 个因子」，
    这枚 0.0102 已过 0.005 却没到 0.02，就这样被丢了。
    ⇒ **每源实际生效的门槛不一样**：llm 0.02，official/simple/genetic 只有 0.005
    （折 1 缓存里 `volume_ratio_20` 存着 mean_ic=+0.0059、并被计入「过 IC 门槛 8」，
    就是 simple 那一档只吃 0.005 的实证）。改门槛要按源分别改，别以为拨一个数。"""
    from main import select_factors
    f = [{"name": "f", "mean_ic": 0.006}]
    assert len(select_factors(f)) == 1
    assert not (abs(0.006) >= IC_THRESHOLD)
