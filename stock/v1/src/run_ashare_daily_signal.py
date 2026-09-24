# -*- coding: utf-8 -*-
"""股票线日频信号：今天哪些票**不该买**，以及该先看哪几只（待买入短名单）。

定位：收盘后跑一次，出两份名单，人工照着下单。**不做盘中分析**——判据全部来自
已落库的日线面板，盘中价一行都不读；跑早了拿到的是昨天的面板，还不如不跑。

1) **剔除名单**（`signal_YYYYMMDD.csv`）——量能族四条构造取并集，踢掉当日截面分位
   ≥ 阈值的那批。这是本系统载荷最硬的结论：截面 IC 为负（放量/爆量是反向指标），
   「把最响的 20% 从可投池里踢掉」相对同域等权池给：单条最佳 +4.36%/年
   （STD(Volume,5)，量能水平/波动三条落在 +3.89%~+4.36%），而**与量能无关的价格
   水平对照只有 +1.69%**；四条取并集值 **+6.46%/年**
   （`data/results/ashare_portfolio_exclusion.csv`，行内 `gate` 列自报档位，现在是
   `board`）。这条判据扛住了护栏 / 闸门 / 成交量基准三次口径变动（最佳单条增益依次
   0.0450 → 0.0436 → 0.0418），⑲ 又加了第四次 = 涨停闸档 flat→board→dated
   （0.0436 → 0.0437 → 0.0437，等于没动）。
   这份 CSV 每行多一列 `n_hit`（当日有几条构造一致判响），因为下一条用的是**另一个
   强度**：并集只管「不该买」这张域（≥1 条即剔），待买入名单的入口闸要
   ≥ `ASHARE_BUY_MIN_HITS`（默认 3）条一致判响才挡。这不是把同一条判据调松，
   是 09-24 的 ⑮P0 量出来的两个用途各自的最优强度——同一张并集掩码压在**当期排序轴**
   （换轴前的 STD(Volume,20)）上会把 +0.95% 打成 -0.39%（换手 0.31→0.48，
   费用 2.49pp 吃掉毛收益 1.15pp），
   而它在减法腿上仍值 +6.46%/年。判据与来路见 config 里那段的注释。
2) **待买入短名单**（`buy_YYYYMMDD.csv`）——待买入域内（闸门 + 未达共识剔除）按
   「安静度」= `ts_mean(volume,20)` 升序取前 ASHARE_BUY_TOP_N 名，等权。
   这根轴 **09-24 换过**（用户裁决）：换之前是 `ts_std(volume,20)`（量能波动），
   现在是量能水平 —— 也就是说它与剔除用的 `level` 那条**同一个表达式**，
   高分端（最热）从域里踢掉、低分端（最冷）排在前面买。
   证据强度比第 1 条低一档，说清楚在哪：
   组合层 39 行（13 构造 × top50/100/200）多头回放中位超额 **-0.98%**、20 行为负，
   也就是说「做多低量能侧」整体不成立；量能族那 24 行里只有 7 行为正，其中 6 行
   就是同簇那两条构造（本轴与旧轴，spearman 0.93~0.95）各三档，剩 1 行是
   SMA(Volume,10)@200 那格 +0.26%：
   本轴 SMA(Volume,20) 三档 = +1.39%/+1.59%/+1.58%、IR +0.11~+0.17、单程换手
   0.162~0.186；旧轴 STD(Volume,20) = +0.95%/+2.31%/+3.70%、换手 0.257~0.311。
   换轴换的是**同一根信号取哪个代理**，不增信息；取水平的理由是在生产名单那一档
   （top50）三项全胜、换手只有旧轴的 60%，且 2021 之后六年 4/6 为正（旧轴 2/6、
   六年均值 -0.7%/年 —— 旧轴的中位 +5.6% 几乎全来自 2015~2020）。代价也说清：
   全窗口 top100/200 两档仍是旧轴更高，所以**放大持仓数要重量这条**。
   而且它与同表的「低价股」对照分不开（对照 MA(Price,5) 三档 +3.56%/+2.73%/+2.18%、
   换手只有 0.08~0.10，换轴之后**三档全部**盖过排序轴）。所以这份名单是**沿用了那条
   被验证过的选票动作**（闸门 + 低分侧 top_n），不是新造的组合，但它样本内、
   未过准入链 —— 叫「先人工核这一批」，不叫买入信号。
   名单上再叠三道纯执行性硬闸：当日无成交/停牌、当日收盘已贴涨停（阈值口径由
   `ASHARE_TRADABLE_GATE` 一个开关说话，三档：`flat` = 全线 0.095；`board` = 自己板块
   的限幅 × ASHARE_LIMIT_NEAR，即主板 9.5% / 科创·创业 19% / 北交所 28.5%，**默认，也是
   实盘该用的那一档**；`dated` = 同 board 但按**当天生效**的限幅取档 —— 它给回测复现
   历史规则用，日频只有「今天」这一档规则，所以 dated 在日频侧与 board 逐字相同）、
   以及 ST/*ST（判据来自当日收盘快照的「名称」列，无快照则该闸不跑并在
   stats 里如实记）。
3) **下单名单**（`order_YYYYMMDD.csv`，09-24 的 B 路）——从上面那份 50 只观察名单里
   按名次挑 `ASHARE_ORDER_TOP_N`（默认 5）只真下单，每只按**槽位**给权 1/top_n。
   这一层**不新造 alpha、不改排序轴**，只加两条分散约束：同一实体行业 ≤
   `ASHARE_ORDER_MAX_PER_INDUSTRY`、同一板块 ≤ `ASHARE_ORDER_MAX_PER_BOARD`。
   约束是**分散度**不是白名单：个人账户科创板/创业板/北交所都有权限，任何一段都不拉黑
   （行业未知的票因此**不参与**行业去重，否则映射缺口 85% 的北交所会被变相排除）。
   凑不满就少买、把 shortfall 报出来，不临时放宽判据。行业列来自
   `data/fetch_industry_map.py` 落的外部映射，缺表/过期时这一条约束自动失效并如实记
   `meta.industry.loaded=false`（与 ST 闸同一降级姿势，日频链路不因此中断）。

口径全部复用 strategy/ashare_screen.py（与回测入口同一份实现），此处只做五件事：
1) 取面板最后一个交易日（或 STOCK_SIGNAL_DATE 指定的历史日）为信号日 s；
2) 跑闸门 + 剔除，逐条构造给出当日截面分位。启用哪几条由 STOCK_SCREEN_RULES 说话
   （默认四条量能构造；`lib:"<因子库 name>"` 可把 RD-Agent 回收的某条表达式提名进来）；
3) 在保留池内排待买入短名单（含三道执行性硬闸与计数），再按两条分散约束裁出下单名单；
4) 报因子库 ↔ 盘前判据的接点：库里 21 条表达式，哪几条就是现在在跑的判据、
   哪几条还进不了名单。这一段是**说明**不是准入 —— 判准入要看
   ashare_factor_eval.csv 的全市场截面 IC 和组合层回放，factors.json 里那四个
   IC 是沙箱模型预测值，冒充不了证据；
5) 两份名单各落一份 CSV 到 ASHARE_SIGNAL_DIR，外加一份 meta_YYYYMMDD.json（当日
   各环节计数 + 启用的构造 + 因子库接点，看板直接读它，不在看板里重算第二口径），
   并打印汇总。

时序上的一个诚实缺口：信号日若是面板最后一天，第二天开盘价还不存在，
「涨停买不进」这道闸没法前瞻（tradable_mask 的 d1=None 分支），只能到第二天
开盘前人工确认——这与本系统「出信号 → 人工下单」的形态正好吻合。指定历史日
则会补上这道闸，可用于事后核对某天的名单到底长什么样。

只读行情，不写因子库、不下单、不触发 git 提交。
"""

