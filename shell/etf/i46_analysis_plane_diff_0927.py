# -*- coding: utf-8 -*-
"""分析面补跑后的对表（09-27，任务 #46）。

要证的四件事，每件都写成**能失败**的断言：
  1. `equity / signals / trades` 三张表的末格日期 == 数据面镜像批末（日线日更已把
     行情推到 09-24，分析面若没跟上就会停在前一天）；
  2. 旧日期**一格不许丢**（结构性判据）。注意这里刻意**不**断言"历史逐值不动"——
     与日线数据面的追加口径不同，这个入口每次重挖因子，篮子换了整条净值就会重算
     （09-27 实测：上一场 3 条含 1 条外部因子、本场 4 条注册表模板，逐值相等的格子
     只有 4%）；所以同时把两场的**因子篮子**打出来，让"不可比"是看得见的；
  3. 因子库三条存储（json/csv/md）与 `trial_counter.json` 的变化要**报出来**，
     它们是全链跑批的副作用（库会被 `[factor-lib]` 自动提交、账本只增不减）；
  4. 看板顶格那条「日线净值止于」读的就是第 1 条那张表 —— 顺手用 AppTest 复核它
     在页面上真的是 09-24，别只信磁盘。

只读，不写任何产物。跑法：
    /usr/bin/python3.10 shell/etf/i46_analysis_plane_diff_0927.py
"""
import json
import os
import subprocess
import sys

import pandas as pd

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
RES = os.path.join(REPO, "etf/v1/data/results")
SNAP = os.path.join(REPO, "shell/etf/snap_analysis_0927/results")
MIRROR = os.path.join(REPO, "etf/v1/data/universe_all")
TABLES = ("equity_daily.csv", "signals_daily.csv", "trades_daily.csv")

fail = []


def mirror_batch_end():
    """镜像批末 = 全市场日线里最新的那一天（数据面自己报到哪儿）"""
    ends = []
    for f in os.listdir(MIRROR):
        if not f.endswith("_daily.csv"):
            continue
        with open(os.path.join(MIRROR, f), encoding="utf-8-sig") as fh:
            last = ""
            for line in fh:
                if line.strip():
                    last = line
        d = last.split(",")[0][:10]
        if len(d) == 10:
            ends.append(d)
    return max(ends)


def load(path):
    df = pd.read_csv(path)
    col = df.columns[0]
    if col not in ("date", "datetime"):
        raise AssertionError(f"{os.path.basename(path)} 首列不是日期：{col}")
    df[col] = pd.to_datetime(df[col])
    return df.set_index(col)


BATCH = mirror_batch_end()
print(f"[数据面] 镜像批末 = {BATCH}")

# ---------- 1 + 2：三张表末格 + 日期只增不减 ----------
# ⚠️ 这里**不拿"历史逐值不动"当判据**（09-27 实测推翻了自己先前的假设）：这个入口
#    每次跑都重新挖一遍因子（注册表 9 条按 |IC| 入池 + 多源追加 + 聚类去重），
#    上一场与这一场的**因子篮子可以不同** ⇒ 整条净值是重算的，逐值必动。
#    真正该断言的是两件结构性的事：日期一格不许丢、末格必须追到镜像批末。
for name in TABLES:
    new_p, old_p = os.path.join(RES, name), os.path.join(SNAP, name)
    if not os.path.exists(new_p):
        fail.append(f"{name} 不存在（跑批没产出？）")
        continue
    new, old = load(new_p), load(old_p)
    last = str(new.index[-1])[:10]
    print(f"[1] {name}: 快照末 {str(old.index[-1])[:10]} → 现末 {last}"
          f"｜{len(old)} 行 → {len(new)} 行")
    if last != BATCH:
        fail.append(f"{name} 末格是 {last}，不是镜像批末 {BATCH}")
    lost = old.index.difference(new.index)
    # 「旧日期不许丢」只对**逐日净值表**成立；signals/trades 是事件表（只在调仓 bar
    # 出行、且只记真发生换手的那一笔），篮子一换、哪些天有行自然跟着变 ⇒ 只报不判
    if len(lost):
        if name.startswith("equity"):
            fail.append(f"{name} 丢了 {len(lost)} 个旧日期（{str(lost[0])[:10]} 起）"
                        f"⇒ 净值是逐日全表，少日期就是少数据")
        else:
            print(f"     ↳ 事件表：{len(lost)} 个旧日期这一场没有行"
                  f"（篮子换了 ⇒ 那些天不再换手，不判失败）")
    shared = old.index.intersection(new.index)
    a = old.loc[shared].apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
    b = new.loc[shared].apply(pd.to_numeric, errors="coerce")[a.columns]
    if a.empty:
        print(f"[2] {name}: 重叠段没有可比数值列（只有代码/日期这类文字列）")
        continue
    diff = (a - b).abs()
    # 用 pandas 的 max（跳过 NaN）而不是 ndarray.max：signals 表里本来就有空格子，
    # 一边 NaN 一边有数会算出 NaN，直接 .values.max() 会把整段读数污染成 nan
    print(f"[2] {name}: 重叠 {len(shared)} 个旧日期、{a.shape[1]} 个数值列逐值对照 "
          f"最大绝对差 {float(diff.max().max()):.4g}、完全相等的格子 "
          f"{float((diff == 0).to_numpy().mean()):.1%}"
          f"（一边 NaN 一边有数按「不等」计；>0 即篮子或权重变了，见 [2b]）")

