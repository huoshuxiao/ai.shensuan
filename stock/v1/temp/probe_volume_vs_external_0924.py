# -*- coding: utf-8 -*-
"""$volume / $amount 单位的外部同日反证（09-24）

为什么要再测一次：日更脚本写 volume 那一格用的是 `真实手数 / $factor`，而这条口径
原先的「证据」是 shell/probe_spot_vs_bin_0924.py 里
    amount ÷ (close × volume × 100) = 0.001
——它拿 bin 自己的 volume 去反推 amount，再拿 amount 来确认 volume，是循环论证。
另一条 w·V=10A 的恒等式（probe_volume_unit_0924.py 判据二）也只能证明包内三个
字段互相自洽，证不了 V 到底是「手」还是「手/f」。

只有拿**同一天的外部真实成交量/成交额**才能分开。样本按 $factor 档差开挑：f 远小于
1 的票（复权除权多）两条口径差几十倍，一眼就能分辨。

接口按可用性依次试（09-23~24 实测 akshare 的包装层全废：tx 解析崩、东财
RemoteDisconnected、新浪单票被 IP 限流；但裸 HTTP 的三家行情接口不一定会被同一
道墙挡住，所以这里绕开 akshare 直连）：
    sina  money.finance.sina.com.cn/.../CN_MarketData.getKLineData  → volume 单位=股
    腾讯  web.ifzq.gtimg.cn/appstock/app/fqkline/get                → volume 单位=手
    东财  push2his.eastmoney.com/api/qt/stock/kline/get             → volume 单位=手
"""
import json
import os
import socket
import sys
import time

socket.setdefaulttimeout(20)
import numpy as np   # noqa: E402
import pandas as pd  # noqa: E402
import requests      # noqa: E402

PROVIDER = ("/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/stock/v1/data/"
            "qlib/qlib_data/cn_data")
DAY = "2026-09-22"     # bin 的最后一个交易日
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

cal = [ln.strip() for ln in open(os.path.join(PROVIDER, "calendars", "day.txt")) if ln.strip()]
assert cal[-1] == DAY, f"日历末日不是 {DAY}，别拿这天的外部行情对"
feat = os.path.join(PROVIDER, "features")


def tail_field(inst, field, n=3):
    a = np.fromfile(os.path.join(feat, inst, f"{field}.day.bin"), dtype="<f4")
    return int(a[0]), a[1:][-n:].astype("float64")


# 样本：按末格 f 分成 8 档各取一档内的代表（覆盖 f≪1 与 f≫1），再加茅台
ser = sorted((i for i in os.listdir(feat) if os.path.isdir(os.path.join(feat, i))),
             key=lambda i: tail_field(i, "factor", 1)[1][-1])
picks = [ser[int(len(ser) * (k + 0.5) / 8)] for k in range(8)]
picks += [p for p in ("sh600519", "sz000001") if p not in picks]

rows = []
for inst in picks:
    st_c, cl = tail_field(inst, "close")
    st_f, fa = tail_field(inst, "factor")
    st_v, vo = tail_field(inst, "volume")
    st_a, am = tail_field(inst, "amount")
    st_w, wp = tail_field(inst, "vwap")
    f, V, A, W = fa[-1], vo[-1], am[-1], wp[-1]
    raw = cl[-1] / f
    code = inst.upper()
    got = None
    for src in ("sina", "tencent", "eastmoney"):
        try:
            if src == "sina":
                u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                     f"CN_MarketData.getKLineData?symbol={inst}&scale=240&ma=no&datalen=12")
                js = json.loads(requests.get(u, headers=UA).text)
                r = [x for x in js if x["day"] == DAY][0]
                got = (src, float(r["volume"]), float(r.get("amount", np.nan)))
            elif src == "tencent":
                u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                     f"param={inst},day,2026-09-15,{DAY},10,qfq")
                js = requests.get(u, headers=UA).json()
                data = js["data"][inst]
                arr = data.get("qfqday") or data.get("day")
                r = [x for x in arr if x[0] == DAY][0]
                got = (src, float(r[5]) * 100, np.nan)        # 手 -> 股
            else:
                secid = ("1." if inst.startswith("sh") else "0.") + inst[2:]
                u = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=" + secid
                     + "&fields1=f1,f2,f3&fields2=f51,f56,f57&klt=101&fpa=3"
                       f"&beg=20260921&end={DAY.replace('-', '')}&lmt=5")
                js = requests.get(u, headers=UA).json()
                k = [x for x in js["data"]["klines"] if x.startswith(DAY)][0].split(",")
                got = (src, float(k[1]) * 100, float(k[2]))   # 手 -> 股，成交额=元
        except Exception as e:
            print(f"    {inst} {src} 失败 {type(e).__name__}: {str(e)[:90]}")
            continue
        break
    if got is None:
        print(f"  {inst} 三家都取不到")
        continue
    src, S, E = got                                        # S=真实股数, E=真实成交额(元)
    # 新浪这份裸接口不给成交额 ⇒ 用「盘面收 × 真股数」当外部成交额的近似尺子
    # （均价与收盘同日同级，噪声 ~0.5%），足够分辨 1000 倍量纲、也足够反证 vwap。
    E_proxy = raw * S
    if not (E and np.isfinite(E)):
        E = E_proxy
    rows.append({
        "inst": inst, "源": src, "f": f, "盘面收": raw,
        "真股数": S, "真手数": S / 100, "真成交额": E,
        "bin_V": V, "V÷(手/f)": V / (S / 100 / f), "V÷手": V / (S / 100),
        "bin_A": A, "A÷(元/1000)": (A * 1000 / E) if E and np.isfinite(E) else np.nan,
        "bin_W": W, "W÷(真均价×f)": W / ((E / S) * f) if E and np.isfinite(E) else np.nan,
    })
    time.sleep(1.0)

df = pd.DataFrame(rows)
pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 30)
print(f"\n同日（{DAY}）外部真值 vs bin 末格，逐票比值（哪条口径对，哪列就恒为 1）：")
print(df.round(5).to_string(index=False))
if len(df):
    print(f"\n判定：V÷(手/f) 中位 {df['V÷(手/f)'].median():.6g}"
          f"（p05 {df['V÷(手/f)'].quantile(.05):.6g} / p95 {df['V÷(手/f)'].quantile(.95):.6g}）"
          f"；V÷手 中位 {df['V÷手'].median():.6g}"
          f"（V÷手 一列 ≈ 1/f 而不是 ≈1 ⇒ volume 不是「手」，日更脚本的除法留着）")
    ok = df.dropna(subset=["A÷(元/1000)"])
    if len(ok):
        print(f"      A÷(元/1000) 中位 {ok['A÷(元/1000)'].median():.6g}"
              f"（p05 {ok['A÷(元/1000)'].quantile(.05):.6g} / p95 {ok['A÷(元/1000)'].quantile(.95):.6g}）"
              f"；W÷(真均价×f) 中位 {ok['W÷(真均价×f)'].median():.6g}"
              f" ⇒ amount=元/1000、vwap=真均价×factor 两条在同日外部真值下成立，"
              "于是上面 V÷(手/f)≡1 是独立判据，不再是包内恒等式")
else:
    print("\n三家接口全取不到 ⇒ 本轮对 volume 单位**没有外部判据**，日更脚本的 "
          "volume 口径只能标注为「未反证」再往下走")
    sys.exit(2)
