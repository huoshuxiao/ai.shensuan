# -*- coding: utf-8 -*-
"""钉住 `recount_foreign_ic` 这把工具的语义，外加 `main.py:713` 那行罩子的**接线**（10-05 用户裁「甲」落地，10-06 用户裁「乙」＝罩子保留、因果全部更正）。

⚠️ 标题里那句「official 腿」是初稿写的，**它是错的**：`main.py:713` 调的 `mine_factors`
是本文件 `:37` 的**注册表**函数（`inspect.getsourcefile(main.mine_factors)` 现证 = `main.py:37`），
不是 `rdagent_facade.mine_factors`。两条同名字链的真相（10-06 逐条现读）：

1. `main.py:37` 注册表腿：逐标的自己算 IC（`:47 compute_ic`），`:60` 就已经过一遍地板乙，
   产物逐标的 IC 天然有离散 ⇒ 永远不满足 `recount_foreign_ic:118` 那颗「逐标的 IC 全等」探针
   ⇒ **`:713` 这一罩在任何 config 下都是空跑**（连 `enabled=False` 也轮不到它吃戳记）。
2. 真会复制容器戳记的是 `rdagent_facade._official_to_impl`（`:20`），它全仓只有一个调用点
   （`rdagent_facade.py:61`＝`mine_factors_multi_source` 在 `MULTI_SOURCE["enabled"]` 为 False 时的兜底转发），
   而这条链在 `main()` 侧的两个消费点 `:178`、`:253` **本批之前就各自罩住了 recount**
   ⇒ 兜底路本来也有防护，`:713` 补的不是缺口。
3. `enabled` 现读 True ⇒ official 走 `multi_source_mine`⇒`_run_official:22`⇒`_attach_impl`
   （`:82` 逐标的重算、`:89` 覆盖）⇒ 地板甲（`multi_source_mining.py:203-205`）与
   地板乙（`main.py:90`）吃的是**同一把重算尺**，容器戳记进不了这两道闸。

真场证据（10-05 19:0x 起、10-06 00:19 收的那场完整日更链，③ exit=0）：
`grep -c '外来 IC 重算'` = **0** ⇒ 罩子在场、一次都没触发；`trial_counter` 429→460。

初稿那句「同一批 14 条，重算尺过 11、戳记尺过 3 ⇒ 8 条被错砍」的真实身份：它是**两把尺量同一批
容器自报数**的差（若有一条形如戳记复制的产物进闸会砍掉 8 条），不是生产实测损失——生产没丢过这 8 条。

本文件为什么不是「调用过就算数」：
- 第 1~6 节量的是 `recount_foreign_ic` 与地板乙这两件**工具**的行为（戳记形状不罩必死、罩了活、
  不误伤离散形状、单标的不判别、重算不出则丢弃），全部用合成面板喂真函数，与生产走哪条腿无关。
- 第 2 节是**负对照臂**：不罩 recount 时同一枚因子在地板乙**确实死掉**。没有这一节，
  第 3 节「罩了就活」证不了任何事：两臂结果会一样。
- 第 3 节把差异**打到判据那一层**（过/不过 0.005），而不是停在「recount 被调了」。
- 第 7 节是**接线闸**（读 `main()` 源码的 AST），第 8 节给这把闸自己配**拔牙负对照**
  （同一段尺子量一份故意拆罩的源码必须判 False）——否则「接线」这类检查最容易恒真。
  它的现在意义只剩一条：那行真改成吃戳记的腿时不许裸奔；**别把它读成"生产缺口已修"**。
- 第 6 节钉 `recount_foreign_ic` 的**已知代价**：一条都重算不出的因子直接**丢弃**（`:133-135`），
  而 `main()` 里 `tc.add(len(raw_factors))`（`:714`）在罩之后。⚠️ 这一格在 `:713` 兑现不了
  （注册表腿的因子带 `impl["factor"]`，`:49`，重算必得同值 ⇒ 不会被丢）；
  真有机会兑现它的只有 `:178`/`:253` 那两处本来就存在的罩子，本批没改它们。
  写在这儿防止日后把"罩子会剔因子"当成这行的实测副作用。

全部用合成面板，不读外部数据、不碰生产产物。
"""

import ast
import os

import numpy as np
import pandas as pd

import main
from main import recount_foreign_ic, select_factors
from rdagent_facade import _official_to_impl

FLOOR = 0.005
STAMP = 0.001  # 沙箱戳记：低于地板，单看它必死


