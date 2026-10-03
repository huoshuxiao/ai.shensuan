# -*- coding: utf-8 -*-
"""件1（10-01）：五处 ETF 侧提示词点名「可以直接传列」之后，这句话是不是真话

只做两件事，全程不调 LLM、不写任何生产产物：
  T1 **抠正文真求值**：把提示词里点名的传列写法（`ma(volume, 20)` 这种）用正则从
     prompt 字符串里抓出来，逐个交给 `safe_eval` 打在真 ETF 日线 CSV 上。
     抓不到例子=红；抓到但求值失败=红 ⇒ 提示词不能教模型写一条求值会炸的表达式。
     （不手写行形：这里验的就是模型照抄提示词会抄出来的那串字符。）
  T2 **差异要活到判据那一层**：每个例子还要和「用 pandas 独立算同一根列的滚动统计」
     逐位对表，并且必须**不等于**同一算子打在 close 上的结果 ⇒ 证明它真吃了那一列，
     而不是嘴上说传列、手上偷偷取 close。
  T3 两条负对照：① 把这句话从 prompt 里摘掉，检测器必须当场报红（否则 T1 是恒真）；
     ② 把 `factor_dsl._price_series` 换回 10-01「乙」之前的写法（只认 DataFrame），
     传列例子必须**求值失败** ⇒ 证明提示词现在这句靠的是「乙」那笔，哪天撤了「乙」，
     这句点名会立刻变成假话、被本脚本抓住。
"""
import os
import re
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import factor_dsl as FD                     # noqa: F402,E402
from factor_static_check import check_expr as FD_check  # noqa: F402,E402
import hypothesis_roles as HR               # noqa: F402,E402
import llm_factor_agent as LA               # noqa: F402,E402
import llm_genetic_hybrid as GH             # noqa: F402,E402
import llm_crossover_operator as CO         # noqa: F402,E402
import llm_mutation_operator as MO          # noqa: F402,E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
CODE = "510300"

# 提示词点名的四个算子 → pandas 里的独立复算口径
ROLL = {"ma": "mean", "std": "std", "max": "max", "min": "min"}
COLS = ("close", "open", "high", "low", "volume")

SITES = [("hypothesis_roles.PROMPT_CODER（四角色·代码实现）", HR.PROMPT_CODER),
         ("llm_factor_agent.SYSTEM_PROMPT（单角色原路）", LA.SYSTEM_PROMPT),
         ("llm_genetic_hybrid.SEED_SYSTEM_PROMPT（遗传种子）", GH.SEED_SYSTEM_PROMPT),
         ("llm_crossover_operator.CROSSOVER_SYSTEM_PROMPT", CO.CROSSOVER_SYSTEM_PROMPT),
         ("llm_mutation_operator.MUTATION_SYSTEM_PROMPT", MO.MUTATION_SYSTEM_PROMPT)]

# 点名这句话的长相：四个算子名里至少一个 + 「可以直接传列」（顺序不限，跨行也吃）
NAMED = re.compile(r"ma/std/max/min[^。\n]*可以直接传列|第一个参数\*\*可以直接传列\*\*",
                   re.S)
# 从正文里抠「算子(列名, 数字)」形态的例子，只认提示词真点名的四算子×真列名
EXAMPLE = re.compile(r"\b(ma|std|max|min)\((" + "|".join(COLS) + r"),\s*(\d+)\)")


def load_df():
    df = pd.read_csv(os.path.join(CACHE, f"{CODE}_daily.csv"),
                     parse_dates=["date"]).set_index("date").sort_index()
    df.columns = [str(x).lower() for x in df.columns]
    return df


def independent(df, fn, col, n):
    """不碰 DSL：直接用 pandas 算同一根列的滚动统计（close 那版留给对照）"""
    return getattr(df[col].rolling(int(n)), ROLL[fn])()


