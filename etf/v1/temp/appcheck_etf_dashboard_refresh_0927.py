# -*- coding: utf-8 -*-
"""验收 #4：ETF 看板是否真的「跟着盘上文件刷新」

跑法：`/usr/bin/python3.10 etf/v1/temp/appcheck_etf_dashboard_refresh_0927.py`

为什么这样测
------------
看板的读盘键换成了文件戳（mtime+size），所以要证明的不是"页面没报错"，而是
**盘上动一下、页面跟着变**，以及**盘上没动时页面不重读文件**。
在真实产物上做这件事等于改判据文件，所以整场测试跑在临时目录里：
`etf/v1/temp/tmp_refresh_0927/` 下把真 `data/` 每一项做成软链，只把要动的那几个文件
换成实体副本，再用 `ETF_DATA_DIR` / `ETF_REPORT_DIR` / `ETF_LOG_DIR` 三个环境变量
把看板引过来。真产物全程只被读、不被写。

四条判据（任何一条不过就 exit 1）
--------------------------------
A  改 `dsr_daily.csv` 里的 DSR → 页首「全样本 DSR」跟着变；原样放回 → 再变回去。
   （双向：只测一个方向的话，"缓存永不失效"也会碰巧通过第二次断言）
B  往一份日志副本尾部追加标记行 → 「🗂 日志与报告」读到的尾巴含该标记；
   且显示行数受"尾部 500 行"约束：不许因为文件有 9.6MB 就整读。
C  改 `report_daily.md` 的正文 → 报告那一格渲染出这句独家结论。
D  盘没动时整页再跑一次，实际调用 `pd.read_csv` 的次数不得增加（防"换了个
   键结果每次都 miss，等于没缓存"）。第一次渲染必须读到过 csv，否则这条恒真。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ETF = os.path.join(REPO, "etf", "v1")
SRC = os.path.join(ETF, "src")
REAL_DATA = os.path.join(ETF, "data")
TMP = os.path.join(HERE, "tmp_refresh_0927")
MARKER = "ETFCHECK_TAIL_MARKER_0927"
SENTENCE = "ETFCHECK 这是一句测试写进日报的独家结论"

fail = []


def link_tree(src_dir, dst_dir):
    """src_dir 整棵树做进 dst_dir：目录递归建、文件软链。"""
    os.makedirs(dst_dir, exist_ok=True)
    for name in sorted(os.listdir(src_dir)):
        s, d = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if os.path.isdir(s):
            link_tree(s, d)
        elif not os.path.lexists(d):
            os.symlink(s, d)


def real_copy(rel_path):
    """把 tmp 里那个软链换成实体副本（测试只动副本），返回副本路径。"""
    tmp_p = os.path.join(TMP, rel_path)
    real = os.path.realpath(tmp_p)
    os.unlink(tmp_p)
    shutil.copyfile(real, tmp_p)
    return tmp_p


def build_sandbox():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(TMP, "data"), exist_ok=True)
    for name in sorted(os.listdir(REAL_DATA)):
        s = os.path.join(REAL_DATA, name)
        if name in ("results", "cache") or not os.path.isdir(s):
            continue
        os.symlink(s, os.path.join(TMP, "data", name))
    link_tree(os.path.join(REAL_DATA, "results"), os.path.join(TMP, "data", "results"))
    link_tree(os.path.join(REAL_DATA, "cache"), os.path.join(TMP, "data", "cache"))
    link_tree(os.path.join(ETF, "report"), os.path.join(TMP, "report"))
    link_tree(os.path.join(ETF, "log"), os.path.join(TMP, "log"))


# 子进程头部：用 %r 插值（f-string 会把 emit 里的 {k} 当成外层变量，踩过一次）
CHILD_HEAD = """
import os, sys
SRC = %r
MARKER = %r
SENTENCE = %r
TMP = %r
os.environ["ETF_DATA_DIR"] = %r
os.environ["ETF_REPORT_DIR"] = %r
os.environ["ETF_LOG_DIR"] = %r
sys.path.insert(0, SRC)
from streamlit.testing.v1 import AppTest

def emit(**kw):
    for k, v in kw.items():
        print("@@@ " + k + " = " + str(v))

def dsr_metric(at):
    for m in at.main.metric:
        if m.label.startswith("全样本 DSR"):
            return m.value
    return None

def new_app():
    return AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=300)
