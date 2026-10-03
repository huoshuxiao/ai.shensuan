#!/usr/bin/python3.10
# -*- coding: utf-8 -*-
"""负对照：把假设闸的四道闸**逐个拔掉**，证明 23 格里那四格是真的在咬

主夹具（check_hypothesis_roles_1001.py）全绿只说明"现在拦住了"，不说明
"是这一行拦的"。这里每次只动一处（拔掉一道闸或换成空操作），要求**读数必须变**：
    N1 拔静态体检（check_expr 恒返回 ''）⇒ 负窗口写法进定稿（主夹具 丁-1 判红）
    N2 拔判重（_normalize 加唯一后缀）⇒ 第 2 轮重复写法照样进（主夹具 壬-2 判红）
    N3 把「审稿人漏判 = 拒收」那一段换成「漏判 = 放行」⇒ 定稿从 0 条变 2 条
       （主夹具 辛-1 判红）。这格走源码手术：读真模块、改那一行、exec 成新模块，
       因为"默认放行"这个反事实不能靠调参数造出来
    N4 把坏 JSON 的容错换成直传 json.loads ⇒ 异常炸穿整轮（主夹具 庚-1 判红）

四格都是**对照格**：期望的是"拔掉就漏"，所以这里的「放行/判红」= 拔掉闸门后
坏写法通过 / 仍被拦住。四格全绿 = 四道闸都承重；任何一格红 = 那道闸白装。
"""
import json
import os
import sys
import types

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
import _bootstrap  # noqa: F402,E402

import hypothesis_roles as HR  # noqa: E402

CACHE = os.path.join(ROOT, "common/data/etf/cache")
POOL = {}
for c in ["510300", "510500", "159516"]:
    POOL[c] = (pd.read_csv(os.path.join(CACHE, f"{c}_daily.csv"),
                           parse_dates=["date"])
               .set_index("date").sort_index())
HR.HYPOTHESIS_ROLES = True

HYP = [{"id": 1, "idea": "近 20 日涨幅延续", "channel": "趋势", "expect": "正"}]
GOOD = [{"name": "mom_20", "expr": "delta(close, 20) / (ma(df, 60) + 1e-9)",
         "idea": "近 20 日涨幅延续"}]
FUTURE = [{"name": "sneak_5", "expr": "delay(close, -5) / (ma(df, 60) + 1e-9)",
           "idea": "偷看未来"}]
J = json.dumps


def gate(script):
    g = HR.HypothesisGate(POOL, type("L", (), {"enabled": True})())

    def role(system, user):
        if "返工" in user:
            return "修正"
        if "翻译成因子表达式" in system:
            return "代码实现"
        if "只提**经济学假设**" in system:
            return "假设生成"
        if "苛刻的审稿人" in system:
            return "批判者"
        if "你是复盘者" in system:
            return "反思者"
        raise AssertionError(system[:24])

    def fake_chat(system, user):
        r = role(system, user)
        g.stats["calls"] += 1
        if r not in script:
            raise AssertionError(f"没准备 {r} 的应答")
        return script[r]
    g._chat = fake_chat
    return g


FAILS = []


def check(tag, cond, detail):
    ok = bool(cond)
    if not ok:
        FAILS.append(f"{tag} {detail}")
    print(f"  {'✅' if ok else '❌'} {tag}｜{detail}")


# ---------- N1 拔静态体检 ----------
print("\n[N1] 拔掉 check_expr ⇒ 未来函数应当漏过去")
g = gate({"假设生成": J({"hypotheses": HYP}),
          "代码实现": J({"factors": FUTURE}),
          "批判者": J({"verdicts": [{"name": "sneak_5", "verdict": "pass",
                                      "problem": "", "fix_expr": ""}]})})
base = g.generate()
check("N1-生产", base == [], f"生产闸下定稿 {len(base)} 条（拦住了）")
_orig_check = HR.check_expr
HR.check_expr = lambda e: ""
try:
    g2 = gate({"假设生成": J({"hypotheses": HYP}),
               "代码实现": J({"factors": FUTURE}),
               "批判者": J({"verdicts": [{"name": "sneak_5", "verdict": "pass",
                                           "problem": "", "fix_expr": ""}]})})
    leaked = g2.generate()
finally:
    HR.check_expr = _orig_check
check("N1-对照", [x["name"] for x in leaked] == ["sneak_5"],
      f"拔掉静态体检后 delay(close,-5) 进了定稿 {[x['name'] for x in leaked]}"
      f"（= 丁-1 那格确实由 check_expr 这一行咬住）")