def main():
    df = load_df()
    print(f"标的 {CODE}：{len(df)} 根日线，列={sorted(df.columns)[:6]}")
    print("=" * 78)

    ok_all = True
    for label, prompt in SITES:
        named = bool(NAMED.search(prompt))
        examples = sorted(set(EXAMPLE.findall(prompt)))
        print(f"【{label}】点名={('✅ 有' if named else '❌ 没点名')}、"
              f"抠到例子 {len(examples)} 个")
        if not named:
            print("   ❌ 这句提示词没点名「可以直接传列」")
            ok_all = False
        if not examples:
            print("   ❌ 正文里一个传列例子都没写 ⇒ 模型没有可抄的写法")
            ok_all = False
        for fn, col, n in examples:
            expr = f"{fn}({col}, {n})"
            try:
                got = FD.safe_eval(expr, df)
            except Exception as e:
                print(f"   ❌ {expr:22s} 求值失败: {type(e).__name__}: {e}")
                ok_all = False
                continue
            ref = independent(df, fn, col, n)
            both = got.to_numpy(dtype="float64")
            r = ref.to_numpy(dtype="float64")
            same = np.array_equal(both, r, equal_nan=True)
            # 「必须不等于吃 close 那版」这一腿只在例子本身不吃 close 时才有意义
            # （`min(close, 5)` 这种两臂恒等，是定义不是缺陷 ⇒ 不当判据使）
            if col == "close":
                differs, note = True, "（例子本身就吃 close，此腿不适用）"
            else:
                close_ver = FD.safe_eval(
                    f"{fn}(close, {n})", df).to_numpy(dtype="float64")
                differs, note = not np.array_equal(both, close_ver,
                                                   equal_nan=True), ""
            flag = "✅" if (same and differs) else "❌"
            print(f"   {flag} {expr:22s} 与 pandas 独立复算逐位同={same}；"
                  f"与吃 close 那版不同={differs}{note}")
            # 第四腿：提示词教的写法不能被 10-01 那道写库闸自己拒掉
            # （否则模型照抄→IC 过线→入库那一步当场挡，白烧一轮）
            rej = FD_check(expr)
            print(f"      {'✅' if not rej else '❌'} 写库闸 `check_expr` 放行"
                  f"{'' if not rej else f'：{rej}'}")
            if rej:
                ok_all = False
            if not (same and differs):
                if same:
                    mx = np.nanmax(np.abs(both - r)) if len(both) else float("nan")
                    print(f"      （max|Δ|={mx:.3e}）")
                ok_all = False

    print("=" * 78)
    print("T3-① 负对照：把点名那句话摘掉，检测器必须报红")
    sample = LA.SYSTEM_PROMPT
    stripped = NAMED.sub("", sample)
    hit = bool(NAMED.search(stripped))
    print(f"   {'✅ 被抓' if not hit else '❌ 摘掉后仍判定为点名 ⇒ 检测器是恒真'}"
          f"（摘掉前={bool(NAMED.search(sample))}，摘掉后={hit}）")
    if hit:
        ok_all = False

    print("T3-② 负对照：撤掉 10-01「乙」（_price_series 退回只认 DataFrame），"
          "传列例子必须求值失败")
    old = FD._price_series
    FD._price_series = lambda x: x["close"]
    try:
        survived = []
        for fn, col, n in [("ma", "volume", 20), ("std", "low", 60), ("max", "high", 240)]:
            try:
                FD.safe_eval(f"{fn}({col}, {n})", df)
                survived.append(f"{fn}({col}, {n})")
            except Exception:
                pass
        print(f"   {'✅ 三条全炸' if not survived else '❌ 撤了乙还求得出: ' + str(survived)}"
              f"（撤乙后存活 {len(survived)}/3）")
        if survived:
            ok_all = False
    finally:
        FD._price_series = old
    # 恢复后必须立刻又能算 ⇒ 证明上面那三炸是那一行造成的，不是求值器坏了
    back = FD.safe_eval("ma(volume, 20)", df).notna().sum()
    print(f"   ✅ 拷回后 `ma(volume, 20)` 恢复可算（非缺值 {back} 根）")

    print("=" * 78)
    print(f"结论: {'全部通过' if ok_all else '有红'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
