# -*- coding: utf-8 -*-
"""B6 那条「复牌末格」豁免到底有没有牙（09-29）

要防的事：B6 从「零差异」改成「放行一型」之后，判据很容易退化成「见到 change 就放」⇒ 恒真。
⇒ 造六格夹具，**只有一格该放行**（真·复牌末格，用 09-29 实测那两条真字节），
   五格必须判红：方向反过来 / 不是 change 字段 / 出不止一格 / 末格外还有别的字节不同 / 多缺文件。
差异行走真的 `diff -rq`（不手写行形），免得把 diff 的措辞猜错。
"""
import os
import shutil
import struct
import subprocess
import sys

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(REPO, "stock", "v1", "src"))
FIX = os.path.join(REPO, "stock", "v1", "temp", "tmp_b6_fixture_0929")
# 09-29 实测那一条真差异：起表前（生产 09-28 20:59 写的那份）末格 NaN、重跑填成 +2.32816%
REAL_NAN = os.path.join(REPO, "stock/v1/temp/tmp_b6_src_0929/prod_NAN.day.bin")
REAL_FLIP = os.path.join(REPO, "stock/v1/temp/tmp_b6_src_0929/live_FLIP.day.bin")
NAN = float("nan")


def cells(start_idx, vals):
    """本仓 `.day.bin` 的真实形状：整份都是 float32，[0] 是起始日历下标，其后逐日一格"""
    return struct.pack("<f", float(start_idx)) + b"".join(struct.pack("<f", float(v)) for v in vals)


def put(root, inst, field, body):
    d = os.path.join(root, "features", inst)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{field}.day.bin"), "wb") as fh:
        fh.write(body)


def build():
    shutil.rmtree(FIX, ignore_errors=True)
    live, prod = os.path.join(FIX, "live"), os.path.join(FIX, "prod")   # live=本场落盘, prod=起表前
    os.makedirs(live)
    os.makedirs(prod)

    # ① 真·复牌末格（真字节：起表前 NaN → 本场 +2.32816%，其余 15712 字节全同）⇒ 唯一该放行
    for root, src in ((prod, REAL_NAN), (live, REAL_FLIP)):
        put(root, "sz300096", "change", open(src, "rb").read())
    # ② 方向反过来（起表前是真实涨幅、本场变 NaN）＝链路把数据弄丢了 ⇒ 必须红
    put(prod, "sz000001", "change", cells(20000, [1.0, 2.0, 0.0134]))
    put(live, "sz000001", "change", cells(20000, [1.0, 2.0, NAN]))
    # ③ 差的是 close 字段，不是 change ⇒ 必须红
    put(prod, "sz000002", "close", cells(20000, [1.0, 2.0, 3.0]))
    put(live, "sz000002", "close", cells(20000, [1.0, 2.0, 4.0]))
    # ④ 出不止一格（倒数第二格也不同）⇒ 必须红
    put(prod, "sz000003", "change", cells(20000, [1.0, NAN, NAN]))
    put(live, "sz000003", "change", cells(20000, [1.0, 0.5, 0.0233]))
    # ⑤ 末格方向对、但末格外还差一格（尺寸相同、md5 不同）⇒ 必须红
    put(prod, "sz000004", "change", cells(20000, [1.0, 9.0, NAN]))
    put(live, "sz000004", "change", cells(20000, [1.0, 8.0, 0.0233]))
    # ⑥ 多出来的文件（diff 打成 `Only in ...`）⇒ 必须红
    put(live, "sz000006", "change", cells(20000, [1.0, 2.0, 0.0233]))
    return live, prod


def main():
    from run_ashare_rerun_chain import b6_judge
    live, prod = build()
    d = subprocess.run(["diff", "-rq", live, prod], stdout=subprocess.PIPE, text=True)
    lines = [ln for ln in d.stdout.splitlines() if ln.strip()]
    waived, held = b6_judge(lines, live, prod)
    print(f"diff -rq 原始差异 {len(lines)} 行")
    print(f"  放行 {len(waived)}：{'、'.join(waived) or '（无）'}")
    for ln in held:
        print("  判红  " + ln.replace(FIX, "")[:150])
    got = sorted({w.split()[0] for w in waived})
    ok = got == ["sz300096"] and len(held) == 5 and len(lines) == 6
    print(f"\n[夹具] 差异共 6 格 ⇒ 该放行 ['sz300096']、实得 {got}；该判红 5 格、实得 {len(held)}")
    print("[结论] " + ("✅ 这条豁免有牙（五格全红，只放真复牌那一型）" if ok
                    else "❌ 恒真/漏放 ⇒ 不许用，回去改 b6_judge"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