import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import json
import os
import sys
import time

import pandas as pd

from config import (ASHARE_BUY_TOP_N, ASHARE_BUY_MIN_HITS, ASHARE_FACTORS_JSON,
                    ASHARE_PORT_MIN_AMOUNT, ASHARE_SCREEN_QUANTILE,
                    ASHARE_SIGNAL_DIR, ASHARE_SNAPSHOT_DIR, ASHARE_ORDER_TOP_N,
                    ASHARE_TRADABLE_GATE,
                    LOT_SIZE)
from ashare_screen import (BUY_EXPR, BUY_NAME, INDUSTRY_UNKNOWN, VOLUME_RULES,
                           active_rules, build_matrices, factor_matrices, gate_desc,
                           library_screen_link, load_industry_map, load_panel,
                           order_candidates, screen_on_date)

SIGNAL_DATE = os.environ.get("STOCK_SIGNAL_DATE", "")   # 空 = 面板最后一天


def pick_date(index):
    """信号日与它的下一个交易日；下一个不存在就交 None 让人工确认涨停"""
    if SIGNAL_DATE:
        d = pd.Timestamp(SIGNAL_DATE)
        if d not in index:
            raise SystemExit(f"[信号] STOCK_SIGNAL_DATE={d.date()} 不在面板交易日里，"
                             f"面板区间 {index[0].date()} ~ {index[-1].date()}")
        i = index.get_loc(d)
    else:
        i = len(index) - 1
    d1 = index[i + 1] if i + 1 < len(index) else None
    return index[i], d1