""" % (SRC, MARKER, SENTENCE, TMP,
       os.path.join(TMP, "data"), os.path.join(TMP, "report"), os.path.join(TMP, "log"))


def run_case(body):
    """子进程里跑一段（每段都是全新解释器），返回 stdout+stderr。"""
    p = subprocess.run([sys.executable, "-c", CHILD_HEAD + body],
                       capture_output=True, text=True, cwd=SRC, timeout=1200)
    out = p.stdout + p.stderr
    if "@@@ " not in out and p.returncode != 0:
        raise AssertionError(f"子进程没打标记就退出了 (rc={p.returncode}):\n{out[-2500:]}")
    return out


def readings(out):
    got = {}
    for ln in out.splitlines():
        if ln.startswith("@@@ "):
            k, _, v = ln[4:].partition("=")
            got[k.strip()] = v.strip()
    return got


print("[0] 搭沙箱（真 data/ 全部软链，只把要动的文件换成副本）…")
build_sandbox()

# ---------- A + D ----------
dsr = real_copy("data/results/dsr_daily.csv")
with open(dsr, encoding="utf-8") as f:
    orig = f.read()
print(f"    副本：{os.path.relpath(dsr, HERE)}（{len(orig)} 字节）")

out = run_case(r"""
import pandas as pd
_real = pd.read_csv
MISS = {"n": 0}
def counting(*a, **k):
    MISS["n"] += 1
    return _real(*a, **k)
pd.read_csv = counting
at = new_app()
at.run()
n1, v1, e1 = MISS["n"], dsr_metric(at), len(at.exception)
at.run()                      # 盘没动：整页再跑一次
n2, v2 = MISS["n"], dsr_metric(at)
emit(rerun_exc=len(at.exception), v1=v1, v2=v2, n1=n1, n2=n2, exc=e1)
""")
r = readings(out)
print(f"[A] 首次渲染 全样本 DSR={r.get('v1')}；二次渲染（盘未动）={r.get('v2')}；异常 {r.get('exc')} 条")
print(f"[D] 实际调用 pd.read_csv：第一次整页 {r.get('n1')} 次 → 第二次 {r.get('n2')} 次")
if int(r.get("exc", 1)):
    fail.append(f"A：整页 {r['exc']} 条异常")
if r.get("v1") is None or r.get("v1") == "None":
    fail.append("A：页首没有「全样本 DSR」这一格（读数搬家了，测试要跟着改）")
if int(r.get("n1", 0)) == 0:
    fail.append("D：一次渲染没读过任何 csv ⇒ 计数没生效，这条断言是恒真的")
if r.get("v1") != r.get("v2"):
    fail.append(f"A：盘没动两次读数却不同（{r['v1']} vs {r['v2']}）")
if r.get("rerun_exc", "0") != "0":
    fail.append(f"D：第二次整页重跑出现 {r['rerun_exc']} 条异常")
if r.get("n1") != r.get("n2"):
    fail.append(f"D：盘没动却重读了 csv（{r['n1']} → {r['n2']}），文件戳失效没起作用"
                "（若 Streamlit 缓存在 AppTest 里本就不跨 run 保留，则这条要改成别的路子验）")

# 改值 → 页面必须跟上
cells = orig.splitlines()[0].split(",")
col = cells.index("dsr")
old_val = orig.splitlines()[1].split(",")[col]
new_val = f"{float(old_val) * 2:.10f}"
rows = orig.splitlines()
c2 = rows[1].split(",")
c2[col] = new_val
rows[1] = ",".join(c2)
with open(dsr, "w", encoding="utf-8") as f:
    f.write("\n".join(rows) + "\n")

out = run_case("at = new_app(); at.run(); emit(v=dsr_metric(at), exc=len(at.exception))")
r2 = readings(out)
print(f"[A] 把 dsr 从 {old_val} 改成 {new_val} → 页面读到 {r2.get('v')}")
if r2.get("v") != f"{float(new_val):.4f}":
    fail.append(f"A：改了盘上的 dsr，页面还是 {r2.get('v')}（期望 {float(new_val):.4f}）")

with open(dsr, "w", encoding="utf-8") as f:
    f.write(orig)
out = run_case("at = new_app(); at.run(); emit(v=dsr_metric(at), exc=len(at.exception))")
r3 = readings(out)
print(f"[A] 原样放回 → 页面读到 {r3.get('v')}")
if r3.get("v") != f"{float(old_val):.4f}":
    fail.append(f"A：改回去没跟上，页面 {r3.get('v')}（期望 {float(old_val):.4f}）")

# ---------- B：日志尾部 ----------
big = "research_daily_20260925.log"          # 9.6MB 那份
log_p = real_copy(f"log/{big}")
size0 = os.path.getsize(log_p)
with open(log_p, "a", encoding="utf-8") as f:
    f.write("\n" + MARKER + " 这行是测试追加的\n")

out = run_case(r"""
at = new_app()
at.run()
codes = [str(c.value) for c in at.code]
newest = max([os.path.join(os.environ["ETF_LOG_DIR"], p) for p in
              os.listdir(os.environ["ETF_LOG_DIR"]) if os.path.isfile(
                  os.path.join(os.environ["ETF_LOG_DIR"], p))], key=os.path.getmtime)
