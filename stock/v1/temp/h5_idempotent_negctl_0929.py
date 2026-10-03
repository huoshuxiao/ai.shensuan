# -*- coding: utf-8 -*-
"""负对照：给 `h5_idempotent_check_0927.py` 这把尺子装牙——同一对面板、只注入一处已知扰动。

命题：如果它对真数据报「0 格差异」是因为**比较逻辑根本不看值**，那注入的扰动也报不出来 ⇒ 那条绿是恒真的。
正确行为：注入 1 格值差 + 1 格 NaN↔有值差 ⇒ 必须念出「值不等 1 格、NaN 位置差 1 格」并 **exit 1**。
扰动打在内存里的 B 侧（不写盘、不碰任何产物），比较逻辑与正例逐字同源。
注入点必须现挑：值差那一格要选在**两侧都不是 NaN** 的位置——随机行号可能正好抽中 NaN，
而 `NaN + 1.0` 还是 NaN，尺子会「抓不到」得像个缺陷、其实是扰动没活下来。
留底那两个 `to_numpy()` 必须 `.copy()`：这张表列全同构（12 列 float32），pandas 返回的是**视图**，
不复制的话注入赋值会把要打印的「原值」一起改掉，日志里的数就成了注入后的假证据。
**这里的退出码和正例是反的**：1 = 两类差异都抓到（尺子有牙，正例那个 0 才可信）；
2 = 没抓全（尺子有缺陷 ⇒ 正例那条绿作废，回去改比较逻辑）。
"""
import sys

import numpy as np
import pandas as pd

A = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5"
B = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/temp/tmp_rerun_20260928_0929_0047/daily_pv.h5"
CHUNK = 1_000_000


def load(path, tag):
    with pd.HDFStore(path, mode="r") as st:
        df = st["/data"]
    print(f"[{tag}] shape={df.shape}", flush=True)
    return df


da, db = load(A, "现档"), load(B, "备份")
# —— 注入扰动：一格改值、一格把 NaN 换成有值（后者专打 NaN 位置那一路）——
a1 = da.iloc[:, 1].to_numpy().copy()
b1 = db.iloc[:, 1].to_numpy().copy()
row_v = int(np.where(~np.isnan(a1) & ~np.isnan(b1))[0][0])
orig_v = float(b1[row_v])
db.iat[row_v, 1] = np.float32(orig_v + 1.0)
row_n = int(np.where(np.isnan(da.iloc[:, 2].to_numpy()))[0][0])
db.iat[row_n, 2] = np.float32(1.0)
print(f"[注入] 值差 1 格在第 {row_v} 行（原值 {orig_v:.7g} → +1.0）、"
      f"NaN→有值 1 格在第 {row_n} 行", flush=True)

va, vb = da.to_numpy(), db.to_numpy()
diffs = nan_mismatch = 0
max_abs = 0.0
for lo in range(0, va.shape[0], CHUNK):
    hi = min(lo + CHUNK, va.shape[0])
    ca, cb = va[lo:hi].astype("float64"), vb[lo:hi].astype("float64")
    na, nb = np.isnan(ca), np.isnan(cb)
    nan_mismatch += int((na != nb).sum())
    both = ~na & ~nb
    d = np.where(both, np.abs(ca - cb), 0.0)
    diffs += int((d > 0).sum())
    max_abs = max(max_abs, float(d.max(initial=0.0)))
    print(f"  行 {lo}~{hi}：累计值不等 {diffs} 格、NaN 位置差 {nan_mismatch} 格、"
          f"最大绝对差 {max_abs:.3e}", flush=True)

print(f"[读数] 值不等 {diffs} 格｜NaN 位置不同 {nan_mismatch} 格｜最大绝对差 {max_abs:.3e}")
if diffs >= 1 and nan_mismatch >= 1:
    print("⇒ 尺子有牙：注入的两类差异都被抓到（值差 / NaN↔有值），正例那个 0 不是恒真")
    sys.exit(1)
print("❌ 尺子有缺陷：两类注入差异没有都抓到 ⇒ 正例那条绿不许引用")
sys.exit(2)
