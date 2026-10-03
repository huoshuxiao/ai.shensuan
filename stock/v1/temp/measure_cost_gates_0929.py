# -*- coding: utf-8 -*-
"""T4a 成本 / 执行 / 仓位类人工阈值：只读落盘产物，**不碰面板**（内存 ~50MB）。

为什么这一组能离线量：手续费是在回放之外按换手乘出来的（公式在下面的模型里核对），
调仓周期/名单形状要重跑才动 ⇒ 那些归到面板那一趟（`measure_shape_gates_0929.py`）。
本脚本量的是「拿现有产物就能算清」的四根，外加把已经量过的档位点名，避免重复劳动：

  ASHARE_PORT_COST_ONE_WAY=0.0015   单边费率        归档 gross/net + 换手，可反解
  ASHARE_INDUSTRY_MAX_AGE=30        行业映射保鲜期  文件年龄 + 覆盖率 + 名单里几只能被管到
  ASHARE_FILL_PRICE_TOL=0.21        人工录入价报警  账本结构（成交 0 行 ⇒ 从没咬过）
  MA_WIN=20 / M7-30,45 / 252        仓位层三根      09-27 已量完，这里只点名 + 前向账本现状

只读，不写生产件；产物写到 `temp/tmp_cost_gates_0929/`。

判据不恒真：
  A 税必须**严格正比于换手**（13 行、换手相差 2.09 倍，税系数 k 要恒定），再拿 config 的
     费率除回去反推回放年数，必须与归档自报的天数对得上；故意用 0.0010 解释 ⇒ 年数歪 50%
  C 覆盖率缺口必须真是缺口（拿一只确定在面板里、不在映射里的票点名），保鲜期阈值
     压到文件年龄以下必须翻成「今天会重抓」
  D 若成交账本以后有了行，本块的读数会跟着变 ⇒ 打印的是「本次实到的行数」，不是写死的 0
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(HERE, "tmp_cost_gates_0929")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "stock", "v1", "src"))
import _bootstrap  # noqa: E402,F401
import config as C  # noqa: E402

RES = os.path.join(REPO, "stock", "v1", "data", "results")
SIG = os.path.join(RES, "daily_signal")
QLIB = os.path.join(REPO, "common", "data", "stock", "qlib", "qlib_data", "cn_data")
FAIL = []


def block(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


t0 = time.time()

# ===================== A 单边费率 ASHARE_PORT_COST_ONE_WAY =====================
block(f"A 单边费率 ASHARE_PORT_COST_ONE_WAY={C.ASHARE_PORT_COST_ONE_WAY}：先反解、再摆档")
bl = pd.read_csv(os.path.join(RES, "ashare_portfolio_buylist.csv"))
days_hat = int(bl.excess_univ_ew_days.max())          # 归档自己报的回放天数
drag = (bl.ann_return_gross - bl.ann_return).to_numpy()
# 模型：净 = 毛 − 2 × 单边换手 × 场数 × 费率 / 年数（买一次卖一次 ⇒ 系数 2）
# 先做**不含费率、不含年数**的形状检验：税 / (2·换手·场数) 必须在 13 行之间恒定
k = drag / (2.0 * bl.one_way_turnover * bl.n_rebal)
spread = bl.one_way_turnover.max() / bl.one_way_turnover.min()
print(f"  {len(bl)} 行的税系数 k = 税/(2×换手×场数)：最小 {k.min():.8f} 最大 {k.max():.8f}"
      f"（相差 {k.max() / k.min() - 1:.2e}）｜这几行换手相差 {spread:.2f} 倍"
      f"（{bl.one_way_turnover.min():.3f}~{bl.one_way_turnover.max():.3f}）"
      f"⇒ k 恒定 = 按换手比例扣、没有固定底")
if k.max() / k.min() - 1 > 1e-5:
    FAIL.append("A 形状检验未过：税不严格正比于换手 ⇒ 归档扣的不是这一条，档位表作废")
# k = 费率 / 年数 ⇒ 拿 config 的费率除回去就得到回放跨度
yrs_hat = float(C.ASHARE_PORT_COST_ONE_WAY / k.iloc[0])
print(f"  拿 config 的费率除回去 ⇒ 回放跨度 {yrs_hat:.3f} 年"
      f"（归档自报 {days_hat} 交易日 = {days_hat / 252:.3f} 年）")
if abs(yrs_hat - days_hat / 252) / (days_hat / 252) > 0.02:
    FAIL.append(f"A 反推跨度 {yrs_hat:.2f} 年与归档 {days_hat / 252:.2f} 年差超 2%"
                "⇒ 费率与年数对不上，档位表作废")
# 有牙：故意拿错费率去解释同一批数，反推出来的跨度必须歪掉
yrs_wrong = float(0.0010 / k.iloc[0])
print(f"  反证：改用 0.0010 解释 ⇒ 反推跨度变成 {yrs_wrong:.2f} 年"
      f"（歪 {yrs_wrong / yrs_hat - 1:+.0%}，对不上才对 ✅）")
if abs(yrs_wrong - yrs_hat) < 0.01:
    FAIL.append("A 反证失败：换费率竟然还能给出同一个跨度 ⇒ 模型无牙")
print("  档位表（净年化 = 毛年化 − 税；税与费率严格成正比，因为回放里换手不受费率影响）")
for lab in ("参照·不剔除", "并集(命中>=3)", "单条·量能动量"):
    r = bl[bl.variant == lab].iloc[0]
    d0 = (r.ann_return_gross - r.ann_return) / C.ASHARE_PORT_COST_ONE_WAY
    cells = []
    for c in (0.0005, 0.0010, C.ASHARE_PORT_COST_ONE_WAY, 0.0025, 0.0050):
        net = r.ann_return_gross - d0 * c
        tag = "*" if abs(c - C.ASHARE_PORT_COST_ONE_WAY) < 1e-9 else " "
        cells.append(f"{tag}{c:.4f}→{net:+.2%}")
    print(f"    {lab:<14} 毛 {r.ann_return_gross:+.2%}｜换手 {r.one_way_turnover:.3f}/场｜"
          + " ".join(cells))
print("  ⚠️ 这张表只重算了税，**没有**重跑「费率变了会不会少换几次票」——回放里税是事后乘的")

# ===================== B 调仓周期 ASHARE_PORT_HOLD =====================
block(f"B 调仓周期 ASHARE_PORT_HOLD={C.ASHARE_PORT_HOLD}：离线只能确认它现在是多少")
first = pd.Timestamp("2015-01-05")
row0 = bl.iloc[0]
print(f"  归档回放 {int(row0.n_rebal)} 个调仓日 × {C.ASHARE_PORT_HOLD} 交易日"
      f" = {int(row0.n_rebal) * C.ASHARE_PORT_HOLD} 格（归档自报 {days_hat} 个交易日"
      f" = {days_hat / 252:.2f} 年，起点 {str(first.date())}）")
print("  换这一档会改整条持仓路径（不同日子建仓 ⇒ 毛收益本身变），**离线量不了**"
      "⇒ 归到面板那一趟；本块不编档位表。")

# ===================== C 行业映射保鲜期 ASHARE_INDUSTRY_MAX_AGE =====================
block(f"C 行业映射保鲜期 ASHARE_INDUSTRY_MAX_AGE={C.ASHARE_INDUSTRY_MAX_AGE} 天")
p = C.ASHARE_INDUSTRY_CSV
age = (time.time() - os.path.getmtime(p)) / 86400
im = pd.read_csv(p)
print(f"  文件 {p}\n  行数 {len(im):,}｜抓取于 "
      f"{dt.datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d %H:%M}｜现龄 {age:.1f} 天")
for t in (7, 15, C.ASHARE_INDUSTRY_MAX_AGE, 90, 365):
    tag = "  ←生产" if t == C.ASHARE_INDUSTRY_MAX_AGE else ""
    print(f"  阈值 {t:>4} 天：今天{'会' if age > t else '不会'}重抓"
          f"｜按此节奏一年约重抓 {365 / t:.1f} 次{tag}")
# 有牙：把阈值压到文件年龄以下，读数必须翻过来说「会重抓」
if not (age > 5):
    FAIL.append(f"C 无牙：文件年龄 {age:.1f} 天，压到 5 天竟还判不出「该重抓」")
elif age > 5:
    print(f"  有牙：阈值改成 5 天（低于现龄 {age:.1f}）⇒ 今天判「会重抓」✅")
inst = sorted({ln.split()[0] for ln in open(os.path.join(QLIB, "instruments", "all.txt"))
               if ln.strip()})
have = set(im["代码"].astype(str))
miss = [i for i in inst if i not in have]
print(f"  覆盖率：面板 {len(inst):,} 只里有行业行 {len(inst) - len(miss):,} 只"
      f"（{1 - len(miss) / len(inst):.1%}）｜**没有行业行 {len(miss):,} 只**")
print(f"  缺口点名（前三只）：{miss[:3]}——在面板里、在映射里没有 ⇒ 这几只行业判据碰不到")
_by = pd.Series([i[:2] for i in miss]).value_counts()
print("  缺口的板块构成：" + "、".join(f"{k} {_v}只" for k, _v in _by.items()))
bl_names = im["行业"].fillna("(空)")
print("  行业桶最大的五个：" + "、".join(f"{k} {v}只" for k, v in bl_names.value_counts().head(5).items()))
buy = pd.read_csv(os.path.join(SIG, "buy_20260928.csv"))
ind = buy["行业"].fillna("(空)")
print(f"  最近一场名单 {len(buy)} 席里：无行业行 {(~buy['code'].isin(have)).sum()} 只"
      f"｜落在「次新股」这一桶 {(ind == '次新股').sum()} 只｜行业取值 {ind.nunique()} 种")
if (~buy["code"].isin(have)).sum() == 0 and (ind == "(空)").sum() == 0:
    print("  ⇒ 这一场的行业闸是全牙的（每席都有行业可判）")
top3 = ind.value_counts().head(3)
print("  名单行业集中度 top3：" + "、".join(f"{k} {v}只" for k, v in top3.items()))

# ===================== D 人工录入价容差 ASHARE_FILL_PRICE_TOL =====================
block(f"D 人工录入价容差 ASHARE_FILL_PRICE_TOL={C.ASHARE_FILL_PRICE_TOL}：这道闸咬过没有")
fills = pd.read_csv(C.ASHARE_FILLS_CSV, comment="#", encoding="utf-8-sig")
n_real = int(fills["日期"].notna().sum())
print(f"  成交账本 {C.ASHARE_FILLS_CSV}：实到 {n_real} 行（表头与注释已排除）")
for k, lab in (("ASHARE_POSITION_OUT", "持仓表"), ("ASHARE_ACCOUNT_OUT", "账户表")):
    print(f"  {lab} {getattr(C, k)}：{'存在' if os.path.exists(getattr(C, k)) else '不存在'}")
if n_real == 0:
    print("  ⇒ 本机 0 条成交 ⇒ 这道判据**从没被喂过数据**，只能量结构、量不了账单"
          "（不是它没效果，是没样本）")
band = buy.nlargest(5, "rank")[["code", "name", "close", "板块"]]
band["允许的合法区间(元)"] = (band["close"] * (1 - C.ASHARE_FILL_PRICE_TOL)).round(2).astype(str) \
    + " ~ " + (band["close"] * (1 + C.ASHARE_FILL_PRICE_TOL)).round(2).astype(str)
limit = {"主板": 0.10, "创业板": 0.20, "科创板": 0.20, "北交所": 0.30}
band["该段涨跌停"] = band["板块"].map(limit)
print(band.to_string(index=False))
nb = buy["板块"].value_counts()
print("  名单板块构成：" + "、".join(f"{k} {v}只" for k, v in nb.items()))
n_main = int(nb.get("主板", 0))
n_bj = int(nb.get("北交所", 0))
print(f"  ⇒ 主板 ±10% 比报警带 ±21% 窄：那 {n_main}/{len(buy)} 席的合法价格**永远进不了**"
      f"报警带（这道闸在那里只抓得到打错价，抓不到行情）")
print(f"  ⇒ 北交所 ±30% 比报警带宽：那 {n_bj}/{len(buy)} 席真涨到 +25% 会被当成录入事故报出来"
      f"（假警报），创业板/科创板 ±20% 只剩 1% 容差，是设计时留的那一层")

# ===================== E 仓位层三根（已量档点名 + 前向账本现状） =====================
block("E 仓位层三根：MA 窗 20 / 市场宽度 30%·45% / 年化 252 —— 09-27 已量完，这里只点名")
exp = pd.read_csv(os.path.join(HERE, "tmp_dd_exposure_0927", "exp_compare.csv"))
print("  已量档（`temp/tmp_dd_exposure_0927/exp_compare.csv`，"
      f"{len(exp)} 行，账单在案）：")
print("    " + "、".join(str(x) for x in exp["规则"].tolist()))
led = pd.read_csv(C.ASHARE_EXPOSURE_LEDGER)
MA_WIN = 20            # 与 run_ashare_exposure_monitor.py:61 同一根旋钮（那脚本 import 会跑主流程，故在此现读注释）
print(f"  前向账本 {os.path.basename(C.ASHARE_EXPOSURE_LEDGER)}：{len(led)} 行"
      f"（{led['日期'].min()} ~ {led['日期'].max()}）"
      f"｜M4-20/M2-20 的 {MA_WIN} 日均线还差 {max(0, MA_WIN - len(led))} 场才判得出")
print("  ⚠️ 252 只是「把日收益换算成年化」的除数，不改变任何一笔买卖 ⇒ 不单独立档")

block("汇总")
bl.to_csv(os.path.join(OUT, "buylist_reused.csv"), index=False)
print(f"  产物 {OUT}｜耗时 {time.time() - t0:.0f}s")
if FAIL:
    print("  ❌ 未过：")
    for x in FAIL:
        print("    -", x)
    sys.exit(1)
print("  ✅ 费率模型反解成功（且换错费率对不上）+ 三根执行/仓位阈值读数已出")