# 因子篮子：这一场与上一场是不是同一套
po = json.load(open(os.path.join(SNAP, "optimized_params_daily.json")))
pn = json.load(open(os.path.join(RES, "optimized_params_daily.json")))
same_set = set(po["factor_weights"]) == set(pn["factor_weights"])
print(f"\n[2b] 因子篮子 {po['n_factors']} 条 → {pn['n_factors']} 条，"
      f"{'同一套' if same_set else '不是同一套（两场数字不可比）'}")
if not same_set:
    only_old = sorted(set(po["factor_weights"]) - set(pn["factor_weights"]))
    only_new = sorted(set(pn["factor_weights"]) - set(po["factor_weights"]))
    print("     只在上一场: " + ("、".join(only_old) if only_old else "—"))
    print("     只在本场  : " + ("、".join(only_new) if only_new else "—"))
else:
    if not all(abs(po["factor_weights"][k] - pn["factor_weights"][k]) < 1e-9
               for k in po["factor_weights"]):
        fail.append("同一套因子但权重不同 ⇒ 净值整条会重算，得说明来源")

# ---------- 3：副作用清单 ----------
def md5(p):
    import hashlib
    return hashlib.md5(open(p, "rb").read()).hexdigest()[:10] if os.path.exists(p) else "缺"


print("\n[3] 跑批副作用（快照 md5 → 现 md5；标 * 的在快照里换了存放名）")
for rel in ("../library/factor_library.csv", "../library/factor_library.md",
            "../library/factor_library_index.json", "optimized_params_daily.json",
            "rdagent_output/factors.json", "dsr_daily.csv", "walk_forward_oos_daily.csv",
            "auto_remining_log.csv"):
    rp = os.path.normpath(os.path.join(RES, rel))
    sp = os.path.join(SNAP, rel)
    if not os.path.exists(sp):        # factors.json 在快照里叫 state/factors.json.bak
        sp = os.path.join(os.path.dirname(SNAP), "state",
                          os.path.basename(rel) + ".bak")
        rel += " *"
    tag = "变了" if md5(sp) != md5(rp) else "未变"
    print(f"    {rel:40s} {md5(sp)} → {md5(rp)}  {tag}")
tc = json.load(open(os.path.join(REPO, "etf/v1/data/cache/trial_counter.json")))
tc_old = json.load(open(os.path.join(REPO, "shell/etf/snap_analysis_0927/state/trial_counter.json")))
print(f"    trial_counter: {tc_old.get('count')} → {tc.get('count')}")
if tc.get("count", 0) < tc_old.get("count", 0):
    fail.append("试验账本 N 变小了：这条线是「只增不减」的持久账本，被 reset 会让 DSR 门槛塌回原样")
state = json.load(open(os.path.join(REPO, "etf/v1/data/cache/remining_state.json")))
d = state["daily"]
print(f"    重挖状态: count={d.get('remining_count')} last_remining_bar={d.get('last_remining_bar')}"
      f"（本场 bar=4064 ⇒ 距上次 {4064 - (d.get('last_remining_bar') or 0)} 个 bar）")
if d.get("remining_count") != json.load(
        open(os.path.join(REPO, "shell/etf/snap_analysis_0927/state/remining_state.json"))
)["daily"]["remining_count"]:
    fail.append("本场触发了自动重挖（预期被 cooldown_bars=100 挡住）⇒ 产物与上一版不可比")

# ---------- 4：看板上那一格 ----------
CHILD = r"""
import os, sys, json
sys.path.insert(0, "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src")
os.chdir("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src")
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(os.path.join(os.getcwd(), "app.py"), default_timeout=180).run()
got = {m.label: str(m.value) for m in at.main.metric}
print("@@@" + json.dumps({"exc": len(at.exception),
                          "止于": got.get("日线净值止于"),
                          "区间": got.get("区间"),
                          "落后": got.get("数据面最大落后")}, ensure_ascii=False))
"""
out = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True)
line = [l for l in out.stdout.splitlines() if l.startswith("@@@")]
if not line:
    fail.append("看板没跑起来：" + out.stderr.strip().splitlines()[-1][:160]
                if out.stderr.strip() else "看板没跑起来（无输出）")
else:
    r = json.loads(line[0][3:])
    print(f"\n[4] 看板：异常 {r['exc']} 条｜日线净值止于={r['止于']}｜区间={r['区间']}"
          f"｜数据面最大落后={r['落后']}")
    if r["exc"]:
        fail.append(f"看板整页 {r['exc']} 条异常")
    if r["止于"] != BATCH:
        fail.append(f"看板顶格读的是 {r['止于']}，不是镜像批末 {BATCH}")

print("\n" + ("[验收失败] " + "；".join(fail) if fail
              else f"[验收通过] 三张表末格都到 {BATCH}、旧日期一格没丢、看板顶格跟着变"
                   f"（净值逐值是否重算见上面 [2]/[2b] 的读数，不在判据里）"))
sys.exit(1 if fail else 0)