# ---------- N2 拔判重 ----------
print("\n[N2] 拔掉判重 ⇒ 第 2 轮重复写法应当漏过去")
g = gate({"假设生成": J({"hypotheses": HYP}),
          "代码实现": J({"factors": GOOD}),
          "批判者": J({"verdicts": [{"name": "mom_20", "verdict": "pass",
                                      "problem": "", "fix_expr": ""}]})})
r1 = g.generate()
r2 = g.generate()
check("N2-生产", len(r1) == 1 and r2 == [],
      f"第 1 轮定稿 {len(r1)} 条、第 2 轮 {len(r2)} 条（判重拦下）")
_orig_norm = HR._normalize
HR._normalize = lambda e: f"{e}#{id(object())}"
try:
    g3 = gate({"假设生成": J({"hypotheses": HYP}),
               "代码实现": J({"factors": GOOD}),
               "批判者": J({"verdicts": [{"name": "mom_20", "verdict": "pass",
                                           "problem": "", "fix_expr": ""}]})})
    p1 = g3.generate()
    p2 = g3.generate()
finally:
    HR._normalize = _orig_norm
check("N2-对照", len(p1) == 1 and len(p2) == 1,
      f"拔掉判重后第 2 轮仍定稿 {len(p2)} 条（= 壬-2 那格由 _normalize 咬住）")

# ---------- N3 漏判=放行 的源码手术 ----------
print("\n[N3] 把「漏判按拒收」换成「漏判按放行」⇒ 该漏的两条要漏进来")
g = gate({"假设生成": J({"hypotheses": HYP}),
          "代码实现": J({"factors": GOOD}),
          "批判者": J({"verdicts": []})})          # 审稿人一条没签
prod = g.generate()
check("N3-生产", prod == [],
      f"生产实现下漏判=拒收，定稿 {len(prod)} 条、reject={g.stats['reject']}")
src = open(os.path.join(ROOT, "common/src/core/hypothesis_roles.py"),
           encoding="utf-8").read()
old = """            if v is None:
                self.stats["reject"] += 1"""
new = """            if v is None:
                self.stats["reject"] += 1
                accepted.append(f)
                continue"""
assert src.count(old) == 1, f"反事实锚点没找到（命中 {src.count(old)} 处）"
mod = types.ModuleType("hr_mutant")
mod.__dict__["__file__"] = "hypothesis_roles_mutant"
exec(compile(src.replace(old, new), "hr_mutant", "exec"), mod.__dict__)
gm = mod.HypothesisGate(POOL, type("L", (), {"enabled": True})())


def fake_m(system, user):
    gm.stats["calls"] += 1
    if "只提**经济学假设**" in system:
        return J({"hypotheses": HYP})
    if "翻译成因子表达式" in system:
        return J({"factors": GOOD})
    return J({"verdicts": []})


gm._chat = fake_m
leaked3 = gm.generate()
check("N3-对照", [x["name"] for x in leaked3] == ["mom_20"],
      f"改那一行后未签字的 mom_20 进了定稿 {[x['name'] for x in leaked3]}"
      f"（= 辛-1 那格确实由「按拒收计」那两行咬住）")

# ---------- N4 坏 JSON 容错 ----------
print("\n[N4] 把坏 JSON 的容错换成直传 ⇒ 异常要炸穿整轮")
g = gate({"假设生成": "这不是 JSON", "代码实现": J({"factors": GOOD}),
          "批判者": J({"verdicts": [{"name": "mom_20", "verdict": "pass",
                                      "problem": "", "fix_expr": ""}]})})
r = g.generate()
check("N4-生产", r == [] and g.stats["json_fail"] == 1,
      f"生产实现下坏 JSON 只作废本趟：定稿 {len(r)} 条、json_fail="
      f"{g.stats['json_fail']}、没抛异常")
g2 = gate({"假设生成": "这不是 JSON", "代码实现": J({"factors": GOOD}),
           "批判者": J({"verdicts": []})})
g2._json = lambda content, key, tag: json.loads(content).get(key, [])
raised = ""
try:
    g2.generate()
except Exception as e:
    raised = f"{type(e).__name__}"
check("N4-对照", raised == "JSONDecodeError",
      f"去掉容错后同一趟直接抛 {raised or '没抛'}（= 庚-1 那格由 _json 的 "
      f"try/except 咬住，而它把异常转成了计数与打印）")

print(f"\n合计：4 组对照（{len(FAILS)} 条不符预期）")
for f in FAILS:
    print(f"  ❌ {f}")
sys.exit(1 if FAILS else 0)
