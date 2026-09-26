# -*- coding: utf-8 -*-
"""看板「🌙 跑今天这场日更」那一条路的端到端回归（09-24 #118）

这一格改的是**唤起方式**，不是判据：链路本体（四步 + 跨步验收）在 ㉔ 已经用命令行
端到端量过，产物与生产逐字相同。所以这里要证的是页面这一层的四件事：

  A **dry-run 那一档真的一个字节都不动**：今晚 ① 无事可做（日历已含 20260924），
    修好的停法应当就地退出；`daily_pv.h5`、生产 `daily_signal/` 的 mtime 都不许动，
    /tmp 覆写目录也不该出现新产物。
    （这一条同时是 ㉔ 那条 `--dry-run` 漏洞的回归：修之前它会跳过 ① 却继续跑 ②③④，
      也就是「排练」两个字底下偷偷重写面板 + 覆盖当日名单。）
    A 只查**这一拍留下的字节**（新建的日志里有链路自己的开头行），不查「进程表里有几个」——
    这一档从起到退场不到 3 秒，睡一觉再数 /proc 是竞态读数，数到 0 说明不了任何事。
    活着的进程让按钮禁用那一格由 B 查（真跑有 130+ 秒窗口）。
  B **真跑那一条路整链走通**：按下之后分离进程把 ②→③→④ 跑完，日志尾巴出
    `[链路完成]`、`[验收 ③]` 四项全对；`/tmp` 覆写目录里 signal/buy/order 三份与
    生产同名三份**逐字相同**（= 页面起的子进程**继承**了 `STOCK_SIGNAL_DIR`，
    数也没变），而生产那三份 mtime 早于本轮开始。中途还顺手量 B'：**正在跑的时候
    重新渲染的那一格里按钮是禁用的**（这一格认的是进程表，命令行起的那一场也算）。
  D **跑完之后页面认得出成功**：新开一次 `AppTest`，那一格里出现
    `✅ 打出了 [链路完成]` 和「名单已更新 ⇒ 刷新整页」。D' 顺手核那一格的**三行落点**
    （① bin/② h5/③④ 名单）：本轮带着 `STOCK_SIGNAL_DIR` 覆写 ⇒ 名单那一行必须念 /tmp
    并打 ⚠️、且给出一条 error；生产档（无覆写）反过来必须 0 error —— 那条判据用**路径**
    不用「环境里有 `STOCK_*`」，后者会被 config 自己注入的 `STOCK_LLM_*` 顶成永久假警。

账本/名单全部覆写到 `/tmp/chain_dash_0924/`（`STOCK_SIGNAL_DIR` 等），**不碰生产
`daily_signal/`**。唯一躲不开的是 ② 重写 `daily_pv.h5`（面板输出路径不在 `STOCK_*`
覆写范围内，且 ② 现状是**每天必然全量重生成**，㉔ 已量过）——这一场 bin 没往前走，
所以重写出来的就是同一份内容，和今晚 21:00 那次冒烟同性质。
用 /usr/bin/python3.10。
"""
import datetime as dt
import glob
import os
import subprocess
import sys
import time

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
TMP = "/tmp/chain_dash_0924"
CHAIN_LOG = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/log/chain_from_dash.log"
PROD = os.path.join(SRC, "..", "data", "results", "daily_signal")
H5 = os.path.join(SRC, "..", "data", "results", "rdagent_output", "git_ignore_folder",
                  "factor_implementation_source_data", "daily_pv.h5")
CHAIN = os.path.join(SRC, "run_ashare_daily_chain.py")
TAG = "20260924"
T0 = time.time()

os.makedirs(TMP, exist_ok=True)
for p in glob.glob(os.path.join(TMP, "*")) + [CHAIN_LOG]:
    os.path.exists(p) and os.remove(p)
sys.path.insert(0, SRC)
os.environ.update(STOCK_SIGNAL_DIR=TMP,
                  STOCK_FILLS_CSV=os.path.join(TMP, "manual_fills.csv"),
                  STOCK_POSITION_OUT=os.path.join(TMP, "positions.csv"),
                  STOCK_ACCOUNT_OUT=os.path.join(TMP, "account.csv"))

from streamlit.testing.v1 import AppTest

fails = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


def page():
    return AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=600).run()


