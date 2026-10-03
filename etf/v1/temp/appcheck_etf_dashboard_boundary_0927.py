# -*- coding: utf-8 -*-
"""ETF 看板边界表述的无头验收（09-27 建，同日二版）。

二版的原因：09-27 把 `data/live/` 里那笔**模拟成交**（一行 `DRY_RUN` 单和从它派生
的滑点/延迟/日报）移走后，反馈闭环那节整块短路不再渲染 —— 原来那条"三个影子盘
标签必须在"的断言就从"验措辞"偷偷变成了"验盘上有数据"。所以拆成**双向**两跑：

  跑 1｜就地跑真目录（当前盘上已无模拟数据）
      a. 两页 0 异常（少了 json 不能把页面带崩）；
      b. 主 tab 有「📡 影子盘监控」、没有任何 tab 叫「实盘监控」；
      c. 页首 / 影子盘监控节两条 caption 的关键词逐条命中（这两条不依赖数据）；
      d. 全页 metric **0 个含「实盘」**，且**「影子盘*」也 0 个** —— 后者为 0 就是
         "那三个假数确实从盘上下来了"，它必须是 0，不是"无所谓"。
  跑 2｜子进程 + 夹具（`ETF_DATA_DIR` 指到 shell/ 下的沙箱，`data/live/` 是真目录，
      里面放一份**全 0 的** feedback_report.json；其余数据子目录符号链接回原处）
      有结构化读数时那节照样渲染，且标签仍是「影子盘滑点/延迟/夏普」3 个、
      含「实盘」0 个 —— 证明措辞改过这件事**不依赖**那批模拟数据还在。

只读真目录，不写任何产物（夹具只落在本脚本自己的 tmp 沙箱）。跑法：
    /usr/bin/python3.10 etf/v1/temp/appcheck_etf_dashboard_boundary_0927.py
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ETF = os.path.join(ROOT, "etf", "v1")
SRC = os.path.join(ETF, "src")
TMP = os.path.join(HERE, "tmp_boundary_0927")

# 页首那句 + 影子盘监控那节的来源说明，各自必须在一句话里说全（反馈闭环节那句
# 依赖 feedback_report.json 在不在，所以只在跑 2 里查）
NEED = {"页首边界句": ["不自动实盘", "完全不接下单", "手工录回", "不存储券商账号密码",
                      "只用公开/授权数据", "收盘后日线口径"],
        "影子盘监控节": ["不接下单", "不发委托", "没接进来"]}

sys.path.insert(0, SRC)
os.chdir(SRC)
from streamlit.testing.v1 import AppTest   # noqa: E402

fail = []


def scan(at):
    errs = [str(e.value) for e in at.exception]
    labels = [t.label for t in at.main.tabs]
    caps = [c.value for c in at.main.caption]
    mlabels = [m.label for m in at.main.metric]
    return {"异常": errs, "tab": labels, "caption": caps,
            "metric": mlabels}


def check_words(shots, tag, need, want_shipan, require_no_shipan):
    """caption 关键词逐节查 + metric 标签双向查。

    `require_no_shipan` 为真时"影子盘标签不许少"；为假时反过来要求它==`want_shipan`
    （跑 1 里期望 0：那三个假数已经下盘了）。"""
    for sec, words in need.items():
        hit = [i for i, t in enumerate(shots["caption"])
               if all(w in t for w in words)]
        if not hit:
            fail.append(f"[{tag}] 没有一条 caption 同时说到「{sec}」需要的 {words}")
        else:
            print(f"[{tag}][{sec}] 命中第 {hit[0]} 条："
                  + shots["caption"][hit[0]][:80] + "…")
    bad = [l for l in shots["metric"] if "实盘" in l]
    shp = [l for l in shots["metric"] if l.startswith("影子盘")]
    print(f"[{tag}][metric] 共 {len(shots['metric'])} 个，含「实盘」{len(bad)} 个，"
          f"「影子盘*」{len(shp)} 个 {shp}")
    if bad:
        fail.append(f"[{tag}] 还有 metric 标签写着「实盘」：{bad}")
    if require_no_shipan and len(shp) < 3:
        fail.append(f"[{tag}] 「影子盘滑点/延迟/夏普」只见到 {len(shp)} 个（期望 ≥3）")
    if not require_no_shipan and len(shp) != want_shipan:
        fail.append(f"[{tag}] 盘上已无模拟数据，「影子盘*」标签期望 "
                    f"{want_shipan} 个、实际 {len(shp)} 个")


# ---------- 跑 1：就地跑真目录 ----------
print("========== 跑 1：真目录（模拟数据已移走） ==========")
live_dir = os.path.join(ETF, "data", "live")
if os.path.exists(os.path.join(live_dir, "feedback_report.json")):
    fail.append("跑 1 前提不成立：data/live/feedback_report.json 还在，"
                "那这三个标签本来就该有")
pages = {}
for fname in ("app.py", "app_live.py"):
    at = AppTest.from_file(os.path.join(SRC, fname),
                           default_timeout=300).run()
    pages[fname] = scan(at)
    print(f"---- {fname}：caption {len(pages[fname]['caption'])} 条，"
          f"异常 {len(pages[fname]['异常'])} 条")
    for e in pages[fname]["异常"]:
        print("     " + e.splitlines()[0][:160])
    if pages[fname]["异常"]:
        fail.append(f"{fname} 抛异常")

main = pages["app.py"]
if "📡 影子盘监控" not in main["tab"]:
    fail.append("主 tab 里没有「📡 影子盘监控」")
if any("实盘监控" in l for l in main["tab"]):
    fail.append("还有 tab 叫「实盘监控」")
print("\n[tab 名单] " + " | ".join(main["tab"]))
check_words(main, "跑1", {k: v for k, v in NEED.items()
                          if k != "反馈闭环节"}, 0, False)

# ---------- 跑 2：子进程 + 全 0 夹具 ----------
print("\n========== 跑 2：沙箱 + 全 0 的 feedback_report.json ==========")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(os.path.join(TMP, "data", "live"))
for sub in sorted(os.listdir(os.path.join(ETF, "data"))):
    if sub != "live":
        os.symlink(os.path.join(ETF, "data", sub),
                   os.path.join(TMP, "data", sub))
FIXTURE = {"generated_at": "2026-09-27T00:00:00",
           "slippage": {"n_trades": 0, "avg_slippage": 0.0, "p95_slippage": 0.0,
                        "suggested_slippage_conservative": 0.0},
           "latency": {}, "turnover": {},
           "strategy": {"live_return": 0.0, "live_sharpe": 0.0},
           "factor": {"n_factors": 0, "n_keep": 0, "n_watch": 0, "n_retire": 0,
                      "retire_factors": []},
           "live_vs_backtest": {}}
with open(os.path.join(TMP, "data", "live", "feedback_report.json"),
          "w", encoding="utf-8") as f:
    json.dump(FIXTURE, f, ensure_ascii=False)

CHILD = """
import os, sys
SRC, TMP = sys.argv[1], sys.argv[2]
os.environ["ETF_DATA_DIR"] = os.path.join(TMP, "data")
sys.path.insert(0, SRC)
from streamlit.testing.v1 import AppTest
for fname in ("app.py", "app_live.py"):
    at = AppTest.from_file(os.path.join(SRC, fname), default_timeout=300).run()
    m = [x.label for x in at.main.metric]
    print("@@@%s\\t%d\\t%s\\t%s" % (
        fname, len(at.exception), json.dumps([l for l in m if "实盘" in l]),
        json.dumps([l for l in m if l.startswith("影子盘")])))
