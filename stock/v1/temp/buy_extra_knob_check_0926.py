# -*- coding: utf-8 -*-
"""选项E 落地后的对账：三场重跑（A 关闸 / B 刀口0.80 / C 刀口0.85）逐字节比到哪儿。

判据不恒真的三件事：
    A vs 生产归档   三张表必须**逐字节相同**；meta 只许差在「与闸无关的时钟字段」和
                    「这道闸的自报键」，而且自报键在 A 场必须是惰值（0 只命中）
                    ⇒ 证明这次改代码没夹带刀口之外的任何漂移
    B vs ㉟ 那一场   必须逐字节相同 ⇒ 证明 0.80 那一版在新代码下可复现（旋钮没串味）
    C vs B          **不该**相同；相同就说明 09-24 这天两档切到同一批票，如实报出来
最后按名次逐行念清：0.85 相对 0.80 换掉了名单里哪几只、下单那 5 席动没动。

⚠️ 拿来做基线的那场生产归档（09-25 23:56 写的）跑在**接线之前**，本来就没有这道闸的
   自报键，所以 meta 不可能整体相等 —— 差在哪几键要逐条点名，不能一句「大致相同」放过。
"""
import json
import hashlib
import os
import sys

import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
OUT = os.path.join(ROOT, "stock/v1/temp/tmp_buy_extra_knob_0926")
ARCH = os.path.join(ROOT, "stock/v1/data/results/daily_signal")
WIRE_ON = os.path.join(ROOT, "stock/v1/temp/wire_extra_0926/on")
TAG = "20260924"
FILES = [f"signal_{TAG}.csv", f"buy_{TAG}.csv", f"order_{TAG}.csv"]

# meta 里允许差的键：① 与闸无关的时钟/新鲜度（age_days 每次跑都在走）
# ② 这道闸的自报键（归档那场跑在接线前，压根没有这三键）
META_ALLOW = {"industry", "buy_extra_rules", "buy_extra_quantile"}
BUY_ALLOW = {"buy_extra_fired", "n_extra_in_pool", "n_extra_net"}


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def cmp_tables(a, b, label):
    bad = []
    for f in FILES:
        pa, pb = os.path.join(a, f), os.path.join(b, f)
        ha, hb = md5(pa), md5(pb)
        print(f"  [{label}] {f:<22} {'逐字节相同' if ha == hb else '**不同**'}"
              f"　{os.path.getsize(pa)}B　{ha[:8]} vs {hb[:8]}")
        if ha != hb:
            bad.append(f)
    return bad


def diff_meta(ma, mb, path="", acc=None):
    """递归列出两场 meta 的值差（字典按并集键、列表按下标）"""
    acc = [] if acc is None else acc
    if isinstance(ma, dict) and isinstance(mb, dict):
        for k in sorted(set(ma) | set(mb)):
            va, vb = ma.get(k, "⟨缺⟩"), mb.get(k, "⟨缺⟩")
            if va != vb:
                diff_meta(va, vb, f"{path}.{k}", acc) if isinstance(va, dict) and isinstance(vb, dict) \
                    else acc.append((path + "." + k, va, vb))
    elif isinstance(ma, list) and isinstance(mb, list):
        if ma != mb:
            acc.append((path + "[]", ma, mb))
    else:
        acc.append((path, ma, mb))
    return acc


def leaf(key):
    return key.lstrip(".").split(".")[0].split("[")[0]


def allowed(k):
    """差在「与闸无关的时钟键」上，或差在 buy_stats 里那三个自报键上 ⇒ 允许"""
    if leaf(k) in META_ALLOW:
        return True
    parts = k.lstrip(".").split(".")
    return len(parts) > 1 and parts[0] == "buy" and parts[1] in BUY_ALLOW


def judge(label, dA, dB):
    """跑一场的 A/B 对表：三张表 + meta 差集，差集必须只落在白名单里"""
    bad = cmp_tables(dA, dB, label)
    ma = json.load(open(os.path.join(dA, f"meta_{TAG}.json")))
    mb = json.load(open(os.path.join(dB, f"meta_{TAG}.json")))
    ds = diff_meta(ma, mb)
    viol = [(k, a, b) for k, a, b in ds if not allowed(k)]
    print(f"  [{label}] meta 值差 {len(ds)} 处（判据级 {len(viol)} 处）：")
    for k, a, b in ds:
        print(f"     {'违规' if any(v[0] == k for v in viol) else '允许'}  "
              f"{k:<34} 本场={json.dumps(a, ensure_ascii=False)[:46]}　"
              f"对照={json.dumps(b, ensure_ascii=False)[:46]}")
    if bad:
        raise SystemExit(f"  ⇒ {label}：三张表有差 ⇒ 改动面溢出了刀口，读数作废")
    if viol:
        raise SystemExit(f"  ⇒ {label}：meta 差在判据上（{[v[0] for v in viol]}），"
                         f"不是「自报键 + 时钟」能解释的")
    print(f"  ⇒ {label} 通过：判据级字段一字未动（时钟/自报键除外）")
    return ma, mb