def spot_labels(s):
    """当日收盘快照里的 (代码→名称, ST/*ST 代码集)；没有快照就返回两个空并如实报

    面板本身没有股票名称字段（只有行情列），ST 标记只能从 akshare 收盘快照的「名称」
    列拿，而那份 CSV 由 data/update_qlib_bin_daily.py 在 append 当天落盘。
    匹配按**大小写敏感**：ST 标记在名称里一律大写，写成 case-insensitive 会把
    带小写字母的简称一起捞进来，那是错的判据。
    """
    path = os.path.join(ASHARE_SNAPSHOT_DIR, f"spot_{s:%Y%m%d}.csv")
    if not os.path.exists(path):
        print(f"[待买入] 无 {path}：名称列留空，ST 这道闸今日未跑（stats.st_checked=False）")
        return {}, set()
    sp = pd.read_csv(path, encoding="utf-8-sig")
    codes = sp["代码"].astype(str).str[:2].str.upper() + sp["代码"].astype(str).str[2:]
    nm = sp["名称"].astype(str)
    st = set(codes[nm.str.contains("ST")])
    print(f"[待买入] 快照 {os.path.basename(path)}：{len(codes)} 只带名称，"
          f"其中名称含 ST 标记 {len(st)} 只")
    return dict(zip(codes, nm)), st


