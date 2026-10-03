# -*- coding: utf-8 -*-
"""09-30 一次性冒烟：折数拨到 25 后，看板「稳不稳」那页的 caption 真能构造出来吗。
判据 5 格（任一红 ⇒ exit=1）：C1 整页 0 exception；C2 含「等分」的 caption 有且只有一条、
且里面写的折数是**当前配置值**（不是写死的 5）；C3 那条含新增的第⑤条边界；
C4 噪声底那句里的两个数必须等于**最近一次实测产物** `noise_floor.csv` 生产档那行的
`噪声p50 / 噪声max`（不钉数字原文 ⇒ 重跑噪声秤后文案若没跟上就红，而不是文案永远绿）；
**C5 = 尺子的牙**：同一把 needle 去量 `git show HEAD` 那份改动前的 app.py（写死「等分 5 段」），
必须抓不到 25、且确实写着 5 ⇒ 否则 C2 是恒绿装饰。旧文案取自 git 真身，不手写行形。"""
import os
import re
import subprocess
import sys
import pandas as pd
from streamlit.testing.v1 import AppTest
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
import _bootstrap  # noqa: F401
import config as C

at = AppTest.from_file(f"{ROOT}/stock/v1/src/app.py", default_timeout=600)
at.run()
FAIL = []
if at.exception:
    FAIL.append(f"C1 页面抛异常 {len(at.exception)} 条：{[e.value for e in at.exception][:2]}")
else:
    print(f"  ✅ [C1] 整页零异常（看板 {len(at.tabs)} 个 tab 全过 script runner）")
caps = [c.value for c in at.caption]
hit = [t for t in caps if "等分" in t]
if len(hit) != 1:
    FAIL.append(f"C2 含「等分」的 caption 应有 1 条，实得 {len(hit)} 条（全部 caption {len(caps)} 条）")
else:
    # 走模式解析（不钉整句原文）：文案里所有「按时间等分 N 段」的 N 必须**只有当前配置值**这一个
    nums = set(re.findall(r"按时间等分 (\d+) 段", hit[0]))
    ok = nums == {str(C.ASHARE_ROLL_FOLDS)}
    (print if ok else FAIL.append)(
        f"  {'✅ [C2]' if ok else '❌ [C2]'} 文案里的折数集合 {sorted(nums)} == 配置值 {C.ASHARE_ROLL_FOLDS}")
    if not ok:
        FAIL.append(f"C2 文案没跟上配置（当前 {C.ASHARE_ROLL_FOLDS}）：{hit[0][:160]}")
    (print if "⑤ `fold_*`" in hit[0] else FAIL.append)(
        f"  {'✅' if '⑤ `fold_*`' in hit[0] else '❌ [C3]'} [C3 第⑤条边界] 在文案里")
    # C4：不钉数字原文，拿最近一次实测产物对表 ⇒ 文案里那两个数必须是**那次跑出来的**
    m4 = re.search(r"噪声底，中位 (\d+) 条、最多 (\d+) 条", hit[0])
    FLOOR = f"{ROOT}/stock/v1/temp/tmp_roll_folds_noise_0930/noise_floor.csv"
    if not m4:
        FAIL.append(f"C4 文案里找不到「噪声底，中位 N 条、最多 M 条」这句：{hit[0][:200]}")
    elif not os.path.exists(FLOOR):
        FAIL.append(f"C4 没有实测产物可对表（先跑 roll_folds_noise_0930.py）：{FLOOR}")
    else:
        row = pd.read_csv(FLOOR).query("生产档")[["折数", "噪声p50", "噪声max", "真序打脸条数"]].iloc[0]
        got = (int(m4.group(1)), int(m4.group(2)))
        want = (int(row["噪声p50"]), int(row["噪声max"]))
        ok4 = got == want
        (print if ok4 else FAIL.append)(
            f"  {'✅' if ok4 else '❌ [C4]'} 文案 (中位,最多)={got} == 实测 {int(row['折数'])} 折那行 "
            f"(p50,max)={want}｜真序打脸 {int(row['真序打脸条数'])} 条")
        if not ok4:
            FAIL.append(f"C4 噪声底数字没跟上实测（文案 {got} vs 产物 {want}）")
# C5 尺子的牙：拿**同一把正则**去量 HEAD 那一份（改动前的 app.py，写死「等分 5 段」），
# 必须读出 {5}、且读不出当前配置值 ⇒ 否则 C2 是恒绿装饰（差异活不到这一层）。
# 旧文案直接取自 `git show`，不手写行形。
old = subprocess.run(["git", "show", "HEAD:stock/v1/src/app.py"], cwd=ROOT,
                     capture_output=True, text=True).stdout
nums_old = set(re.findall(r"按时间等分 (\d+) 段", old))
tooth = ("5" in nums_old) and (str(C.ASHARE_ROLL_FOLDS) not in nums_old)
(print if tooth else FAIL.append)(
    f"  {'✅ [C5]' if tooth else '❌ [C5]'} 负对照（尺子有牙）：同一把正则量 HEAD 那份旧文案"
    f"实读折数 {sorted(nums_old)} ⇒ 必须含 5、不含 {C.ASHARE_ROLL_FOLDS}")
if not tooth:
    FAIL.append(f"C5 尺子没牙：HEAD 那份读出 {sorted(nums_old)}"
                f"（{'竟然含配置值' if str(C.ASHARE_ROLL_FOLDS) in nums_old else '竟然不含 5=拿错了文本'}）")
print(f"\n===== 冒烟：{'全绿' if not FAIL else '红 ' + str(len(FAIL)) + ' 条'} =====")
for f in FAIL:
    print("  ❌", f)
print("（本探针只读：不起服务、不写任何产物；日志 probe_caption_25folds_0930.log）")
sys.exit(1 if FAIL else 0)
