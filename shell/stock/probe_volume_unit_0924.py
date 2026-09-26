# -*- coding: utf-8 -*-
"""$volume 单位判定：纯内部判据，不依赖任何行情接口（09-24）

为什么必须自己测：日更脚本往 bin 里写 volume 那一格时用的是
`volume = 真实手数 / $factor`，这条口径的证据来自另一个会话的探针，而它的脚本
已经不在仓库里了。写错单位不会报错，只会让所有量能因子在日更之后悄悄换量纲。

判据一（交易所规则：A 股一手 = 100 股，成交以手为最小单位 ⇒ **手数恒为整数**）
    设面板某格 V=$volume、f=$factor：
      (i)  V = 手数 / f  ⇒ 手 = V·f 恒为整数
      (ii) V = 手数       ⇒ 手 = V   恒为整数
    错的那一侧小数部分在 [0,1) 上近似均匀，落在整数 ±0.1 内只有 ~20% 偶然命中率；
    对的那侧应接近 100%（float32 在 1e5~1e7 量级的绝对误差 0.006~0.6 ≪ 0.1）。

判据二（同格自洽，与判据一互相独立）：成交额(元) = 均价 × 股数，包里
amount = 元/1000、vwap = 均价 × f（09-24 已用 bulk 快照反证），两式合起来给出
手 = 10·A·f/w ⇒ 比值 w·V/(10·A) 在 (i) 下恒等于 1，在 (ii) 下恒等于 f。
所以看这个比值跟 f 相不相干，就能分辨两条口径。
"""
import os

import numpy as np
import pandas as pd

PROVIDER = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
            "qlib/qlib_data/cn_data")
N_INST = 200                 # 按 $factor 分层抽样的只数
N_DAY = 250                  # 每只票取最后这么多格
TOL = 0.1                    # 「算整数」的绝对容忍带

cal = [ln.strip() for ln in open(os.path.join(PROVIDER, "calendars", "day.txt")) if ln.strip()]
feat = os.path.join(PROVIDER, "features")
insts = sorted(d for d in os.listdir(feat) if os.path.isdir(os.path.join(feat, d)))
print(f"日历 {len(cal)} 格（末 {cal[-1]}），features {len(insts)} 只票")


def load(inst, field):
    p = os.path.join(feat, inst, f"{field}.day.bin")
    if not os.path.exists(p):
        return None, None
    a = np.fromfile(p, dtype="<f4")
    return int(a[0]), a[1:]


# 分层抽样：按末格 f 十分位各取若干，免得只看到某一档量级
last_f = {}
for i in insts:
    st, v = load(i, "factor")
    if v is not None and len(v) and np.isfinite(v[-1]) and v[-1] > 0:
        last_f[i] = float(v[-1])
ser = sorted(last_f.items(), key=lambda kv: kv[1])
strata = [ser[i * len(ser) // N_INST:(i + 1) * len(ser) // N_INST] for i in range(N_INST)]
sample = [s[len(s) // 2] for s in strata if s]
print(f"抽样 {len(sample)} 只，末格 f 从 {sample[0][1]:.5f} 到 {sample[-1][1]:.5f}")


def int_rate(x):
    return float((np.abs(x - np.round(x)) < TOL).mean())


rows = []
for inst, _f in sample:
    got = {k: load(inst, k) for k in ("volume", "factor", "amount", "vwap")}
    if any(g is None for g in got.values()):
        continue
    V = got["volume"][1][-N_DAY:].astype("float64")
    F = got["factor"][1][-len(V):].astype("float64")
    A = got["amount"][1][-len(V):].astype("float64")
    W = got["vwap"][1][-len(V):].astype("float64")
    m = (np.isfinite(V) & np.isfinite(F) & np.isfinite(A) & np.isfinite(W)
         & (V > 0) & (A > 0) & (F > 0) & (W > 0))
    if m.sum() < 50:
        continue
    v, f, a, w = V[m], F[m], A[m], W[m]
    rows.append({
        "inst": inst, "n": int(m.sum()), "f": float(f[-1]),
        "V整数率": int_rate(v), "Vf整数率": int_rate(v * f),
        "V/f整数率": int_rate(v / f), "Vff整数率": int_rate(v * f * f),
        "wV/10A": float(np.median(w * v / (10 * a))),
    })

df = pd.DataFrame(rows)
pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 20)
print(f"\n判据一：逐票「末 {N_DAY} 格」取整命中率（{len(df)} 只票）")
print(df[["V整数率", "Vf整数率", "V/f整数率", "Vff整数率"]].describe().loc[
    ["min", "25%", "50%", "75%", "max"]].round(4).to_string())
print(f"  命中率 ≥95% 的票数：V={int((df['V整数率'] >= .95).sum())}  "
      f"V·f={int((df['Vf整数率'] >= .95).sum())}  "
      f"V/f={int((df['V/f整数率'] >= .95).sum())}  "
      f"V·f·f={int((df['Vff整数率'] >= .95).sum())} / {len(df)}")
print(f"\n判据二：中位 w·V/(10A) = {df['wV/10A'].median():.6g}"
      f"（p05 {df['wV/10A'].quantile(.05):.6g} / p95 {df['wV/10A'].quantile(.95):.6g}）"
      f"；与 f 的相关 {np.corrcoef(df['wV/10A'], df['f'])[0, 1]:+.3f}")
print("  ⇒ 比值恒为 1 且与 f 无关 ⇒ 口径 (i) volume = 真实手数/$factor")
print("  ⇒ 比值 ≈ f（与 f 强相关）    ⇒ 口径 (ii) volume = 真实手数")
print("\n抽样前 6 只：")
print(df.head(6).round(4).to_string(index=False))

# 判据一按板块拆开看：注册制（创业板/科创板/北交所）允许 1 股递增的成交，
# 手数本来就不是整数，把它混进全样本会把「整数」这条旁证摊平。主板（60/00）
# 必须以 100 股为最小单位成交 ⇒ 若口径是 V=手/f，那 **V·f 应当几乎格格局整数**。
df["board"] = np.select(
    [df["inst"].str.startswith(("sh60", "sz00")), df["inst"].str.startswith("sh68"),
     df["inst"].str.startswith("sz30"), df["inst"].str.startswith("bj")],
    ["主板", "科创板", "创业板", "北交所"], "其他")
print(f"\n判据一按板块（{N_DAY} 格窗口，中位取整命中率）：")
print(df.groupby("board")[["V整数率", "Vf整数率"]].agg(["count", "median", "max"]).round(3).to_string())
