# -*- coding: utf-8 -*-
"""选项F（日更链路改日历驱动）的验收：判据两个方向都真跑，外加「休市日晚上不动字节」的 e2e

为什么判据要两个方向都量（09-25 的教训，见 CHANGELOG ㉙ §二）：上一版那个「恒真闸」
就是因为只测了拒绝的那一边。这一轮同一个函数既要给出「该补」（否则链路推不动 bin，
就是被换掉的旧判据的病），也要给出「不该补」（否则休市日会把 ① 起起来撞闸）。

    M9  反事实：bin 末格 = 09-23、把今天当成 09-24 ⇒ advance=True 且那一格就是 09-24
        （旧判据在这里永远只会说「跳过」，bin 一格也推不动 —— 选项F 买回来的就是这一条）
    M10 bin 末格 = 09-24、今天 09-24 当天 ⇒ advance=False（已经补到最近一场，不重复起 ①）
    M11 真实的今晚：读真日历末格 + 真今天（09-25 中秋休市）⇒ advance=False、无 ⚠️
    M12 取不到日历的两条腿（接口抛异常 / 日历表落后）⇒ 不炸、返回 unknown 文案、advance=False
    M13 真跑一次 `run_ashare_daily_chain.py --dry-run`：整链在 ① 之前停、退出码 0，
        并且**一个字节都没动**（day.txt / daily_pv.h5 / 当日 meta 的 md5 与 mtime 全等，
        快照目录没有新文件）
    M14 下一场必须**跳过周末**（09-24 → 09-28），而且不能是 bin 里已有的日子

跑法（要联网取交易所日历，与 ① 同一张表）：
    cd stock/v1/src && /usr/bin/python3.10 ../temp/chain_session_unit_0925.py
"""
import hashlib
import os
import subprocess
import sys
import time

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                   "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

import pandas as pd                      # noqa: E402
import run_ashare_daily_chain as chain    # noqa: E402  (它会先跑 _bootstrap 把 config 包接上)
from config import ASHARE_SNAPSHOT_DIR    # noqa: E402

FAILS = []


def chk(tag, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + tag + ("　" + detail if detail else ""))
    if not cond:
        FAILS.append(tag)


def md5(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


print("== M9/M10/M11  该不该补 ①：三个读数（含两个方向 + 今晚真值） ==")
r9 = chain.decide_session("2026-09-23", today="2026-09-24")
chk("M9 反事实：末格 09-23、今天 09-24 ⇒ 该补，且补的就是 09-24",
    r9 == ("2026-09-24", True, ""), repr(r9))
r10 = chain.decide_session("2026-09-24", today="2026-09-24")
chk("M10 末格已经是 09-24 那天 ⇒ 不重复起 ①（下一场 09-28 还没发生）",
    r10 == ("2026-09-28", False, ""), repr(r10))
have = chain.calendar_end()
r11 = chain.decide_session(have)
chk(f"M11 今晚真值：真末格 {have} + 真今天 ⇒ 不该补（09-25 是中秋）、且没有 ⚠️",
    r11[1] is False and r11[2] == "" and pd.Timestamp(r11[0]) > pd.Timestamp.now().normalize(),
    repr(r11))

print("== M12  取不到日历：① 不起、后面三步照跑，而不是整链报错 ==")
mod = sys.modules["update_qlib_bin_daily"]
orig = mod.next_trade_day


def boom_net(*a, **k):
    raise RuntimeError("simulated akshare outage")


def boom_stale(*a, **k):
    raise SystemExit("日历里没有 2026-09-24 之后的交易日")


mod.next_trade_day = boom_net
a1 = chain.decide_session("2026-09-24")
mod.next_trade_day = boom_stale
a2 = chain.decide_session("2026-09-24")
mod.next_trade_day = orig
chk("M12a 接口抛异常 ⇒ 吞成 ⚠️ 文案，advance=False，不往上炸",
    a1[0] is None and a1[1] is False and "取不到" in a1[2], repr(a1))
chk("M12b 日历表落后（SystemExit）⇒ 同样吞成 ⚠️ 文案",
    a2[0] is None and a2[1] is False and "没有" in a2[2], repr(a2))
chk("M12c 两条腿都不许被当成「今天没有该更的一场」（unknown 必须非空）",
    bool(a1[2]) and bool(a2[2]), "⚠️ 文案非空")

print("== M13  真跑一次 --dry-run：整链停在 ① 之前，产物逐字节不变 ==")
snap_dir = ASHARE_SNAPSHOT_DIR


def fingerprint():
    return {
        "day_txt_md5": md5(chain.DAY_TXT),
        "day_txt_mtime": os.path.getmtime(chain.DAY_TXT),
        "h5_mtime": os.path.getmtime(chain.H5) if os.path.exists(chain.H5) else None,
        "meta_mtime": {f: os.path.getmtime(os.path.join(chain.ASHARE_SIGNAL_DIR, f))
                       for f in os.listdir(chain.ASHARE_SIGNAL_DIR) if f.startswith("meta_")},
        "snap_files": sorted(os.listdir(snap_dir)),
    }


before = fingerprint()
chk("M13前置 没有遗留的取消标记（有的话这一跑会停在 ① 之前，测不到要测的那一条）",
    not os.path.exists(chain.CANCEL_FLAG), chain.CANCEL_FLAG)
t0 = time.time()
p = subprocess.run(["/usr/bin/python3.10", os.path.join(SRC, "run_ashare_daily_chain.py"), "--dry-run"],
                   cwd=SRC, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                   encoding="utf-8", errors="replace")
dur = time.time() - t0
out = p.stdout or ""
print("  " + "\n  ".join(out.rstrip().splitlines()))
chk("M13a 退出码 0（休市日晚上点日更是**良性的停**，不是报错）",
    p.returncode == 0, f"rc={p.returncode}　用时 {dur:.1f}s")
chk("M13b 打印里认出「下一场 = 09-28」并明说 ① 跳过",
    "2026-09-28" in out and "[① 跳过]" in out)
chk("M13c --dry-run 且 ① 无事可做 ⇒ 明说停在这里，②③④ 没起",
    "[停在这里]" in out and "重生成 daily_pv.h5" not in out)
after = fingerprint()
chk("M13d 日历、面板、当日 meta、快照目录四类产物**一个字节都没动**（md5 + mtime 全等）",
    all(before[k] == after[k] for k in after),
    "　".join(f"{k}:{'=' if before[k]==after[k] else '≠'}" for k in after))

print("== M14  下一场要跳过周末、且不能是库里已有的日子 ==")
nxt = chain.pending_session("2026-09-24")
chk("M14 09-24（周四）的下一场 = 09-28（周一）", nxt == "2026-09-28", repr(nxt))
chk("M14b 给出的场次不早于 bin 末格（否则 ① 会被自己的幂等闸拒）",
    pd.Timestamp(nxt) > pd.Timestamp(have), f"{have} → {nxt}")

print(f"\n{'❌ ' + str(len(FAILS)) + ' 条不过：' + '；'.join(FAILS) if FAILS else '✅ 全部通过'}")
sys.exit(1 if FAILS else 0)
