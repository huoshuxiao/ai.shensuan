# -*- coding: utf-8 -*-
"""㊹ 退闸落地验账（09-27）：两场 ③ 的**双向**对表，判据不许恒真。

跑法：先 `bash stock/v1/temp/buy_extra_revert_0927.sh`（≈2×75s），再本脚本（秒级）。

E = 新默认（`STOCK_BUY_EXTRA_RULES` 不传 ⇒ config 的 `""` 生效）
F = 显式 `low0`（回到 ㉟ 那一版）
参照 = ㊴/㊵ 那批留在 `tmp_buy_extra_knob_0926/` 的 a_off（关闸、刀口显式 0.85）与
      b_q080（开闸、刀口 0.80），外加**生产归档** `daily_signal/`（09-24 那场是关闸出的）

四条判据：
  ① E 的三张 CSV == a_off == 生产归档（逐字节）      ⇒ 新默认真的等于「关掉这道闸」
  ② F 的三张 CSV == b_q080（逐字节）+ meta 全等      ⇒ 面板没动过，且 `low0` 这条路零代码就回来
  ③ E 与 F 的 `buy_*.csv` **必须不同**               ⇒ 否则这场压根没吃到开关（判据 ① 就成了空话）
  ④ E 与 a_off 的 meta 差集 **只允许** `buy_extra_quantile` 一格（0.85→0.80，㊴ 那场是显式传的）
     ⇒ 差出第二格就说明退闸顺带动了别的判据

⚠️ meta 里有一格**不是判据**：`.industry.age_days` = 行业映射表距今几天（`ashare_screen.py:233`
   拿 `time.time()` 减文件 mtime，round 到 0.1）。跨日期对表它**必然**变，而且是唯一必然变的
   一格 ⇒ 单独放行，并要求它只能**变大**（表只会变老；变小 = mtime 被动过，那是另一件事）。
"""
import hashlib
import json
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
OUT = os.path.join(ROOT, "stock/v1/temp/tmp_buy_extra_revert_0927")
REF = os.path.join(ROOT, "stock/v1/temp/tmp_buy_extra_knob_0926")
PROD = os.path.join(ROOT, "stock/v1/data/results/daily_signal")
DAY = "20260924"
CSVS = [f"signal_{DAY}.csv", f"buy_{DAY}.csv", f"order_{DAY}.csv"]
META = f"meta_{DAY}.json"
# 跨日期对表**必然**变的那一格（墙钟减文件 mtime，见模块 docstring），不是判据
CLOCK = {".industry.age_days"}


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def flat(d, p=""):
    """meta 摊平成 {点路径: 标量}，list 带下标；用来数「到底差了几格」"""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{p}.{k}"))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(flat(v, f"{p}[{i}]"))
    else:
        out[p] = d
    return out


def diff_meta(a, b):
    fa, fb = flat(a), flat(b)
    bad = {}
    for k in sorted(set(fa) | set(fb)):
        if fa.get(k, "<缺>") != fb.get(k, "<缺>"):
            bad[k] = (fa.get(k, "<缺>"), fb.get(k, "<缺>"))
    return bad