emit(exc=len(at.exception), newest=os.path.basename(newest),
     hit=any(MARKER in c for c in codes), longest=max([len(c.splitlines()) for c in codes] or [0]))
""")
rb = readings(out)
print(f"[B] 页面尾巴里有追加的标记行：{rb.get('hit')}；单块 code 最长 {rb.get('longest')} 行"
      f"（那份日志 {size0} 字节）；最新日志 = {rb.get('newest')}")
if rb.get("hit") != "True":
    fail.append("B：往日志副本追加了一行，页面尾巴里没有这行")
if int(rb.get("longest", 0)) > 3000:
    fail.append(f"B：页面显示了 {rb['longest']} 行 ⇒ 尾部回退读没生效（等于整读 9.6MB）")
if rb.get("newest") != big:
    fail.append(f"B：刚追加过的 {big} 没被认成最新日志（认成 {rb.get('newest')}）")
if rb.get("exc", "0") != "0":
    fail.append(f"B：整页 {rb['exc']} 条异常")

# ---------- C：报告正文 ----------
# 09-27 起真目录里不再有 `report_daily.md` 了（那份是模拟成交派生的日报，移进了
# `etf/v1/temp/snap_daily_0927/mock_removed/report/`）。C 要证的是"盘上正文变了、页面
# 下一拍就跟得上"，与原文件写的是什么无关 ⇒ 缺就现场造一份进沙箱。
rep_rel = "report/report_daily.md"
if os.path.lexists(os.path.join(TMP, rep_rel)):
    rep_p = real_copy(rep_rel)
else:
    rep_p = os.path.join(TMP, rep_rel)
    with open(rep_p, "w", encoding="utf-8") as f:
        f.write("# 日报夹具（本验收现场造的）\n")
with open(rep_p, encoding="utf-8") as f:
    rep_head = f.read().splitlines()[:6]
with open(rep_p, "w", encoding="utf-8") as f:
    f.write(SENTENCE + "\n\n" + "\n".join(rep_head) + "\n")

out = run_case(r"""
at = new_app()
at.run()
hit = any(SENTENCE in str(m.value) for m in at.main.markdown)
if not hit:                      # 报告格默认选中的不一定是这份 md：显式选一次再验
    sbs = [s for s in at.selectbox if s.label.startswith("选一份")]
    target = os.path.join(os.environ["ETF_REPORT_DIR"], "report_daily.md")
    cand = [s for s in sbs if any(str(o).endswith(".md") for o in s.options)]
    emit(n_md=len(cand))
    if cand:
        cand[0].set_value(target)
        at.run()
        hit = any(SENTENCE in str(m.value) for m in at.main.markdown)
emit(exc=len(at.exception), hit=hit)
""")
rc = readings(out)
print(f"[C] 改过的日报正文是否上屏：{rc.get('hit')}"
      + (f"（经显式选中，md 选项 {rc.get('n_md')} 个）" if rc.get("n_md") else ""))
if rc.get("hit") != "True":
    fail.append("C：改了 report_daily.md，页面两种路径（默认/显式选中）都没读到新正文")
if rc.get("exc", "0") != "0":
    fail.append(f"C：整页 {rc['exc']} 条异常")

print("\n沙箱留在 " + os.path.relpath(TMP, REPO) + "（除副本外全是软链，未写回真产物）")
print(("[验收失败] " + "；".join(fail)) if fail
      else "[验收通过] A/B/C/D 四条都过：盘上动一下页面跟着变，没动就不重读文件")
sys.exit(1 if fail else 0)