print("===== 一、A（关掉这道闸）vs 生产归档：三张表必须逐字节相同 =====")
ma, march = judge("A vs 归档", os.path.join(OUT, "a_off"), ARCH)
# 自报键在 A 场必须是惰值 —— 否则「关掉」是假的
for k, want in (("buy_extra_rules", []), ("buy_extra_quantile", 0.85)):
    got = ma.get(k, "⟨缺⟩")
    print(f"  [A 自报] {k} = {got}（期望 {want}）")
    assert got == want, f"A 场的 {k} 不是期望值 ⇒ 旋钮没生效"
bb = ma["buy"]
print(f"  [A 自报] buy.n_extra_in_pool={bb['n_extra_in_pool']} n_extra_net={bb['n_extra_net']} "
      f"fired={bb['buy_extra_fired']}")
assert bb["n_extra_in_pool"] == 0 and bb["n_extra_net"] == 0 and bb["buy_extra_fired"] == {}, \
    "A 场关闸却仍有命中 ⇒ 关不掉"
print("  ⇒ 通过：关闸是真空挡（0 只命中），且与归档同形")

print("\n===== 二、B（刀口 0.80）vs ㉟ 当晚那一版：必须逐字节相同 =====")
mb, _ = judge("B vs ㉟", os.path.join(OUT, "b_q080"), WIRE_ON)

print("\n===== 三、C（刀口 0.85）vs B（0.80）：这里**不该**完全相同 =====")
bad = cmp_tables(os.path.join(OUT, "c_q085"), os.path.join(OUT, "b_q080"), "C vs B")
mcc = json.load(open(os.path.join(OUT, "c_q085", f"meta_{TAG}.json")))
print(f"  [C 刀口自报] buy_extra_quantile={mcc['buy_extra_quantile']}")
print(f"  [各档自报] A 关闸 命中 {ma['buy']['n_extra_in_pool']}　"
      f"B 0.80 池内 {mb['buy']['n_extra_in_pool']} / 净新增 {mb['buy']['n_extra_net']}　"
      f"C 0.85 池内 {mcc['buy']['n_extra_in_pool']} / 净新增 {mcc['buy']['n_extra_net']}")
if not bad:
    print("  ⚠️ C 与 B 三张表逐字节相同 ⇒ 09-24 这天两档切到同一批票，这场样本读不出深浅差别")

buy = {t: pd.read_csv(os.path.join(OUT, d, f"buy_{TAG}.csv"), dtype={"code": str})
              .set_index("code")
       for t, d in (("B", "b_q080"), ("C", "c_q085"))}
swapped = [c for c in buy["B"].index if c not in buy["C"].index]
added = [c for c in buy["C"].index if c not in buy["B"].index]
print(f"\n[名单换血 0.80→0.85] 出去 {len(swapped)} 只 {swapped}　进来 {len(added)} 只 {added}")
for c in swapped + added:
    side = "出(0.80)" if c in swapped else "进(0.85)"
    r = (buy["B"] if c in swapped else buy["C"]).loc[c]
    print(f"  {side} {c} 第{int(r['rank']):>2}名 {r['板块']}／{r['行业']}　"
          f"20日均额 {r['amount20_yi']:.2f} 亿　当日涨幅 {r['当日涨幅']:+.2%}")
oB = pd.read_csv(os.path.join(OUT, "b_q080", f"order_{TAG}.csv"), dtype={"code": str})
oC = pd.read_csv(os.path.join(OUT, "c_q085", f"order_{TAG}.csv"), dtype={"code": str})
o_sw = [c for c in oB["code"] if c not in set(oC["code"])]
print(f"[下单 5 席] 0.80→0.85 换掉 {len(o_sw)} 只 {o_sw or '（一只没换）'}")
print(f"        B：{list(oB['code'])}　C：{list(oC['code'])}")
print("\n[读法] 三场同一信号日、同一份面板、同一套判据，只有名单独立闸那根刀口不一样")
sys.exit(0)
