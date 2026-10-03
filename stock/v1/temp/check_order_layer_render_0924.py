"""复验股票线看板：口径表措辞被 f-string 求值 + 下单层真的渲染出名单。用 python3.10。

两类断言分开，别混在一起写死：
  ①**静态针**——与「生产上躺着哪一天的名单」无关，只验措辞有没有被求值
    （分档限幅、下单层、归档档位、㉑ 的排序轴口径行）；
  ②**动态断言**——凡是指向「今天这份名单的内容/新旧轴」的东西一律**从文件读出来再判**，
    不写死代码与票名。Why：09-24 那版里我写死过两枚日期相关的针（`迦南科技` 与
    「本页名单由 `ts_std(volume,20)`」），当晚日更出新名单后一枚变假阴性、一枚极性
    反转 —— 写死日期本身就是这个脚本最容易坏的地方。
"""
import glob
import json
import os
import re
import pandas as pd
import sys

from streamlit.testing.v1 import AppTest

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/src"
sys.path.insert(0, SRC)
import _bootstrap  # noqa: F401  裸模块名导入的前提
from config import ASHARE_BUY_TOP_N, ASHARE_SIGNAL_DIR  # noqa: E402
from ashare_screen import BUY_EXPR             # noqa: E402

APP = os.path.join(SRC, "app.py")
at = AppTest.from_file(APP, default_timeout=300)
at.run()
print("exceptions:", len(at.exception))
for e in at.exception:
    print("  !!", e.value)

blob = []
# subheader / title 也在独立集合里，漏了就会把「🧾 下单名单」判成没渲染（假阴性）
for attr in ("markdown", "caption", "text", "info", "warning", "error",
             "title", "header", "subheader"):
    for el in getattr(at, attr, []):
        blob.append(str(getattr(el, "value", "")))
for d in at.dataframe:
    blob.append(str(d.value))
all_text = "\n".join(blob)

def latest_tag(kind):
    """目录里最新一份 `kind_YYYYMMDD.csv` 的日期串——不写死日期，防假阴性。"""
    ps = glob.glob(os.path.join(ASHARE_SIGNAL_DIR, f"{kind}_[0-9]*.csv"))
    if not ps:
        raise SystemExit(f"[无产物] {ASHARE_SIGNAL_DIR} 下没有 {kind}_*.csv")
    return max(os.path.basename(p).split("_")[1][:8] for p in ps)


NEEDLES = [
    "🧾 下单名单",
    "同板块 ≤ 3",          # 口径表下单层
    "主板 9.5%",           # 分档限幅求值后的样子
    "北交所 28.5%",
    "81~85%",
    "家电行业",
    "当前生效档：`board`",  # 口径表自报进程里的实际档位（防「文档说 board、代码是 flat」）
    # dated 档那两行落地：生效日表 + 「差异只在创业板 2020-08-24 之前」的实测范围
    "创业板 19.0%（2020-08-24 起，之前 9.5%）",
    "837/1449",
    # 09-24 落 board 基线：这句里的 `board` 是从归档 CSV 的 gate 列**读出来**的
    # （app.archived_gate()），所以它同时验了「文件确实是 board 档」和「看板不写死」
    "表内 `gate` 列当前 = `board`",
    "归档基线三张表",
    # ㉑ 换轴：这三条都必须是 `{BUY_EXPR}` 求值后的样子，不是写死的 STD/SMA
    "待买入域内 `ts_mean(volume,20)`",       # 口径表「待买入排序轴」那一行
    "09-24 换过轴",                          # 同一行自报换过，旧轴点名
    # ㉘ 换名单形状后这笔钱重量的那一行（旧针「现轴口径：+1.39% → -3.98%」是全局档的数，
    # 09-25 起页面上已经是配额档那一套 ⇒ 针跟着换，别拿旧账单当新口径）
    "现轴现名单口径：+0.11% → -2.79%",        # 剔除页脚注里并集压在名单上的钱
    # 时效那一条状态栏在 09-24 日更后必须报今日信号日（不写死日期，从目录反推）
    "-".join([latest_tag("buy")[:4], latest_tag("buy")[4:6],
              latest_tag("buy")[6:]]),   # 时效栏报的是这一场信号日（页面用带横杠的 ISO 格式）
]
bad = [n for n in NEEDLES if n not in all_text]
print(f"静态针 {len(NEEDLES)} 枚：", "全中" if not bad else f"未命中 {bad}")

# ---- 动态断言①：下单名单那 5 只，代码必须逐个出现在页面上（从最新 order CSV 读）----
tag = latest_tag("order")
od = pd.read_csv(
    os.path.join(ASHARE_SIGNAL_DIR, f"order_{tag}.csv"), dtype={"code": str})
miss = [c for c in od["code"] if c not in all_text]
print(f"下单名单 {tag} 共 {len(od)} 只渲染：", "全中" if not miss else f"缺 {miss}")

# ---- 动态断言②：轴一致性——页面自报的轴与该日 meta 的 buy.expr 必须同真同假 ----
meta = json.load(open(os.path.join(ASHARE_SIGNAL_DIR, f"meta_{tag}.json")))
file_axis = (meta.get("buy") or {}).get("expr")
mismatch = file_axis != BUY_EXPR
warned = f"本页名单由 `{file_axis}`" in all_text
print(f"轴一致性：文件轴 `{file_axis}` vs 进程轴 `{BUY_EXPR}` ⇒ 应报不一致 = {mismatch}；"
      f"页面确实报不一致 = {warned} ⇒",
      "OK" if mismatch == warned else "错位（拿今天的判据解释昨天的名单）")

# 未被求值的 f-string 残留（花括号里带 ASHARE_ 或 {k}）= 措辞漏在 raw 串外
leak = re.findall(r"\{[A-Za-z_][A-Za-z0-9_ .\[\]'\"*:/]*\}", all_text)
leak = [x for x in leak if "ASHARE" in x or x in ("{k}", "{v}", "{c}")]
print("未求值占位符:", leak[:8] or "无")

# 下单名单那张表的形状
tbls = [d for d in at.dataframe if "weight" in [str(x) for x in d.value.columns]]
print("下单表个数:", len(tbls), "行数:", [len(d.value) for d in tbls])

# ---------- 判据：任何一项不合格就非零退出 ----------
# 这一节 09-27 才补上（回归时发现：这脚本会把「未命中」打印出来然后 exit 0，
# 等于报出问题却不拦流程 —— 打印只给人看，退出码才是给链路用的）。
FAIL = []
if at.exception:
    FAIL.append(f"看板有 {len(at.exception)} 处 exception")
if bad:
    FAIL.append(f"静态针未命中 {len(bad)}/{len(NEEDLES)}: {bad}")
if miss:
    FAIL.append(f"下单 {len(od)} 只有 {len(miss)} 只没渲染: {miss}")
if mismatch != warned:
    FAIL.append(f"轴一致性错位（应报={mismatch}、实报={warned}）")
if leak:
    FAIL.append(f"未求值占位符 {leak[:4]}")
if [len(d.value) for d in tbls] != [len(od), ASHARE_BUY_TOP_N]:
    FAIL.append(f"下单表形状不是 [下单数 {len(od)}, 名单数 {ASHARE_BUY_TOP_N}]: "
                f"{[len(d.value) for d in tbls]}")
print("\n[下单层渲染自检]",
      "全部通过" if not FAIL else f"失败 {len(FAIL)} 条：" + "；".join(FAIL))
sys.exit(1 if FAIL else 0)
