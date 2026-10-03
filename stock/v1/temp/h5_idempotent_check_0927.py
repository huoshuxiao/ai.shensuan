# -*- coding: utf-8 -*-
"""② 重写面板后的**数据层**对拍：现档 vs 动前的备份，逐格比，不看 md5。

为什么要它：`chain_button_e2e_0924.py` 的 docstring 写着「这一场 bin 没往前走 ⇒ 重写出来的
就是同一份内容」，可本轮实测 **md5 变了**（字节数一模一样、头 512 字节也一样）。
md5 不同只说明「文件里有东西不同」，说明不了是不是**数据**不同——HDF5 的对象头里带
PyTables 写的时间戳，纯元数据变化也会翻 md5。所以判「内容等不等价」必须在数据层判：
比 index / columns / 每一格的值（含 NaN 位置）。

为什么不能分块读：这张表是 `FrameFixed`（pandas 的 fixed 格式），带 MultiIndex 行轴，
`select(iterator=True/chunksize=)` 会直接 TypeError，`start/stop` 切片也会在读 multi
index 时报错。实测只能整表 `store["/data"]` 拿进来（15,176,062 行 × 12 列 float32，
约 0.73GB/份，两份同时在内存 ≈1.7GB，机器 16G 上限内），再对 numpy 数组按行分块比，
避免为了比精度把整表升成 float64（那会再多 2.9GB）。
退出码：0 = 数据逐格等价（差异只在元数据）；1 = 数据真的动了。

**09-29 换过 B 侧配对**（原来的 B = `tmp_chain_backup_0927/daily_pv.h5`，09-26 11:05 那一版，那块面板当天核过过期、已删）：
两侧行数差 5,557（两场新开的票），拿它跟现档比会在 `if da.shape != db.shape` 那一格判红 ⇒ **那是设计行为，不是面板换版**。
现在 B 指 `tmp_rerun_20260928_0929_0047/daily_pv.h5` = **同一场次（09-28）的真生产面板归档**，
于是这一对证的是更硬的一句话：一遍是真日更 21:01 写的、一遍是 `--from-bin` **把末格砍掉再贴回**之后 12:19 写的，
两遍之间隔着 rollback ⇒ 若仍逐格等价，「② 重写幂等」就不只在"bin 没往前走"那种舒适情形下成立。
⚠️ 路径写死是有代价的：09-29 13:04 有人把 `shell/{stock,etf,live2etf}/` 整批改搬进各线的 `temp/`，
这两条绝对路径跟着换过一次 ⇒ 以后再搬目录，这两行和 B6 那两个 helper 会一起断，改动时记得同批扫一遍。
"""
import sys

import numpy as np
import pandas as pd

A = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/results/rdagent_output/git_ignore_folder/factor_implementation_source_data/daily_pv.h5"
B = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/temp/tmp_rerun_20260928_0929_0047/daily_pv.h5"
CHUNK = 1_000_000


def load(path, tag):
    with pd.HDFStore(path, mode="r") as st:
        keys = [str(k) for k in st.keys()]
        assert keys == ["/data"], f"[{tag}] 表名不是预期的 ['/data']：{keys}"
        shape = list(st.get_storer("/data").shape)
        df = st["/data"]
    print(f"[{tag}] 读出 shape={df.shape}（元数据记的 shape={shape}）"
          f"　列={list(df.columns)}", flush=True)
    return df


da, db = load(A, "现档"), load(B, "备份")

struct_bad = 0
if list(da.columns) != list(db.columns):
    struct_bad += 1
    print(f"❌ 列名不同：只在新档 {set(da.columns) - set(db.columns)}　"
          f"只在备份 {set(db.columns) - set(da.columns)}")
if da.index.names != db.index.names:
    struct_bad += 1
    print(f"❌ index 层名不同：{da.index.names} vs {db.index.names}")
if da.shape != db.shape:
    struct_bad += 1
    print(f"❌ 形状不同：{da.shape} vs {db.shape}")
if struct_bad:
    print("\n[结论] 结构本身不同 ⇒ 别再逐格比，先看 ② 的写出方式")
    sys.exit(1)

# 行轴（datetime, instrument）逐行按位置比：位置一错，后面所有格子的比较都失去意义
ia, ib = da.index, db.index
idx_mismatch = int((ia != ib).sum()) if len(ia) == len(ib) else len(ia)
if idx_mismatch:
    first = int(np.argmax(np.asarray(ia != ib)))
    print(f"❌ index 不同 {idx_mismatch} 行，首个在第 {first} 行：{ia[first]} vs {ib[first]}")

va = da.to_numpy()
vb = db.to_numpy()
if va.dtype != vb.dtype:
    print(f"⚠️ dtype 不同：{va.dtype} vs {vb.dtype}")
diffs = nan_mismatch = 0
max_abs = 0.0
worst = None
for lo in range(0, va.shape[0], CHUNK):
    hi = min(lo + CHUNK, va.shape[0])
    ca, cb = va[lo:hi].astype("float64"), vb[lo:hi].astype("float64")
    na, nb = np.isnan(ca), np.isnan(cb)
    nan_mismatch += int((na != nb).sum())
    both = ~na & ~nb
    d = np.where(both, np.abs(ca - cb), 0.0)
    n = int((d > 0).sum())
    if n:
        pos = np.unravel_index(int(np.argmax(d)), d.shape)
        worst = worst or (lo + pos[0], pos[1], float(ca[pos]), float(cb[pos]))
    diffs += n
    max_abs = max(max_abs, float(d.max(initial=0.0)))
    print(f"  行 {lo}~{hi}：累计值不等 {diffs} 格、NaN 位置差 {nan_mismatch} 格、"
          f"最大绝对差 {max_abs:.3e}", flush=True)

print(f"\n[结论] index 不同 {idx_mismatch} 行｜值不等 {diffs} 格｜NaN 位置不同 "
      f"{nan_mismatch} 格｜最大绝对差 {max_abs:.3e}")
if worst:
    print(f"  首个差值样本：第 {worst[0]} 行 列 {da.columns[worst[1]]} "
          f"现档 {worst[2]!r} vs 备份 {worst[3]!r}")
bad = struct_bad or idx_mismatch or diffs or nan_mismatch
if bad:
    print("⇒ **数据真的动了**：② 不是幂等重写，归档读数要按「面板换版」对待，"
          "md5 之外还得逐表对差")
else:
    print("⇒ 数据逐格等价：md5 不同只出在 HDF5 元数据（PyTables 时间戳一类），"
          "「② 重写出同一份内容」这句在数据层成立")
sys.exit(1 if bad else 0)