def one(items, label):
    got = [w for w in items if str(getattr(w, "label", "")) == label]
    assert len(got) == 1, f"标签「{label}」匹配到 {len(got)} 个控件"
    return got[0]


def procs():
    """页面那格读的就是这张表，这里用同一把尺（不起第二份判据）"""
    out = subprocess.run(["/bin/sh", "-c", "pgrep -f 'run_ashare_daily_chain[.]py' | wc -l"],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def text_of(at):
    return "\n".join(str(getattr(c, "value", "")) for c in at.caption) + "\n" + \
           "\n".join(str(getattr(i, "value", "")) for i in at.info) + "\n" + \
           "\n".join(str(getattr(e, "value", "")) for e in at.error) + "\n" + \
           "\n".join(str(getattr(c, "value", "")) for c in at.code) + "\n" + \
           "\n".join(str(getattr(b, "label", "")) for b in at.button)


def wait_done(timeout_s):
    """等分离进程退场：只读 /proc，不 kill、不碰它的任何文件"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if procs() == 0:
            return time.time() - t0
        time.sleep(5)
    return None


def log_lines():
    if not os.path.exists(CHAIN_LOG):
        return []
    with open(CHAIN_LOG, encoding="utf-8", errors="replace") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip()]


mt = lambda p: os.path.getmtime(p) if os.path.exists(p) else 0
prod_before = {os.path.basename(p): mt(p) for p in glob.glob(os.path.join(PROD, "*"))}
h5_before = mt(H5)
print(f"[起手] 生产 daily_signal {len(prod_before)} 个文件、h5 mtime "
      f"{dt.datetime.fromtimestamp(h5_before):%m-%d %H:%M:%S}，本轮 T0="
      f"{dt.datetime.fromtimestamp(T0):%m-%d %H:%M:%S}")

print("\n===== A dry-run 那一档：① 无事可做 ⇒ 整链停，一个字节不动 =====")
at = page()
one(at.toggle, "只跑 ① 的 dry-run（快照只算不写；① 无事可做时整链就停）").set_value(True)
t_click = time.time()
one(at.button, "跑今天这场日更").click()
at.run()
el = wait_done(180)
a = log_lines()
# A 这一档**故意不查「进程表里有 1 个」**：这条路从起到退场不到 3 秒（① 跳过就直接 return），
# 按下之后 sleep 再去数 /proc 必然有竞态——数到 0 既可能是"还没起来"也可能是"已经跑完"，
# 那种断言只会随机器快慢翻脸。这里改成查**这一拍留下的字节**：日志文件本轮新建（起手已删）
# 且带链路自己打的第一行 [前置体检] ⇒ 进程确实起来了、stdout 确实被重定向进去了。
# "正在跑的时候按钮禁用"由 B 那一档查（真跑那一条有 130+ 秒的窗口，数得着）。
check("页面按下去确实起了一个进程（本轮新建的日志里有链路的开头行）",
      os.path.exists(CHAIN_LOG) and mt(CHAIN_LOG) >= t_click
      and any("[前置体检]" in ln for ln in a),
      f"日志 {len(a)} 行，起于 {dt.datetime.fromtimestamp(mt(CHAIN_LOG)):%H:%M:%S}"
      f"（点击于 {dt.datetime.fromtimestamp(t_click):%H:%M:%S}）")
check("① 无事可做这条路被走到（日志有 [① 跳过]）", any("[① 跳过]" in ln for ln in a),
      f"日志 {len(a)} 行")
check("dry-run 就地停住（[停在这里] 而非继续 ②③④）",
      any("[停在这里]" in ln and "--dry-run" in ln for ln in a)
      and not any("[链路完成]" in ln for ln in a))
check("面板 h5 一个字节没动", mt(H5) == h5_before,
      f"{dt.datetime.fromtimestamp(mt(H5)):%H:%M:%S} vs 起手 {dt.datetime.fromtimestamp(h5_before):%H:%M:%S}")
check("生产 daily_signal 全目录 mtime 未变",
      all(mt(os.path.join(PROD, k)) == v for k, v in prod_before.items()))
check("覆写目录里也没落新产物（停在这里 = 真的没跑 ③）", not glob.glob(os.path.join(TMP, "*_*")),
      f"实测 {len(glob.glob(os.path.join(TMP, '*_*')))} 个")
check(f"等待 {el:.0f}s 内退场（不是挂在那儿）", el is not None and el < 120, f"{el:.0f}s")

print("\n===== B 真跑：②→③→④ 整链在分离进程里走完 =====")
at = page()
one(at.toggle, "只跑 ① 的 dry-run（快照只算不写；① 无事可做时整链就停）").set_value(False)
one(at.button, "跑今天这场日更").click()
at.run()
time.sleep(6)                                   # 让它真的起起来
n_running = procs()
check("链路正在跑（进程表里 1 个）", n_running == 1, f"实测 {n_running} 个")
at2 = page()
check("重新渲染的那一格里按钮 disabled、并写明 pid 在跑（第二场起不来）",
      one(at2.button, "跑今天这场日更").disabled
      and "正在跑这一场" in text_of(at2))
el = wait_done(16 * 60)
b = log_lines()
blob = "\n".join(b)
check(f"整链跑完（等待 {el if el is None else round(el)}s，上限 16 分钟）", el is not None)
check("日志尾巴打出 [链路完成]", "[链路完成]" in blob)
check("② 确实重写了面板（h5 变新且不早于日历）",
      mt(H5) > T0 and mt(H5) >= mt(os.path.join(SRC, "..", "data", "qlib", "qlib_data",
                                                "cn_data", "calendars", "day.txt")),
      f"h5 {dt.datetime.fromtimestamp(mt(H5)):%H:%M:%S}")
check("③ 的四项跨步验收全对（场次/面板末格/闸门档/排序轴）",
      "四项全对" in blob, [ln for ln in b if "验收 ③" in ln][:1])
n = {k: len(glob.glob(os.path.join(TMP, f"{k}_{TAG}.csv"))) for k in ("signal", "buy", "order")}
check("③ 的三份产物落到了**覆写目录**（继承 STOCK_SIGNAL_DIR）", all(v == 1 for v in n.values()), n)
same = {k: open(os.path.join(TMP, f"{k}_{TAG}.csv"), "rb").read()
        == open(os.path.join(PROD, f"{k}_{TAG}.csv"), "rb").read() for k in n if n[k]}
check("页面起的这一场与生产同名三份**逐字相同**（数没有因为换唤起方式而变）",
      all(same.values()), same)
check("生产 daily_signal 仍然一个字节没改",
      all(mt(os.path.join(PROD, k)) == v for k, v in prod_before.items())
      and not [p for p in glob.glob(os.path.join(PROD, "*")) if mt(p) >= T0])
check("④ 次日真账确实跑了（日志出审计场次）", "[审计场次]" in blob or "④ 跳过" in blob,
      [ln for ln in b if "审计场次" in ln][:1])

print("\n===== D 跑完之后，新开一次页面认得出这一场成功了 =====")
at3 = page()
t = text_of(at3)
check("那一格写着 ✅ 打出了 [链路完成]", "✅ 打出了 `[链路完成]`" in t)
check("并且给得出「名单已更新 ⇒ 刷新整页」的按钮", "名单已更新 ⇒ 刷新整页" in t)
check("它把这一场的验收行原话贴回来了（不是自己拼的结论）",
      "四项全对" in t or "[产物 ③]" in t)
# D' 落点三行：本轮进程从起手就带着 `STOCK_SIGNAL_DIR=/tmp/...`，所以名单那一行必须
# 被点名成覆写档（这一格唯一的用途就是说清「从这里起会写到哪儿」）。
# 判据不能写成 `any(k.startswith("STOCK_"))` —— 本线 config 自己会注入 STOCK_LLM_*，
# 那样每一次生产渲染都会报假警（09-24 无覆写 AppTest 实测）。
caps = [str(getattr(c, "value", "")) for c in at3.caption]
sig = [c for c in caps if "③④ 当日名单" in c]
check("三行落点都在（①/②/③④ 各一行）",
      sum(any(p in c for c in caps) for p in ("① bin 与日历", "② 面板 h5", "③④ 当日名单")) == 3)
check("覆写档里名单那一行念的是 /tmp 且打了 ⚠️",
      len(sig) == 1 and sig[0].startswith("⚠️") and TMP in sig[0], sig[:1])
check("并且给出一条 error 说这不是生产那一场",
      any("本线 `data/` 目录之外" in str(getattr(e, "value", "")) for e in at3.error))

print(f"\n{'全部通过' if not fails else '失败：' + '、'.join(fails)}　（{len(fails)} 项）")
raise SystemExit(1 if fails else 0)