def main():
    fails = []
    for tag in ("e_off", "f_low0"):
        for f in CSVS + [META]:
            if not os.path.exists(os.path.join(OUT, tag, f)):
                fails.append(f"缺产物 {tag}/{f} ⇒ 驱动没跑完，先看 {tag}.log")
    if fails:
        raise SystemExit("\n".join(fails))

    print("===== ① E（新默认）vs a_off vs 生产归档：三张 CSV 逐字节 =====")
    for f in CSVS:
        e, a, p = md5(f"{OUT}/e_off/{f}"), md5(f"{REF}/a_off/{f}"), md5(f"{PROD}/{f}")
        ok = e == a == p
        print(f"  {f:<24} E={e[:12]}｜a_off={a[:12]}｜归档={p[:12]} ⇒ {'相同' if ok else '**不同**'}")
        if not ok:
            fails.append(f"① {f}：新默认没等于关闸（E/a_off/归档 三者不一致）")

    print("\n===== ② F（显式 low0）vs b_q080：三张 CSV 逐字节 + meta 全等 =====")
    for f in CSVS:
        x, y = md5(f"{OUT}/f_low0/{f}"), md5(f"{REF}/b_q080/{f}")
        print(f"  {f:<24} F={x[:12]}｜b_q080={y[:12]} ⇒ {'相同' if x == y else '**不同**'}")
        if x != y:
            fails.append(f"② {f}：面板或判据在 ㉟ 之后动过，`low0` 回不去了")
    dm = diff_meta(json.load(open(f"{OUT}/f_low0/{META}", encoding="utf-8")),
                   json.load(open(f"{REF}/b_q080/{META}", encoding="utf-8")))
    print(f"  meta 差集：{len(dm)} 格" + (f" ⇒ {dm}" if dm else " ⇒ F 与 b_q080 连自报读数都逐位相同"))
    if set(dm) - CLOCK:
        fails.append(f"② meta 差出判据级 {sorted(set(dm) - CLOCK)}："
                     f"{ {k: v for k, v in dm.items() if k not in CLOCK} }")
    if dm and set(dm) <= CLOCK:
        print(f"  （差的全是墙钟那一格 {sorted(CLOCK)} ⇒ 放行，但③会验它只会变大）")

    print("\n===== ③ 正对照：E 与 F 的名单表**必须**不同 =====")
    e, x = md5(f"{OUT}/e_off/buy_{DAY}.csv"), md5(f"{OUT}/f_low0/buy_{DAY}.csv")
    print(f"  buy_ E={e[:12]}｜F={x[:12]} ⇒ {'不同（这道闸确实在改名单）' if e != x else '**相同 ⇒ 开关没牙，①是空话**'}")
    if e == x:
        fails.append("③ E 与 F 的 buy_ 表逐字节相同 ⇒ 开关根本没生效，前两条读数一律作废")

    print("\n===== ④ E vs a_off 的 meta：差集只许 buy_extra_quantile + 墙钟一格 =====")
    dm = diff_meta(json.load(open(f"{OUT}/e_off/{META}", encoding="utf-8")),
                   json.load(open(f"{REF}/a_off/{META}", encoding="utf-8")))
    allow = {".buy_extra_quantile"} | CLOCK
    for k, v in dm.items():
        print(f"  {k}: E={v[0]}｜a_off={v[1]}")
    if set(dm) != allow:
        fails.append(f"④ meta 差集 = {sorted(dm)}，期望 {sorted(allow)}"
                     f"（a_off 那场是**显式传 0.85**，新默认自报 0.80；墙钟那格只会自己变）")
    else:
        for k in dm:
            new, old = dm[k]
            if k in CLOCK and not float(new) >= float(old):
                fails.append(f"④ 墙钟那格 {k} 反而变小（{old}→{new}）⇒ 行业表 mtime 被动过，先看这个")
        print(f"  只差 {sorted(allow)} 两格（墙钟那格 {dm['.industry.age_days'][0]}≥"
              f"{dm['.industry.age_days'][1]} 只会变老）⇒ 退闸没有顺带动了别的判据")

    print("\n===== 附：本场自报的闸门读数（E = 关、F = 开）=====")
    for tag in ("e_off", "f_low0"):
        m = json.load(open(os.path.join(OUT, tag, META), encoding="utf-8"))
        bs = m["buy"]
        n_list = sum(1 for _ in open(f"{OUT}/{tag}/buy_{DAY}.csv", encoding="utf-8")) - 1
        print(f"  {tag}: rules={[r['key'] for r in m['buy_extra_rules']]} "
              f"刀口={m['buy_extra_quantile']} 池内命中={bs.get('n_extra_in_pool')} "
              f"净新增={bs.get('n_extra_net')} fired={bs.get('buy_extra_fired')} "
              f"名单={n_list} 只")
    print("\n" + "=" * 60)
    if fails:
        for s in fails:
            print("✗", s)
        raise SystemExit(1)
    print("⇒ 四条判据全过：新默认 = 关掉这道闸，且 `low0` 零代码回到 ㉟ 那一版")


if __name__ == "__main__":
    main()