def main():
    t0 = time.time()
    rules = active_rules()        # STOCK_SCREEN_RULES 在这里生效（含 lib:<因子库名> 提名）
    wide, _bench = load_panel()
    mtx = build_matrices(wide)
    del wide
    # 启用的剔除构造 + 排序轴 + 闸门基准一次求值：同一份 factor_matrices、同一套
    # $volume 口径，否则「剔谁」与「买谁」会踩在两套成交量定义上。
    # 闸门基准恒为「量能水平」那条（VOLUME_RULES[0]），不随 STOCK_SCREEN_RULES
    # 收窄而换 —— 否则关掉一条构造会连带把闸门挪到别的分布上，池子口径就漂了。
    exprs = {e for _k, _n, e, _d in rules} | {BUY_EXPR, VOLUME_RULES[0][2]}
    rule_mats = factor_matrices(sorted(exprs), mtx)
    s, d1 = pick_date(mtx["close"].index)
    print(f"[信号] 信号日 {s.date()}，下一交易日 "
          + (f"{d1.date()}（涨停闸生效）" if d1 is not None else "未到（涨停闸留给人工）"))
    print(f"[筛选] 启用构造 {len(rules)} 条：" + "、".join(n for _k, n, _e, _d in rules))

    gate = rule_mats[VOLUME_RULES[0][2]]          # 闸门用「量能水平」那条判因子有值
    names, st_codes = spot_labels(s)
    r = screen_on_date(s, d1, mtx, rule_mats, gate, ASHARE_SCREEN_QUANTILE,
                       top_n=ASHARE_BUY_TOP_N, st_codes=st_codes,
                       buy_min_hits=ASHARE_BUY_MIN_HITS)
    detail, keep, universe = r["detail"], r["keep"], r["universe"]

    # 收盘价与成交额只为人读盘服务，不参与任何筛选判据，故在入口侧补进明细，
    # 不塞进 ashare_screen（那里只放规则）。close 用 raw_price（盘面真实报价）而非
    # 面板复权价：人工下单要能跟券商行情对上，茅台 09-22 复权 304.93 / 盘面 1253.79。
    # amount20_yi 是真钱（元 → 亿）：09-23 探针把它对回新浪自报成交额，逐票比值
    # 0.99~1.01（判据见 ashare_screen 模块 docstring「数据口径」段）。之前那一版
    # （盘面价×$volume）虚高 25 倍，已修正。
    detail = detail.copy()
    detail.insert(0, "close", mtx["raw_price"].loc[s].reindex(detail.index))
    detail.insert(1, "amount20_yi",
                  mtx["amount20"].loc[s].reindex(detail.index) / 1e8)   # 亿元，人工读盘方便
    out = detail.reset_index(names="code")
    os.makedirs(ASHARE_SIGNAL_DIR, exist_ok=True)
    path = os.path.join(ASHARE_SIGNAL_DIR, f"signal_{s:%Y%m%d}.csv")
    out.to_csv(path, index=False)

    print(f"\n===== {s.date()} 剔除名单（阈值 pct >= {ASHARE_SCREEN_QUANTILE}，"
          f"启用构造取并集；待买入那道闸另按 ≥{ASHARE_BUY_MIN_HITS} 条一致判响） =====")
    print(f"过闸门可投池 {int(universe.sum())} 只　"
          f"被量能族剔除 {int(r['dropped'].sum())} 只 "
          f"({r['dropped'].sum() / max(universe.sum(), 1):.1%})　保留 {int(keep.sum())} 只")
    print(f"涨停剔除（仅历史日可判）{r['n_limit_up']} 只"
          f"　涨停闸口径 {gate_desc()}")

    print("\n逐条构造命中（池内，分位 >= 阈值即踢）：")
    rule_hits = []
    for _k, cn, _e, doc in rules:
        hit = {"key": _k, "name": cn, "expr": _e, "fired": None}
        rule_hits.append(hit)
        if cn not in detail.columns:
            print(f"  {cn} 当日无值（表达式不可求值或整列缺数据）　｜ {doc}")
            continue
        fired = detail[cn] >= ASHARE_SCREEN_QUANTILE
        hit["fired"] = int(fired.sum())
        print(f"  {cn} 踢 {hit['fired']:4d} 只　"
              f"命中组当日成交额中位 {detail.loc[fired, 'amount20_yi'].median():.2f} 亿 vs "
              f"保留组 {detail.loc[~fired & detail[cn].notna(), 'amount20_yi'].median():.2f} 亿"
              f"　｜ {doc}")
    # 因子库 ↔ 盘前判据 的接点：库里的表达式哪几条正是上面在跑的判据、哪几条没进。
    # 这一段只是**说明现状**，不构成准入 —— 库里那些条目的全市场截面 IC 在
    # ashare_factor_eval.csv，沙箱 IC（factors.json 里那四个数）不可当准入用
    link = library_screen_link()
    in_rules = {r["expr"] for r in rule_hits} | {BUY_EXPR}
    linked = [x for x in link if x["role"] != "未启用"]
    print(f"\n===== 因子库接点（{os.path.basename(os.path.dirname(ASHARE_FACTORS_JSON))}/factors.json"
          f" 共 {len(link)} 条） =====")
    for x in linked:
        print(f"  {x['role']}[{x['rule_name']}] ← {x['name']}  = {x['expr']}")
    n_unused = len(link) - len(linked)
    print(f"  未进判据 {n_unused} 条；提名方式：STOCK_SCREEN_RULES=...,"
          f"lib:\"<因子库 name>\"（会进剔除并集、改变保留池，须先看它的截面 IC 与组合层回放）")
    # 保留池按 20 日均额降序给个「先看得到的」顺序。这**不是**买入排序：它是池内
    # 容量排序，给人核对闸门用；真正的买入排队在下面的 buy_ 名单里，排序轴只有一根
    head = out[out["keep"]].sort_values("amount20_yi", ascending=False)
    print(f"\n保留池前 20（按 20 日均成交额降序，只为可读，**非买入排序**）：")
    cols = ["code", "close", "amount20_yi", "keep"]
    print(head[cols].head(20).to_string(index=False))

    # ---------- 待买入短名单 ----------
    buy = r["buy"].copy()
    bs = r["buy_stats"]
    buy.insert(1, "name", [names.get(c, "") for c in buy.index])
    buy.insert(2, "close", mtx["raw_price"].loc[s].reindex(buy.index))
    buy.insert(3, "amount20_yi", mtx["amount20"].loc[s].reindex(buy.index) / 1e8)
    # 一手金额 = 盘面价 × LOT_SIZE：等权买不买得下来由这个数说话，不靠人猜
    buy.insert(4, "lot_value_yuan", mtx["raw_price"].loc[s].reindex(buy.index) * LOT_SIZE)
    # 行业/板块：板块由代码前缀判（面板自带，永不变，且限幅就按它分档）；行业来自
    # 外部映射落盘表，缺表/过期时整列落「未知」——那是**约束失效**的标记，不是「无风险」
    ind_map, ind_meta = load_industry_map()
    if not ind_meta["loaded"]:
        print(f"[下单层] 行业映射不可用（{ind_meta.get('reason')}）："
              f"行业去重这一条今天不生效，板块去重照常")
    else:
        print(f"[下单层] 行业映射 {os.path.basename(ind_meta['path'])}："
              f"{ind_meta['n']} 只有行业、抓取日 {ind_meta['fetched']}、"
              f"{ind_meta['age_days']} 天前"
              + (f" ⚠️ {ind_meta['reason']}" if ind_meta["stale"] else ""))
    buy.insert(5, "行业", [str(ind_map.get(c, "")).strip() or INDUSTRY_UNKNOWN
                          for c in buy.index])
    buy.insert(5, "板块", buy.pop("板块"))          # 板块挨着行业放，读盘时一眼配成对
    n_unk = int((buy["行业"] == INDUSTRY_UNKNOWN).sum())
    print(f"[待买入] 50 只里行业未知 {n_unk} 只（这些票的行业约束不生效，看板上标出来）")
    path_buy = os.path.join(ASHARE_SIGNAL_DIR, f"buy_{s:%Y%m%d}.csv")
    buy.reset_index(names="code").to_csv(path_buy, index=False)

    n = len(buy)
    print(f"\n===== {s.date()} 待买入短名单（待买入域内 {BUY_NAME} = {BUY_EXPR} 升序，"
          f"前 {n} 名，等权 {1 / max(n, 1):.1%}） =====")
    # 两道剔除阈值今日的实际差：并集（≥1 条判响即剔）只管「不该买」那张域，
    # 名单入口用的是「≥ ASHARE_BUY_MIN_HITS 条一致判响才挡」。不打印出来，
    # 下一次有人拿两份名单逐行对账就会当成 bug 报回来
    print(f"剔除强度：本名单用「命中 ≥{bs['buy_min_hits']} 条构造」才挡，"
          f"今日被这道闸挡住 {bs['n_consensus']} 只；"
          f"另有 {bs['n_lenient_vs_union']} 只虽在「不该买」并集域内被剔、"
          f"但未达共识故放行（域口径 ≥1 条，见上一节）")
    print(f"候选漏斗：闸门内且未达共识剔除 {bs['n_step1']} → 当日有成交 {bs['n_traded']} "
          f"→ 非近涨停 {bs['n_traded'] - bs['n_chase']} → 非 ST {bs['n_cand']}"
          f"（剔 ST {bs['n_st']} 只{'' if bs['st_checked'] else '，今日无快照未跑'}）"
          f"　昨收取不到而放过 {bs['n_chg_unknown']} 只")
    print(f"入线阈值：第 {n} 名的 {BUY_NAME} = {bs['quiet_cut']:.4g}"
          f"　与组合层选票口径的对账：那一层只过闸门就取低分侧 top_n，"
          f"本名单多跑三道执行闸后有 {bs['n_diff_vs_backtest']} 只不同")
    print(buy[["name", "close", "amount20_yi", "lot_value_yuan", BUY_NAME,
               "安静度分位", "当日涨幅", "listed_days", "weight"]].to_string())
    print(f"\n⚠️ 排序轴的证据强度（读一遍再下单）：组合层 39 行多头回放中位超额 -0.98%、"
          f"20 行为负，量能族那 24 行里只有 7 行为正（本轴与换轴前旧轴各三档 = 6 行，"
          f"spearman 0.93~0.95；剩 1 行是 SMA(Volume,10)@200 的 +0.26%）；"
          f"本轴 {BUY_EXPR} = SMA(Volume,20) 三档 "
          f"+1.39%/+1.59%/+1.58% @ top50/100/200、IR +0.11~+0.17、单程换手 0.162~0.186。"
          f"**09-24 换轴**（用户裁决）：换掉的旧轴 STD(Volume,20) 是 +0.95%/+2.31%/+3.70%、"
          f"换手 0.257~0.311 —— top100/200 两档仍归旧轴，放大持仓数要重量本轴；"
          f"换过来的理由是 top50（= 本名单那一档）三项全胜，且 2021 起六年 4/6 为正"
          f"（旧轴 2/6、六年均值 -0.7%/年，旧轴中位 +5.6% 几乎全来自 2015~2020）；"
          f"与同表「低价股」对照分不开（MA(Price,5) 三档 +3.56%/+2.73%/+2.18%、"
          f"换手只有 0.08~0.10，换轴后三档全部盖过本轴）。"
          f"本轴与剔除用的 `level` 是**同一个表达式**（高分端踢出域、低分端买）⇒ 这根轴"
          f"一旦失效，域和名单同时坏。"
          f"样本内、未过准入链 —— 它是「先人工核这一批」的排队顺序，不是收益承诺。"
          f"　（以上数字来自 `board` 档归档表 `ashare_portfolio_eval.csv`，"
          f"核对请查行内 `expr` 列 = `{BUY_EXPR}`；本次运行档位 = "
          f"`{ASHARE_TRADABLE_GATE}`）")

    # ---------- 下单名单（B 路：观察名单 → 仓位表，不加判据） ----------
    order, ost = order_candidates(buy, ind_map, ASHARE_ORDER_TOP_N)
    # 名字/盘面价/一手金额/安静度从观察名单那几列带过来，不另算第二口径
    od = order.copy()
    od["name"] = [names.get(c, "") for c in od.index]
    od["close"] = mtx["raw_price"].loc[s].reindex(od.index)
    od["lot_value_yuan"] = od["close"] * LOT_SIZE
    od[BUY_NAME] = buy[BUY_NAME].reindex(od.index)
    path_order = os.path.join(ASHARE_SIGNAL_DIR, f"order_{s:%Y%m%d}.csv")
    od.reset_index(names="code").to_csv(path_order, index=False)
    print(f"\n===== {s.date()} 下单名单（观察名单 {n} 只按名次取前 {ASHARE_ORDER_TOP_N} 只，"
          f"同行业 ≤{ost['max_per_industry']}、同板块 ≤{ost['max_per_board']}）=====")
    print(od[["obs_rank", "name", "板块", "行业", "close", "lot_value_yuan",
              BUY_NAME, "weight"]].to_string())
    print(f"仓位：{ost['n_picked']} 只 × 每槽 {1 / ASHARE_ORDER_TOP_N:.0%} = "
          f"{ost['weight_sum']:.0%}，其余留现金"
          + (f"　⚠️ 只凑到 {ost['n_picked']} 只（shortfall {ost['shortfall']}）："
             f"放宽约束等于在本层改判据，所以宁可少买" if ost["shortfall"] else ""))
    if ost["shortfall"]:
        # 凑不满有两种：约束真的挡完了，或 cap 定得比名单构成还紧（后者是参数错，
        # 不是市场没给票）—— 把名单自己的板块分布打出来，让人一眼分得开
        print(f"  观察名单板块构成 {ost['n_board_in_list']}（每段上限 "
              f"{ost['max_per_board']}）⇒ 缺口来自名单太偏还是约束太严，照这个数判")
    if ost["n_skipped"]:
        print(f"  被分散约束跳过 {ost['n_skipped']} 只："
              + "、".join(f"{x['code']}(第{x['rank']}名 {x['reason']})"
                          for x in ost["skipped"][:8]))

    # 运行概览另落一份 JSON：上面那些计数只打在终端的话，看板就只能重算一遍才能
    # 显示——而重算是本系统禁止的第二口径。名单 CSV 保持「一行一标的」的干净形状，
    # 计数走这份 meta
    meta = {"signal_date": str(s.date()),
            "next_trade_day": None if d1 is None else str(d1.date()),
            "panel_end": str(mtx["close"].index[-1].date()),
            "quantile": ASHARE_SCREEN_QUANTILE,
            # 口径自报：换 STOCK_TRADABLE_GATE 重跑过的名单与旧名单不是一回事，
            # 事后对账只能从这份 meta 认
            "tradable_gate": ASHARE_TRADABLE_GATE,
            # 两套剔除强度分开记：看板的「不该买」页读 quantile，「待买入」页读这个
            "buy_min_hits": ASHARE_BUY_MIN_HITS,
            "universe": int(universe.sum()), "dropped": int(r["dropped"].sum()),
            "keep": int(keep.sum()), "n_limit_up": r["n_limit_up"],
            # 今天到底跑了哪几条剔除构造、各踢了多少 —— 看板直接读，不自己拼规则表
            "screen_rules": rule_hits,
            # 因子库与盘前判据的接点（按表达式匹配）。linked = 库里哪几条就是现在的
            # 判据；unused = 库里有但没进判据的。看板据此标注「已准入/仅在册」，
            # 不再让 factors.json 的沙箱 IC 冒充准入证据
            "library": {"path": ASHARE_FACTORS_JSON, "n": len(link),
                        "linked": [{k: x[k] for k in ("name", "expr", "role",
                                                      "rule_key", "rule_name")}
                                   for x in linked],
                        "unused": [{"name": x["name"], "expr": x["expr"],
                                    "dsl_ok": x["dsl_ok"]}
                                   for x in link if x["role"] == "未启用"]},
            "buy": {**bs, "expr": BUY_EXPR, "top_n_requested": ASHARE_BUY_TOP_N},
            # 下单层与它依赖的行业映射：看板读这份就够，不在页面里重算约束（第二口径）
            "order": ost,
            "industry": {k: ind_meta[k] for k in
                         ("path", "loaded", "n", "age_days", "stale", "fetched")}
            | {"reason": ind_meta.get("reason", ""),
               "n_unknown_in_buylist": n_unk}}
    meta_path = os.path.join(ASHARE_SIGNAL_DIR, f"meta_{s:%Y%m%d}.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)

    print(f"\n[输出] {path}（{len(out)} 行）")
    print(f"[输出] {path_buy}（{n} 行，观察名单）")
    print(f"[输出] {path_order}（{ost['n_picked']} 行，下单名单）")
    print(f"[输出] {meta_path}")
    print(f"[耗时] {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
