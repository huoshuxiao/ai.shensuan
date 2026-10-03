# -*- coding: utf-8 -*-
"""09-26 夜回退验账：默认配置那一场（D）必须与显式 0.80 那一场（B）逐字节相同。

三件事，每件都可能失败（不是恒真判据）：
    D vs B  三张表**必须**逐字节相同，meta 只许差在时钟/自报键
            ⇒ 证明 config 默认值真的等于 0.80，回退只动了口径文字没动判据
    D vs C  三张表**必须不同**（C 是刀口 0.85 那版）
            ⇒ 证明这场真的吃到了旋钮；若相同就是 env 泄漏或默认值写错，当场作废
    D 自报  meta.buy_extra_quantile 必须 == 0.80 且 == ASHARE_SCREEN_QUANTILE
            ⇒ 两根旋钮现值相同这件事由进程自己说出来，不靠我读代码
"""
import hashlib
import json
import os
import sys

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F402,E401  (裸模块名导入的 sys.path 引导)
from config import ASHARE_BUY_EXTRA_QUANTILE, ASHARE_SCREEN_QUANTILE  # noqa: E402
from ashare_screen import buy_extra_block  # noqa: E402

OUT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/temp/tmp_buy_extra_knob_0926"
TAG = "20260924"
FILES = [f"signal_{TAG}.csv", f"buy_{TAG}.csv", f"order_{TAG}.csv"]
META_ALLOW = {"industry", "buy_extra_rules", "buy_extra_quantile"}
BUY_ALLOW = {"buy_extra_fired", "n_extra_in_pool", "n_extra_net"}


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def tables(dX, dY, label, want_same):
    diff = []
    for f in FILES:
        pa, pb = os.path.join(dX, f), os.path.join(dY, f)
        same = md5(pa) == md5(pb)
        print(f"  [{label}] {f:<22} {'逐字节相同' if same else '**不同**'}"
              f"　{os.path.getsize(pa)}B")
        if not same:
            diff.append(f)
    if want_same and diff:
        raise SystemExit(f"  ⇒ {label}：默认配置没复现出 0.80 那一场（{diff}）⇒ 回退不干净")
    if not want_same and not diff:
        raise SystemExit(f"  ⇒ {label}：两档刀口切出**完全相同**的三张表 ⇒ 旋钮没生效，读数作废")
    return diff


def diff_meta(ma, mb, path="", acc=None):
    acc = [] if acc is None else acc
    if isinstance(ma, dict) and isinstance(mb, dict):
        for k in sorted(set(ma) | set(mb)):
            va, vb = ma.get(k, "⟨缺⟩"), mb.get(k, "⟨缺⟩")
            if isinstance(va, dict) and isinstance(vb, dict):
                diff_meta(va, vb, f"{path}.{k}", acc)
            elif va != vb:
                acc.append((path + "." + k, va, vb))
    elif ma != mb:
        acc.append((path + "[]", ma, mb))
    return acc


def allowed(k):
    leaf = k.lstrip(".").split(".")[0].split("[")[0]
    if leaf in META_ALLOW:
        return True
    parts = k.lstrip(".").split(".")
    return len(parts) > 1 and parts[0] == "buy" and parts[1] in BUY_ALLOW


D, B, CC = (os.path.join(OUT, t) for t in ("d_default", "b_q080", "c_q085"))
md, mb, mc = (json.load(open(os.path.join(d, f"meta_{TAG}.json"))) for d in (D, B, CC))

print("===== 一、进程自报的默认值 =====")
print(f"  config.ASHARE_BUY_EXTRA_QUANTILE = {ASHARE_BUY_EXTRA_QUANTILE}"
      f"　config.ASHARE_SCREEN_QUANTILE = {ASHARE_SCREEN_QUANTILE}")
print(f"  buy_extra_block 默认阈值         = {buy_extra_block.__defaults__[0]}")
print(f"  D 场 meta.buy_extra_quantile     = {md['buy_extra_quantile']}")
assert ASHARE_BUY_EXTRA_QUANTILE == 0.80, "默认值没改成 0.80"
assert md["buy_extra_quantile"] == ASHARE_BUY_EXTRA_QUANTILE, "meta 自报 ≠ 进程配置"
assert buy_extra_block.__defaults__[0] == ASHARE_BUY_EXTRA_QUANTILE, \
    "判据单点的默认阈值还挂在旧值上 ⇒ 三处调用点会分叉"
print("  ⇒ 通过：三处（config / meta 自报 / 判据单点默认参数）同值")

print("\n===== 二、D（默认配置）vs B（显式 0.80）：必须逐字节相同 =====")
tables(D, B, "D vs B", want_same=True)
ds = [(k, a, b) for k, a, b in diff_meta(md, mb) if not allowed(k)]
print(f"  [D vs B] meta 判据级差 {len(ds)} 处 {ds or ''}")
if ds:
    raise SystemExit("  ⇒ 默认配置那一场与 0.80 那一场判据不同 ⇒ 回退不干净")
print("  ⇒ 通过：三张表 + 判据级字段一字未动")

print("\n===== 三、D vs C（0.85 那版）：这里**不该**相同 =====")
tables(D, CC, "D vs C", want_same=False)
print(f"  [各场自报] B 池内 {mb['buy']['n_extra_in_pool']} / 净新增 {mb['buy']['n_extra_net']}"
      f"　D 池内 {md['buy']['n_extra_in_pool']} / 净新增 {md['buy']['n_extra_net']}"
      f"　C 池内 {mc['buy']['n_extra_in_pool']} / 净新增 {mc['buy']['n_extra_net']}")
assert md["buy"]["n_extra_in_pool"] == mb["buy"]["n_extra_in_pool"] > 0, \
    "D 场命中数没复现 B 场（或为 0 ⇒ 闸被回退掉了）"
print("  ⇒ 通过：D 吃的是 0.80 那把刀，且与 0.85 那把切得出差别")
print("\n[读法] 生产回到 ㉟ 那一版的形态（刀口 0.80），唯一多出来的自由度是"
      "以后拨这根旋钮不会再连带收紧「不该买」那张域")
sys.exit(0)