"""

r = subprocess.run([sys.executable, "-c", "import json\n" + CHILD, SRC, TMP],
                   capture_output=True, text=True)
got = {}
for ln in (r.stdout or "").splitlines():
    if ln.startswith("@@@"):
        fname, err, real, shipan = ln[3:].split("\t")
        got[fname] = {"异常": int(err), "metric": json.loads(real),
                      "影子盘": json.loads(shipan)}
if not got:
    fail.append("跑 2 子进程没出读数：" + (r.stderr or "")[-200:])
for fname, shots in got.items():
    print(f"---- {fname}：异常 {shots['异常']} 条，"
          f"影子盘标签 {shots['影子盘']}")
    if shots["异常"]:
        fail.append(f"[跑2] {fname} 抛异常")
    if shots["metric"]:
        fail.append(f"[跑2] {fname} 有写着「实盘」的标签：{shots['metric']}")
    if fname == "app.py":
        # 有结构化读数时那节会渲染：三个标签必须在位，一个都不许写「实盘」
        if len(shots["影子盘"]) < 3:
            fail.append(f"[跑2] 有夹具时「影子盘*」只见到 {len(shots['影子盘'])} 个："
                        f"{shots['影子盘']}")
        else:
            print(f"[跑2][反馈闭环节] 三个标签在位：{shots['影子盘']}")

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("[验收失败] " + "；".join(fail) if fail else
              "[验收通过] 假数已下盘（跑 1 影子盘标签 0 个、整页无异常）；"
              "有结构化读数时措辞仍是影子盘口径（跑 2 三个标签在位、含「实盘」0 个）"))
sys.exit(1 if fail else 0)
