"""P3（两处剔除阈值）在看板上的落地自检：措辞真的渲染出来、且没有未求值的占位符

判据在 config（ASHARE_BUY_MIN_HITS=3）、实现只在 ashare_screen.screen_on_date 一处，
但**给两个人看的口径**分散在看板三处：待买入页脚注、「不该买」页阈值行与页脚、
「📖 口径」表。这些全是 f-string —— 漏一个花括号或键名写错，Streamlit 不会崩，
只会把 `{ASHARE_BUY_MIN_HITS}` 原样印在页面上，于是页面上出现一句读不懂的口径。
所以按文本断言，而不是只看「8 个 tab 无 exception」。

用 /usr/bin/python3.10 跑。
"""
import re
from streamlit.testing.v1 import AppTest

APP = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src/app.py"
at = AppTest.from_file(APP, default_timeout=300)
at.run()
print("exceptions:", len(at.exception))
for e in at.exception:
    print("  !!", e.value)

blob = []
for attr in ("markdown", "caption", "text", "info", "warning", "error",
             "title", "header", "subheader"):
    for el in getattr(at, attr, []):
        blob.append(str(getattr(el, "value", "")))
for d in at.dataframe:
    blob.append(str(d.value))
all_text = "\n".join(blob)

NEEDLES = [
    "≥3 条量能构造一致判响",       # 待买入页脚注（共识爆量那道闸）
    "并集在减法腿值 +6.42%/年",     # 同一页脚注里的依据
    "今日 496 只命中",             # ← 计数真从 meta 读出来（09-23 生产实跑）
    "1801 只在域里被剔",           # 域剔、名单放行的只数
]
for n in NEEDLES:
    print(f"  渲染 {n!r}: {n in all_text}")
# 未求值的 f-string 残留 = 键名写错或漏了 f 前缀
leak = [x for x in re.findall(r"\{[A-Za-z_][A-Za-z0-9_ .\[\]'\"*:/\-]*\}", all_text)
        if "ASHARE" in x or "bst." in x or x in ("{k}", "{v}", "{c}")]
print("未求值占位符:", leak[:8] or "无")
# 「不该买」页那张表：n_hit 是计数列，不能被当成一条构造（否则会出现第 5 个假指标）
sig = [d for d in at.dataframe if "excluded_by" in [str(c) for c in d.value.columns]]
for d in sig:
    cols = [str(c) for c in d.value.columns]
    print("剔除表列:", cols)
    print("n_hit 在表里:", "n_hit" in cols, "｜被当成构造渲染:",
          bool({"n_hit"} & {c for c in cols if c not in
                            ("code", "close", "amount20_yi", "excluded_by",
                             "n_hit", "keep")}))
