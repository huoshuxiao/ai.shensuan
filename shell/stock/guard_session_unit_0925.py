# -*- coding: utf-8 -*-
"""09-25 判据验收：bin append 这一层不落「还没发生的那一场」的数据

背景（当天真实踩到的）：09-25 是中秋休市，日历末格就是最近一场（09-24），但默认档
的待补场次取自交易日历 ⇒ 算出的是下一场 09-28。老版本会在**抓数之后**才对账，
于是接口里那份 09-24 的行情被钉成 `spot_20260928.csv` 落进缓存；它的收盘行占比是
100%，过得了复用判据 ⇒ 下一场真日更拿 09-24 的行情去 append 09-28。

新判据分两层，这个脚本按层验收，每层都带**正反对照**（断言不许恒真）：
    第一层 `guard_session`（抓数之前拒）        → M1~M4
    第二层 缓存复用处对表（相信缓存之前再对一场） → M5~M7
    落盘面：被拒的那一场不许留任何字节           → M6
    回归：阈值/判据抽成单点常量后，写入闸的读数   → M8（拿 09-24 那场真数据只读重算）

跑法（仓库根，需要外网：交易日历 + 一次全市场快照，实测全程 ~40s）
    /usr/bin/python3.10 shell/stock/guard_session_unit_0925.py
退出码 0 = 全部通过；非 0 = 有 FAIL（末尾列出哪几条）。
"""
import contextlib
import hashlib
import io
import os
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC = os.path.join(REPO, "stock", "v1", "src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, "data"))
import _bootstrap                                     # noqa: F401,E402
import update_qlib_bin_daily as m                      # noqa: E402
import pandas as pd                                    # noqa: E402
from config import ASHARE_SNAPSHOT_DIR, RDAGENT_QLIB_PROVIDER  # noqa: E402

ENTRY = os.path.join(SRC, "data", "update_qlib_bin_daily.py")
SCRATCH = os.path.join(REPO, "shell", "stock", "reuse_probe_0925")
P310 = "/usr/bin/python3.10"
HOLIDAY, NEXT_SESSION = "2026-09-25", "2026-09-28"

results = []


def check(label, cond, detail):
    results.append((bool(cond), label, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}　{detail}")


def expect_exit(fn, *a, **kw):
    """跑 fn，返回 (SystemExit 的 message, 是否真的抛了)"""
    try:
        fn(*a, **kw)
        return "", False
    except SystemExit as e:
        return str(e), True


def md5(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def main():
    cal = m.read_calendar(RDAGENT_QLIB_PROVIDER)
    print(f"bin 根 {RDAGENT_QLIB_PROVIDER}\n日历 {cal[0]} ~ {cal[-1]}（{len(cal)} 格）")

    print("\nM1 下一场的算法：日历末格 09-24 的下一场就是 09-28（09-25~27 不交易）")
    got = m.next_trade_day(cal[-1])
    check("M1", got == NEXT_SESSION, f"next_trade_day({cal[-1]}) = {got}，期望 {NEXT_SESSION}")

    print("\nM2 第一道闸·反：待补场次落在未来 ⇒ 拒，且明说那一场还没发生")
    msg, raised = expect_exit(m.guard_session, NEXT_SESSION, False)
    check("M2", raised and "晚于今天" in msg, f"raised={raised} 消息片段={msg[:28]!r}")

    print("\nM3 第一道闸·反：手敲 --session 指到休市日 ⇒ 拒（那天根本不交易）")
    msg, raised = expect_exit(m.guard_session, HOLIDAY, True)
    check("M3", raised and "不在交易所交易日历" in msg,
          f"raised={raised} 消息片段={msg[:34]!r}")

    print("\nM4 第一道闸·正对照：真交易日的场次必须放行，否则闸是恒真")
    msg, raised = expect_exit(m.guard_session, cal[-1], True)
    check("M4", not raised, f"guard_session({cal[-1]}, explicit=True) raised={raised} {msg[:30]!r}")

    print("\nM5 第二道闸·抽样对表两个方向（写入闸同一把尺子，只是抽样）")
    df_last = pd.read_csv(os.path.join(ASHARE_SNAPSHOT_DIR, "spot_20260924.csv"),
                          encoding="utf-8-sig")
    df_prev = pd.read_csv(os.path.join(ASHARE_SNAPSHOT_DIR, "spot_20260923.csv"),
                          encoding="utf-8-sig")
    t0 = time.time()
    e_ok, w_ok, n_ok = m.align_against_bin(RDAGENT_QLIB_PROVIDER, df_last, len(cal) - 2)
    ms = (time.time() - t0) * 1000
    e_no, w_no, n_no = m.align_against_bin(RDAGENT_QLIB_PROVIDER, df_prev, len(cal) - 1)
    # 正向：09-24 那份快照对 09-23 那格（就是当天真写进 bin 时闸口过的方向）
    check("M5a 真缓存过表",
          n_ok >= 300 and e_ok >= m.ALIGN_EXACT_MIN and w_ok >= m.ALIGN_MIN,
          f"逐字 {e_ok:.2%} / <{m.ALIGN_TOL:g} {w_ok:.2%}，抽样 {n_ok} 只"
          f"（门槛 {m.ALIGN_EXACT_MIN:.0%}/{m.ALIGN_MIN:.0%}，{ms:.0f}ms）")
    # 反向：拿 09-23 的行情去当 09-28 这一场 ⇒ 必须不过（这正是今晚那种脏缓存）
    check("M5b 错场缓存不过表", w_no < m.ALIGN_MIN,
          f"逐字 {e_no:.2%} / <{m.ALIGN_TOL:g} {w_no:.2%}，抽样 {n_no} 只 ⇒ 被拒 ✅")

    print("\nM6 落盘面：被拒的两条 CLI 路径各退出非零，且一个字节都不留")
    day_txt = os.path.join(RDAGENT_QLIB_PROVIDER, "calendars", "day.txt")
    before = (os.path.getmtime(day_txt), md5(day_txt))
    phantom = os.path.join(ASHARE_SNAPSHOT_DIR, f"spot_{NEXT_SESSION.replace('-', '')}.csv")
    holiday_cache = os.path.join(ASHARE_SNAPSHOT_DIR, f"spot_{HOLIDAY.replace('-', '')}.csv")
    for label, cmd in [
            ("默认档 --dry-run", [P310, ENTRY, "--dry-run"]),
            ("--session 休市日", [P310, ENTRY, "--session", HOLIDAY])]:
        r = subprocess.run(cmd, cwd=SRC, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        check(f"M6 {label} 退出非零", r.returncode != 0,
              f"exit={r.returncode}　末行={r.stdout.strip().splitlines()[-1][:56]!r}")
    check("M6 没落未来场次的缓存", not os.path.exists(phantom), f"{phantom} 存在={os.path.exists(phantom)}")
    check("M6 没落休市日的缓存", not os.path.exists(holiday_cache),
          f"{holiday_cache} 存在={os.path.exists(holiday_cache)}")
    after = (os.path.getmtime(day_txt), md5(day_txt))
    check("M6 日历 day.txt 未动", before[1] == after[1] and before[0] == after[0],
          f"md5 {before[1][:8]}→{after[1][:8]}　mtime 变={after[0] != before[0]}")

    print("\nM7 复用处端到端：目录里已经躺着脏缓存时，不再相信它、重取一份")
    os.makedirs(SCRATCH, exist_ok=True)
    dirty = os.path.join(SCRATCH, f"spot_{NEXT_SESSION.replace('-', '')}.csv")
    shutil_copy(df_last, dirty)
    md5_before, mt_before = md5(dirty), os.path.getmtime(dirty)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        df = m.fetch_spot(NEXT_SESSION, SCRATCH, RDAGENT_QLIB_PROVIDER)
    out = buf.getvalue()
    check("M7a 脏缓存被对表拒掉", "过不了对表" in out,
          f"取到 {len(df)} 行；输出首行={out.splitlines()[0][:60]!r}")
    check("M7b 真的重取了", "抓到快照" in out and "用缓存快照" not in out,
          f"含『抓到快照』={'抓到快照' in out}　含『用缓存快照』={'用缓存快照' in out}")
    check("M7c 脏缓存被新快照覆写", md5(dirty) != md5_before and os.path.getmtime(dirty) > mt_before,
          f"md5 {md5_before[:8]}→{md5(dirty)[:8]}")
    check("M7d 生产快照目录没被这次排练污染", not os.path.exists(phantom),
          f"{os.path.basename(phantom)} 存在={os.path.exists(phantom)}")

    print("\nM8 写入闸回归：把 09-24 那场真日更的判断**只读**重算一遍（阈值改成单点常量后，读数不许变）")
    idx_0924 = len(cal) - 1                      # 09-24 在日历里的下标（本场就是它）
    try:
        plan, rep = m.build_plan(RDAGENT_QLIB_PROVIDER, cal[idx_0924], df_last, idx_0924)
        both = rep.dropna(subset=["前收_bin", "昨收(除权参考价)"])
        e_w, w_w, n_w = m.align_rates(both["昨收(除权参考价)"], both["前收_bin"])
        check("M8 全量写入闸仍过（且与抽样对表同向）",
              len(plan) > 4000 and e_w >= m.ALIGN_EXACT_MIN and w_w >= m.ALIGN_MIN
              and abs(e_w - e_ok) < 0.05,
              f"待写 {len(plan)} 只／全量 {n_w} 只：逐字 {e_w:.2%} / <{m.ALIGN_TOL:g} {w_w:.2%}，"
              f"与抽样 {e_ok:.2%} 差 {abs(e_w - e_ok) * 100:.1f}pp")
    except SystemExit as e:
        check("M8 全量写入闸仍过（且与抽样对表同向）", False, f"抛了 SystemExit：{str(e)[:80]}")

    bad = [r for r in results if not r[0]]
    print(f"\n===== {len(results) - len(bad)}/{len(results)} 条通过 =====")
    for _, label, detail in bad:
        print(f"FAIL {label}　{detail}")
    return 1 if bad else 0


def shutil_copy(df, path):
    df.to_csv(path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    sys.exit(main())
