"""丙（#26）报价尺子：official 折内泄漏「修到哪一层」值多少 bar —— 只读，判据一行不改。

读数口径（全部本会话现量）：
  R1  同一份 `factors.json` 是不是三折共用的：三折 entry 里 source=official 的表达式集合是否重合
  R2  「回收侧点时过滤」这条便宜路（B′）值多少：每折训练段里，有多少只标的的**首根 K 线晚于
      该折训练段起点** ⇒ 候选在全历史面板上挑的时候，这些标的根本还不存在
  R3  这条便宜路的**天花板**：过滤只能剔标的、剔不掉「表达式是在全集上选出来的」这一层
  R4  真点时重挖（A 路）的前置：qlib bin / 全历史 h5 有没有按折切窗的入口

用法：/usr/bin/python3.10 etf/v1/temp/official_pit_probe_0930.py
"""
import os
import sys
import json
import pickle

HERE = os.path.dirname(os.path.abspath(__file__))
BYPASS = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(BYPASS, "src"))
import _bootstrap  # noqa: F401

from config import FREQ, DATA_DIR                              # noqa: E402
from data_loader import DataLoader                             # noqa: E402
from etf_universe import get_universe                          # noqa: E402
from walk_forward import make_splits                           # noqa: E402

CKPT = os.path.abspath(os.path.join(BYPASS, "..", "..", "common", "data",
                                    "etf", "cache", "run_checkpoint_daily.pkl"))
OFFICIAL_JSON = os.path.join(DATA_DIR, "results", "rdagent_output", "factors.json")
GATE = 240


def die(msg):
    print(f"❌ {msg}")
    sys.exit(1)


universe = get_universe()
pool = DataLoader(freq=FREQ).load_pool(universe.universe["code"].tolist())
ref_code = max(pool, key=lambda c: len(pool[c]))
all_ts = pool[ref_code].index
splits = make_splits(all_ts)

blob = pickle.load(open(CKPT, "rb"))
entries = blob.get("entries") or {}

print("\n===== R1 三折回收的是不是同一批 official 表达式 =====")
per_fold_official = {}
for k, v in entries.items():
    facs = v.get("value") or []
    off = [str(f.get("expr") or f.get("name") or "") for f in facs
           if str(f.get("source") or "") == "official"]
    if k == "主线多源因子" or k.startswith("折 "):
        per_fold_official[k] = off
        print(f"  {k:12s} 因子 {len(facs)} 条，其中 official {len(off)} 条："
              f"{'、'.join(x[:28] for x in off) or '（无）'}")

sets = {k: set(v) for k, v in per_fold_official.items() if v}
fold_keys = [k for k in sets if k.startswith("折")]
for i, a in enumerate(fold_keys):
    for b in fold_keys[i + 1:]:
        inter = sets[a] & sets[b]
        print(f"  {a} ∩ {b} = {len(inter)} 条（各自的 official 数 {len(sets[a])}/{len(sets[b])}）"
              f"{'  ⇒ 完全同一批' if inter == sets[a] == sets[b] else ''}")

print("\n===== R2 便宜路 B′ 能剔掉多少标的（点时不存在）=====")
for i, (tr_s, tr_e, *_rest) in enumerate(splits):
    train = {c: df.loc[tr_s:tr_e] for c, df in pool.items()}
    train = {c: df for c, df in train.items() if len(df) > GATE}
    first = {c: pool[c].index[0] for c in pool}
    late = [c for c in train if first[c] > tr_s]
    print(f"  折 {i+1} 训练段 {tr_s.date()} ~ {tr_e.date()}：可用 {len(train)} 只，"
          f"其中首根晚于训练段起点 **{len(late)} 只（{len(late)/max(len(train),1):.0%}）**"
          f" ⇒ 候选在全历史挑时，这些票还没出生")

print("\n===== R3 B′ 的天花板 =====")
print("  上面剔的是「标的层」：表达式在折内还能算。但 official 那一源**挖它时看到的是 100 只 × 全历史**，")
print("  B′ 救不回「同一批表达式是从全集选出来的」这一层选择偏差 ⇒ 只能把读数改诚实，不能让折变点时。")

print("\n===== R4 真点时重挖（A 路）的前置：数据面有没有按折切窗的入口 =====")
d = json.load(open(OFFICIAL_JSON))
print(f"  official 落盘 {os.path.basename(OFFICIAL_JSON)}：{len(d)} 条，"
      f"键={sorted(d[0])}｜戳={os.path.getmtime(OFFICIAL_JSON)}")
print("  ⇒ 文件里**没有任何时间窗键**（无 start/end/window）⇒ 想在回收侧验点时，无凭据可用，")
print("    只能重开容器：`common/rdagent_docker/pregen_source_data.py:41 START=\"2008-12-29\"` 写死全量，")
print("    qlib bin 的 calendars/day.txt 也只有一份全历史 ⇒ 折级切窗要先重造数据面。")
