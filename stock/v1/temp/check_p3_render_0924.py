"""P3（两处剔除阈值）在看板上的落地自检：措辞真的渲染出来、且没有未求值的占位符

判据在 config（ASHARE_BUY_MIN_HITS=3）、实现只在 `ashare_screen.screen_on_date` 一处，
但**给两个人看的口径**分散在看板三处：待买入页脚注、「不该买」页阈值行与页脚、
「📖 口径」表。这些全是 f-string —— 漏一个花括号或键名写错，Streamlit 不会崩，
只会把 `{ASHARE_BUY_MIN_HITS}` 原样印在页面上，于是页面上出现一句读不懂的口径。
所以按文本断言，而不是只看「8 个 tab 无 exception」。

两类针分开，别混着写死（09-27 回归时被挑出来的正是这件事）：
  ①**静态针** = 与日期无关的口径措辞。上一版这里的「并集在减法腿值 +6.42%/年」已经
    跟着 ⑮P0 那次重量变成了 +6.46%，针没跟上 ⇒ 假阴性。
  ②**动态针** = 「今日 461 只命中」「1833 只在域里被剔」这种**从生产刷新的计数**，
    一律从最新一份 `meta_*.json` 读出来再拼针（09-23 那版写死的是 496/1801，
    日更一次就废）。判据本身仍不在这个脚本里重算。

用 /usr/bin/python3.10 跑。
"""
import glob
import json
import os
import re
import sys

from streamlit.testing.v1 import AppTest

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401,E402
from config import ASHARE_SIGNAL_DIR  # noqa: E402

APP = os.path.join(SRC, "app.py")
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

# ---------- 动态针的读数来源：最新一场日更自己的 meta（不写死日期） ----------
ps = glob.glob(os.path.join(ASHARE_SIGNAL_DIR, "meta_*.json"))
if not ps:
    raise SystemExit(f"[无产物] {ASHARE_SIGNAL_DIR} 下没有 meta_*.json，无场次可验")
mp = max(ps)
buy = (json.load(open(mp)).get("buy") or {})
for k in ("buy_min_hits", "n_consensus", "n_lenient_vs_union"):
    if buy.get(k) is None:
        raise SystemExit(f"[无读数] {os.path.basename(mp)} 里缺 buy.{k}，"
                         f"这一场比 P3 落地还早，本脚本判不了它")
print(f"[读数来源] {os.path.basename(mp)}：{len(ps)} 场里最新一份")

NEEDLES_STATIC = [
    "同一批判据、两个强度",              # 待买入页脚注那句总起
    "≥1 条即剔",                        # 「不该买」那张域的强度
    "并集在减法腿值 +6.46%/年",          # 脚注里给的依据
    "还要看安静度排名",                  # 脚注的边界话（防止读成「未达共识就必进名单」）
]
NEEDLES_DERIVED = [
    f"≥{buy['buy_min_hits']} 条量能构造一致判响",
    f"今日 {buy['n_consensus']} 只命中",
    f"{buy['n_lenient_vs_union']} 只在域里被剔",
]
for n in NEEDLES_STATIC + NEEDLES_DERIVED:
    print(f"  渲染 {n!r}: {n in all_text}")
miss = [n for n in NEEDLES_STATIC + NEEDLES_DERIVED if n not in all_text]

# 未求值的 f-string 残留 = 键名写错或漏了 f 前缀
leak = [x for x in re.findall(r"\{[A-Za-z_][A-Za-z0-9_ .\[\]'\"*:/\-]*\}", all_text)
        if "ASHARE" in x or "bst." in x or x in ("{k}", "{v}", "{c}")]
print("未求值占位符:", leak[:8] or "无")

# 「不该买」页那张表：n_hit 是计数列，不能被当成一条构造（否则会出现第 5 个假指标）
sig = [d for d in at.dataframe if "excluded_by" in [str(c) for c in d.value.columns]]
if not sig:
    raise SystemExit("[没渲染] 找不到「不该买」那张带 excluded_by 的表，下面两条判据作废")
cols = [str(c) for c in sig[0].value.columns]
RULE_COLS = {"量能水平", "量能波动", "量能动量", "量能比"}
NON_RULE = {"code", "close", "amount20_yi", "excluded_by", "n_hit", "keep"}
rule_cols_in_table = [c for c in cols if c not in NON_RULE]
print("剔除表列:", cols)
has_n_hit = "n_hit" in cols
# 判据 = 「表里的构造列恰好那四条、n_hit 不混在其中」。
# 旧版写的是 `{"n_hit"} & {不在白名单里的列}`，那个交集恒为空 ⇒ 抓不住任何东西，
# 是回归时发现的又一处无牙判据，这里换成会失败的形状。
as_rule = set(rule_cols_in_table) != RULE_COLS
print(f"表内构造列: {rule_cols_in_table}")
print(f"n_hit 在表里: {has_n_hit} ｜构造列形状不对（n_hit 混进来/构造被改名）: {as_rule}")

# ---------- 判据：任何一项不合格就非零退出 ----------
# 09-27 回归时发现这一节整段不存在：三枚针 False 也只打印、照样 exit 0，
# 等于「报了问题还放行」——打印给人看，退出码才是给链路用的。
FAIL = []
if at.exception:
    FAIL.append(f"看板有 {len(at.exception)} 处 exception")
if miss:
    FAIL.append(f"针未命中 {len(miss)}/{len(NEEDLES_STATIC) + len(NEEDLES_DERIVED)}: {miss}")
if leak:
    FAIL.append(f"未求值占位符 {leak[:4]}")
if not has_n_hit:
    FAIL.append("meta 里带 n_hit、页面上那张表却没有 ⇒ 计数列没接上看板")
if as_rule:
    FAIL.append(f"表内构造列 ≠ 那四条量能构造（实为 {rule_cols_in_table}）")
print(f"\n[P3 渲染自检] 静态 {len(NEEDLES_STATIC)} + 派生 {len(NEEDLES_DERIVED)} 枚针"
      f"{'，全部通过' if not FAIL else '，失败 ' + str(len(FAIL)) + ' 条：' + '；'.join(FAIL)}")
sys.exit(1 if FAIL else 0)