def _ar1_pool(n_codes=3, n_bars=800, phi=0.6, seed=11):
    """确定性 AR(1) 收益池：r_t = phi·r_{t-1} + eps ⇒ 今日的 `returns` 与次日收益强相关。

    造这一份是为了让「本池重算值」与「沙箱戳记」**必然分家**：重算 IC ≈ phi（≫0.005），
    而戳记写死 0.001（≪0.005）。只有这样的夹具才能让第 2/3 节给出相反判决。
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-05", periods=n_bars)
    pool = {}
    for k in range(n_codes):
        eps = rng.normal(0.0, 0.01, n_bars)
        r = np.zeros(n_bars)
        for t in range(1, n_bars):
            r[t] = phi * r[t - 1] + eps[t]
        close = 10.0 * np.cumprod(1.0 + r)
        pool["5103%02d" % k] = pd.DataFrame(
            {"open": close, "high": close * 1.001, "low": close * 0.999,
             "close": close, "volume": np.full(n_bars, 2_000_000.0)}, index=idx)
    return pool


POOL = _ar1_pool()
OFFICIAL_JSON = [{"name": "AR1 持续性", "expr": "returns", "mean_ic": STAMP}]


# ---------------------------------------------------------------- 1. 形状
def test_official_leg_copies_single_stamp_to_every_symbol():
    """`_official_to_impl` 的产物必须长成「逐标的 IC 全等」——这是 recount 那颗探针吃的形状。

    认字段会漏放（这条腿压根不写 source），所以 `recount_foreign_ic:103-104` 选择认
    「逐标的 IC 全等」。这一节钉的是：**兜底路（`MULTI_SOURCE["enabled"]=False`）真产出时
    就落在这个形状里**，否则第 3 节的"被救活"就只是夹具自己造的假象。
    ⚠️ 生产里没有任何一条腿把这个形状送到 `:713`：唯一产它的 facade 腿只被 `:178`/`:253`
    两个**早已罩住** recount 的消费点吃（`:713` 吃的是注册表腿，见文件头第 1 条）。
    """
    leg = _official_to_impl(OFFICIAL_JSON, POOL)
    assert len(leg) == 1, "表达式可求值 ⇒ 这条腿应产出 1 枚"
    ics = [v["ic"] for v in leg[0]["impl"].values()]
    assert len(ics) == len(POOL) > 1, "逐标的都该带上戳记"
    assert len(set(ics)) == 1 and ics[0] == STAMP, (
        "戳记被逐标的复制成同一个数 ⇒ 这才是「沙箱的数」")


# ---------------------------------------------------------------- 2. 负对照臂
def test_unwrapped_leg_dies_at_the_floor():
    """**不罩 recount** 时同一枚因子在地板乙被砍 ⇒ 这就是罩子缺失时兜底路的形状。

    这一节必须为「死」：若它哪天返回非空，说明地板乙不再吃戳记（改动已无必要）或地板被拨松，
    届时第 3 节的"活"就不再是证据。两臂同判＝没有测试。
    ⚠️ 它**不是**"生产在丢因子"的读数——10-06 复查：生产那一场 `grep -c '外来 IC 重算'` = 0。
    """
    leg = _official_to_impl(OFFICIAL_JSON, POOL)
    kept = select_factors(leg)
    assert kept == [], (
        "戳记 %.6f < 地板 %.3f ⇒ 不重算必死；实际留下 %d 条"
        % (STAMP, FLOOR, len(kept)))


# ---------------------------------------------------------------- 3. 正对照臂
def test_wrapped_leg_survives_the_floor():
    """罩上 recount（＝改动后那条腿的写法）同一枚因子**活到判据之后**。

    与第 2 节唯一的差别就是中间那一次重算 ⇒ 差异活到了「过/不过 0.005」这一层，
    不是停在"函数被调用过"。
    """
    leg = recount_foreign_ic(_official_to_impl(OFFICIAL_JSON, POOL), POOL)
    kept = select_factors(leg)
    assert len(kept) == 1, "本池重算值应远超地板 ⇒ 该收"


def test_surviving_mean_ic_is_the_recomputed_value_not_the_stamp():
    """活下来的那枚，闸上读的必须是**重算值**，且戳记已被就地覆盖。

    顺带钉方向：AR(1) phi>0 ⇒ 重算 IC 为正且量级 ~0.1 以上，与 0.001 的戳记差两个数量级。
    """
    leg = recount_foreign_ic(_official_to_impl(OFFICIAL_JSON, POOL), POOL)
    assert len(leg) == 1
    f = leg[0]
    assert f["mean_ic"] != STAMP, "戳记必须被覆盖，不能原样带进闸"
    assert f["mean_ic"] > 0.1, "重算 IC 实测应接近 phi，实际 %r" % f["mean_ic"]
    assert abs(f["mean_ic"]) >= FLOOR


# ---------------------------------------------------------------- 4. 不误伤
def test_native_source_factors_pass_through_untouched():
    """逐标的 IC 有离散的正常产物（注册表/LLM/genetic 那条口径）不得被重算或剔除。

    `:118-120` 的分支决定这条腿**只**救"戳记复制"形状；把正常产物一起改写会污染别的引擎口径
    （`select_factors` docstring 前提 1 讲的加权均值就是另一回事）。
    """
    f = {"name": "正常源一枚", "mean_ic": 0.02, "icir": 1.0,
         "impl": {"510300": {"factor": None, "ic": 0.011},
                  "510301": {"factor": None, "ic": 0.024},
                  "510302": {"factor": None, "ic": 0.018}}}
    out = recount_foreign_ic([f], POOL)
    assert len(out) == 1 and out[0]["mean_ic"] == 0.02, "离散 IC ⇒ 原样放行"


# ---------------------------------------------------------------- 5. 边界
def test_single_symbol_pool_is_left_as_is():
    """只有一个标的时「全等」无法判别 ⇒ 保持原样（`:111` 明写的边界）。

    钉这一格是为了说清**罩子救不了窄面板**：走兜底路时若求值只剩 1 只活着，
    戳记照样进闸。别把本文件的结论读成"所有 official 因子都已被重算"，
    也别反过来读成"生产一直在吃戳记"（现值 True，见第 1 节那条 ⚠️）。
    """
    solo = _ar1_pool(n_codes=1)
    leg = _official_to_impl(OFFICIAL_JSON, solo)
    out = recount_foreign_ic(leg, solo)
    assert len(out) == 1 and out[0]["mean_ic"] == STAMP, "单标的 ⇒ 不动"


# ---------------------------------------------------------------- 6. 代价
def test_unrecomputable_factor_is_dropped_not_passed():
    """一条都重算不出来的因子被**丢弃**（`:133-135`）——`recount_foreign_ic` 的已知代价。

    `main()` 的 `tc.add(len(raw_factors))`（`:714`）在罩之后，所以**凡是罩子真触发的那一处**，
    被剔的条数就不进试验账（DSR 多重检验的分母）。⚠️ 但这一格在 `:713` 兑现不了：注册表腿的
    因子带 `impl["factor"]`（`:49`），重算必得同值 ⇒ 不会被丢；真有机会兑现它的是 `:178`/`:253`
    那两处本批没动的罩子。10-06 那场 `trial_counter` 429→460、罩子触发 0 次。
    这一节防的是"改了以后凭空少几条没人认领"，同时也是这把尺子的**行为**读数（不是生产损失读数）。
    """
    f = {"name": "求值残渣", "mean_ic": STAMP, "icir": 0.0,
         "impl": {c: {"factor": None, "ic": STAMP} for c in POOL}}
    out = recount_foreign_ic([f], POOL)
    assert out == [], "全 None ⇒ 无入场资格，必须被剔而不是带着戳记过关"


# ---------------------------------------------------------------- 7. 接线闸
def _find_wrapped_leg(src):
    """在源码文本里找「被 recount_foreign_ic 罩住的 mine_factors(pool)」那处调用。

    返回 (call_node, enclosing_func_name)；没罩则返回 (None, None)。
    """
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "recount_foreign_ic"
                    and len(node.args) == 2
                    and isinstance(node.args[0], ast.Call)
                    and getattr(node.args[0].func, "id", None) == "mine_factors"):
                return node, fn.name
    return None, None


def test_main_leg_is_actually_wrapped():
    """生产接线：`main()` 里那行必须是罩住的，且**第二参传的是 pool**。

    ⚠️ 这把闸只证「接线没掉」，**不证**"生产缺口已修"——那行罩的是注册表腿，本身空跑
    （文件头第 1 条）。它防的是日后有人把 `:713` 换成吃戳记的腿时把罩子丢掉。

    漏传 pool 会当场 TypeError（`recount_foreign_ic(factors, pool)` 是两参），
    而这条腿要跑三小时才走到 ⇒ 必须在静态层就钉住，10-05 落地时我就先写过一次漏参。
    """
    path = os.path.abspath(main.__file__)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    node, fn_name = _find_wrapped_leg(src)
    assert node is not None, "main.py 里找不到被罩住的 mine_factors 调用 ⇒ 改动没接线"
    assert fn_name == "main", "罩子的外层函数应是 main()，实际 %r" % fn_name
    second = node.args[1]
    assert isinstance(second, ast.Name) and second.id == "pool", (
        "第二参必须是当场的 pool，实际 %r" % ast.dump(second))


def test_wrapper_matcher_has_teeth():
    """给第 7 节那把尺子自己配负对照：同一把尺量"故意拆罩"的源码必须判 False。

    接线类检查最容易恒真——只写「源码里出现了 recount_foreign_ic 字样」这种判据，
    注释里提一句就能骗过去。所以这里用同一个 AST 尺子跑三种假源码。
    """
    unwrapped = "def main():\n    raw = mine_factors(pool)\n    return raw\n"
    assert _find_wrapped_leg(unwrapped)[0] is None, "没罩的必须判 False"

    wrong_type = ("def main():\n    raw = recount_foreign_ic(mine_factors_multi"
                  "_(pool), pool)\n")
    assert _find_wrapped_leg(wrong_type)[0] is None, "罩错函数（不是 mine_factors）必须判 False"

    missing_pool = ("def main():\n    raw = recount_foreign_ic(mine_factors(pool)"
                    ")\n")
    assert _find_wrapped_leg(missing_pool)[0] is None, "漏传 pool 必须判 False"

    wrapped = "def main():\n    raw = recount_foreign_ic(mine_factors(pool), pool)\n"
    assert _find_wrapped_leg(wrapped)[0] is not None, "罩对了必须判 True（尺子没瞎）"
