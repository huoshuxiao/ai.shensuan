# -*- coding: utf-8 -*-
"""T3b/T4 一趟面板：量**名单形状**与**闸门/成本/护栏**这几根人工阈值各吃掉多少。

为什么要读面板：这两组的旋钮作用在「5000 只排完序取前 50」和「今天能不能买到」上，
落盘的 daily_signal 只有选完之后的结果，没有第 51 名以后的候选 ⇒ 离线量不了。

量的六根（**读数全部由生产函数给出，本脚本不重抄任何一道判据**）：
  ASHARE_BUY_TOP_N=50            名单长度            buy_candidates → apply_board_quota
  ASHARE_LIST_QUOTA=20/10/10/10  板块席位            apply_board_quota（改模块全局）
  ASHARE_PORT_MIN_LISTED=60      次新闸              tradable_mask 第 2 道
  ASHARE_PORT_MIN_AMOUNT=2e7     容量闸              tradable_mask 第 3 道
  RET_LIMIT=0.30                 假台阶收益护栏      guard_ret / build_matrices
  ASHARE_LIMIT_NEAR=0.95         贴涨停裕度          tradable_mask 第 4 道 + buy_candidates 第 3 道

档位怎么拨：`patched(...)` 改的是 ashare_screen 模块全局（生产函数在调用时现读这些名字），
所以档位走的是同一条代码路径。⚠️ 一个必须踩掉的坑：`gate_vector` 按 columns **对象身份**
缓存阈值向量，而向量里的每个阈值都已经乘过 `ASHARE_LIMIT_NEAR` ⇒ 只改全局不清缓存，
H 那三档会拿到同一份旧阈值、读数恒等（这就是"报了还 exit 0"那一型），所以 patch_near 里
连 `_GATE_VEC` / `_GATE_DATED` 一起清。

内存：`load_panel` 自己就把切片钉在 START−暖机（约 2014-07），本脚本再切一次是同一把尺子；
真正的峰值来自「读整张面板 + 逐票求值表达式」，与生产 ③ 同一条路径（③ 实测 75s/2.07GB），
**不拿环 2 的 5.01GB 当本趟的估计**——本趟跑完打印自己的峰值 RSS。
**只读生产件，所有产物写进 stock/v1/temp/**。`--anchor` = 丁模式，只跑到锚点为止。

拆件必须等价（本趟自己的锚点，C 块）：名单层在档位之间复用「过闸池 + 待买入入口」，
这两件用生产的 `tradable_mask` / `volume_exclusion` / `buy_extra_block` 现算，再把结果
喂给 `buy_candidates`；生产档下这条路必须与 `screen_on_date(with_buy=True)` 的名单
**逐只逐名次相同** ⇒ 拆错了就不是拆错了，是整张档位表作废。

判据不恒真：
  ① 锚点 = 复现落盘的 daily_signal/meta_20260928.json **八个数**（universe/dropped/keep/
     n_limit_up 四个总数 + 四条量能构造各自的剔量），再用故意改错的刀口做一次反证（必须对不上）
  ② 每一根旋钮都要给出「比生产更严 ⇒ 留得更少」的单调性，不单调就是尺子坏了。
  ③ 极端档：TOP_N=0 必须空名单；MIN_LISTED / MIN_AMOUNT 拨到 +inf 必须把池清成 0、
     拨到闸底（0 与 −inf）必须与"这道闸不存在"同数；「席位设 0」的档里席位腿必须一只不吃，
     且每场「四段构成相加 = 名单全长」（G 块另记「想补几只」与「真补进去几只」的差）。
  ④ 护栏那块给两条对照：裁完之后「复权越界而盘面正常」这一型必须清零（漏咬就红），
     且这 90 格里确实有格被压回界内（一行没改就是装饰）；两侧同时越界的真行情**不该**被裁。
  ⑤ 名单形状那两根旋钮（F/G/I）必须真的换人：生产档的名单与「全给主板」「均分」
     「只给主板 20」「旧口径 global」四档在 569 场里都必须至少有一场不同。
     **裕度（H）不进这条判红**：它判的是「当天涨得贴近涨停」，名单按「近 20 日成交量最小」
     排，被挡的票在轴的远端、挤不进前 50 是常态（㊽ 全历史实测只换过 0.82% 的席位）；
     这根旋钮的牙改为直接比 `gate_vector` 的阈值向量必须随档位变（H 块「接线有牙」），
     名单换几只只报读数。
"""
import json
import os
import resource
import sys
import time
from contextlib import contextmanager

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(HERE, "tmp_shape_gates_0929")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "stock", "v1", "src"))
import _bootstrap  # noqa: E402,F401
import config as C  # noqa: E402
from strategy import ashare_screen as S  # noqa: E402

FAIL = []
ANCHOR_DAY = "2026-09-28"
# 丁模式：只跑到锚点为止（A 护栏 + B 复现 09-28 那场），不铺 570 场网格。
# 为什么单独开这一档：锚点那一趟求值的表达式集合与生产 ③ 是**同一个子集**
# （③ 实测 75s / 2.07GB），而档位表那几块要多存三份 (场×票) 数组并把 570 场跑满。
ANCHOR_ONLY = "--anchor" in sys.argv


def peak_gib():
    """本进程峰值 RSS（GiB）；下一趟该起多大的判断依据只能来自实测，不能靠环 2 的数"""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20


@contextmanager
def patched(**kw):
    """把 ashare_screen 的模块全局换成某一档，退出时原样装回。

    生产函数（`tradable_mask` / `apply_board_quota` / `gate_vector`）都是**调用时现读**
    这些名字，所以改这里就等于改 config 的那一行——本脚本因此一行判据都不重抄。
    """
    old = {k: getattr(S, k) for k in kw}
    for k, v in kw.items():
        setattr(S, k, v)
    if "ASHARE_LIMIT_NEAR" in kw:          # 见模块 docstring：阈值向量已经乘过裕度
        S._GATE_VEC.clear()
        S._GATE_DATED.clear()
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(S, k, v)
        if "ASHARE_LIMIT_NEAR" in kw:
            S._GATE_VEC.clear()
            S._GATE_DATED.clear()


def block(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


t0 = time.time()
rules = S.active_rules()
exprs = sorted({e for _k, _n, e, _d in rules} | {S.BUY_EXPR})
print(f"[装载] 判据构造 {len(rules)} 条 + 排序轴 {S.BUY_EXPR} ⇒ 求值 {len(exprs)} 条表达式", flush=True)
wide, _bench = S.load_panel()
# 留一份 float64 的复权因子：护栏的判据吃的是 `open / factor`，而 mtx["raw_price"]
# 已经是 float32（生产为了省内存故意折的），拿它反推因子会在 0.30 这条界上差最后一位
fac64 = wide["factor"].astype("float64")
mtx = S.build_matrices(wide)
del wide
f = mtx["ret_open"]
print(f"[装载] 面板 {f.shape[0]} 天 × {f.shape[1]} 只　{time.time() - t0:.0f}s", flush=True)

block("A RET_LIMIT=0.30（假台阶护栏）：全切片到底咬过几格")
# 护栏在 build_matrices 里面已经咬过了，mtx["ret_open"] 是**裁完之后**的那张，
# 所以要自己把裁之前那两张拿出来：复权开盘收益 pre、盘面开盘收益 raw
pre = mtx["open"].astype("float64").pct_change(fill_method=None).to_numpy()
raw = (mtx["open"].astype("float64") / fac64).pct_change(fill_method=None).to_numpy()
ret_np = f.to_numpy()
n_valid = int(np.isfinite(pre).sum())
for lim in (0.20, 0.25, S.RET_LIMIT, 0.40, 0.60):
    n = int(((np.abs(pre) > lim) & (np.abs(raw) <= lim)).sum())
    print(f"  复权越界而盘面正常 ⇒ 护栏咬：|复权开盘收益| > {lim:.2f} 且 |盘面| ≤ {lim:.2f}"
          f" 共 {n:,} 格（占有效格 {n / n_valid:.4%}）"
          f"{'  ←生产' if abs(lim - S.RET_LIMIT) < 1e-9 else ''}")
fake = (np.abs(pre) > S.RET_LIMIT) & (np.abs(raw) <= S.RET_LIMIT)
n_fake = int(fake.sum())
flip = int((fake & (np.sign(pre) != np.sign(ret_np))).sum())
print(f"  裁回 ±{S.RET_LIMIT} 不改符号：被裁的 {n_fake:,} 格里裁前裁后符号不同的 {flip:,} 格")
# 有牙：护栏的活是「复权越界而盘面正常」那一型，裁完之后这类必须清零；
# 两侧同时越界（真涨停 / 注册制新股首周）是**故意留着的真行情**，不算漏咬
resid = int(((np.abs(ret_np) > S.RET_LIMIT) & (np.abs(raw) <= S.RET_LIMIT)).sum())
both = int(((np.abs(ret_np) > S.RET_LIMIT) & (np.abs(raw) > S.RET_LIMIT)).sum())
print(f"  有牙：裁完之后「复权越界而盘面正常」还剩 {resid:,} 格 ⇒ "
      f"{'✅ 这一型清零了' if resid == 0 else '❌ 护栏漏咬'}"
      f"｜另一型「两侧同时越界」留 {both:,} 格（真行情，护栏按设计不动它）")
if resid:
    FAIL.append(f"A 有牙失败：护栏裁完仍剩 {resid} 格「复权越界而盘面正常」")
# 第二条对照：这 90 格里有多少真被压回界内（=0 就说明护栏一行没动，是装饰）
leak = int(((np.abs(pre) > S.RET_LIMIT) & (np.abs(raw) <= S.RET_LIMIT)
            & (np.abs(ret_np) <= S.RET_LIMIT)).sum())
print(f"  对照：这 {n_fake} 格里被裁到 ±{S.RET_LIMIT} 之内的 {leak:,} 格"
      f"（= 护栏真的动过手；若为 0 说明它一行都没改）")
if n_fake and leak == 0:
    FAIL.append("A 对照失败：号称裁了 90 格却没有一格被压回界内 ⇒ 护栏是装饰")
del pre, raw, ret_np, fac64

live = mtx["ret_open"].index >= pd.Timestamp(os.environ.get("SMOKE_LIVE_FROM", "2014-07-01"))
mtx = {k: v.loc[mtx["ret_open"].index[live]] for k, v in mtx.items()}
fms = S.factor_matrices(exprs, mtx)
days = mtx["ret_open"].index
cols = list(mtx["close"].columns)
print(f"[装载] 切完 {mtx['ret_open'].shape}　求值 {time.time() - t0:.0f}s", flush=True)

block("B 锚点：复现 " + ANCHOR_DAY + " 那场日更（`screen_on_date` 本体，判据一律走生产函数）")
s = pd.Timestamp(ANCHOR_DAY)
r = S.screen_on_date(s, None, mtx, fms, fms[rules[0][2]],
                     quantile=C.ASHARE_SCREEN_QUANTILE, top_n=C.ASHARE_BUY_TOP_N,
                     buy_min_hits=C.ASHARE_BUY_MIN_HITS, with_buy=False)
meta = json.load(open(os.path.join(REPO, "stock", "v1", "data", "results",
                                   "daily_signal", "meta_20260928.json")))
print(f"  口径先对表：meta 记 quantile={meta['quantile']}/gate={meta['tradable_gate']}/"
      f"next_trade_day={meta['next_trade_day']}｜本进程现读 "
      f"quantile={C.ASHARE_SCREEN_QUANTILE}/gate={C.ASHARE_TRADABLE_GATE}/"
      f"暖机起点={str(mtx['ret_open'].index[0].date())}")
got = {"universe": int(r["universe"].sum()), "dropped": int(r["dropped"].sum()),
       "keep": int(r["keep"].sum()), "n_limit_up": int(r["n_limit_up"])}
want = {k: int(meta[k]) for k in ("universe", "dropped", "keep", "n_limit_up")}
print(f"  复算 {got}")
print(f"  meta {want}　{'✅ 逐位相同' if got == want else '❌ 对不上'}")
if got != want:
    FAIL.append(f"B 锚点复现失败（{got} vs {want}）⇒ 本脚本所有档位表作废")
# 第二条锚：逐条构造各剔几只（4 个数），比总数更难蒙对
mine_fired = {cn: int((r["detail"][cn].notna() & (r["detail"][cn] >= meta["quantile"])).sum())
              for _k, cn, _e, _d in rules if cn in r["detail"].columns}
meta_fired = {it["name"]: int(it["fired"]) for it in meta["screen_rules"]}
same = mine_fired == meta_fired
print(f"  逐条剔量 复算 {mine_fired}")
print(f"  逐条剔量 meta {meta_fired}　{'✅ 四条全对' if same else '❌ 有差'}")
if not same:
    FAIL.append("B 第二条锚（逐条构造剔量）对不上 ⇒ 分位口径与生产不同版")
bad = S.screen_on_date(s, None, mtx, fms, fms[rules[0][2]], quantile=0.75,
                       top_n=C.ASHARE_BUY_TOP_N, with_buy=False)
n75 = int(bad["dropped"].sum())
print(f"  反证：故意把刀口写成 0.75 ⇒ 剔 {n75:,} 只（meta 记的 0.80 是 {want['dropped']:,}），"
      f"必须不等 ⇒ {'✅ 有牙' if n75 != want['dropped'] else '❌ 恒真'}")
if n75 == want["dropped"]:
    FAIL.append("B 反证失败：换刀口读数不变 ⇒ 锚点是恒真装饰")

if ANCHOR_ONLY:
    block("丁模式收尾：锚点到为止，不铺 570 场网格")
    print(f"  峰值 RSS {peak_gib():.2f} GiB｜耗时 {time.time() - t0:.0f}s"
          f"｜送验表达式 {len(exprs)} 条（生产 ③ 是它的超集：还多带闸门基准与独立闸）")
    if FAIL:
        print("  ❌ 未过：" + "；".join(FAIL))
        sys.exit(1)
    print("  ✅ 锚点逐位复现 + 换刀口反证有牙（档位表未跑，归 甲/乙/丙）")
    sys.exit(0)

def same_val(a, b):
    """NaN 与 NaN 算相同（`quiet_cut` 在空名单那一场就是 NaN，两边都是 NaN 不是走样）"""
    if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        return True
    return a == b


def same_stats(a, b):
    return all(same_val(a[k], b[k]) for k in a) and set(a) == set(b)


def entry_of(sd, d1):
    """生产口径的「过闸池 + 待买入入口」：四道闸、共识强度、独立闸一律走生产函数本体

    为什么要单独拆出来：F/G 那两根旋钮（名单长度、板块席位）作用在入口**下游**，
    570 场 × 若干档如果每档都从 screen_on_date 重算一遍分位，等于把最贵的一步重复付费。
    拆件是否等价由 C 块逐场证明（与 screen_on_date 的名单比逐只逐名次）。
    """
    pool, n_limit = S.tradable_mask(sd, d1, gate_mat, mtx)
    _keep, detail = S.volume_exclusion(fms, sd, pool, C.ASHARE_SCREEN_QUANTILE)
    n_hit = detail["n_hit"].reindex(pool.index).fillna(0)
    keep_buy = pool & (n_hit < C.ASHARE_BUY_MIN_HITS)
    blocked, _fired = S.buy_extra_block(fms, sd, pool)
    return pool, keep_buy & ~blocked, int(n_limit)


hold = C.ASHARE_PORT_HOLD
rebal = list(range(0, len(days) - hold - 1, hold))
if os.environ.get("SMOKE_GRID"):                     # 只给自检用：截短网格，先验证脚本自己跑得通
    rebal = [i for i in rebal if i % int(os.environ["SMOKE_GRID"]) == 0][:12]
gate_mat = fms[rules[0][2]]
# 网格与环 2 同一张（`range(0, len(days)-hold-1, hold)`），第 1 场正好落在面板首日。
# 首日没有昨收 ⇒ `buy_candidates` 直接 SystemExit（生产的护栏，不是本脚本的坑），
# 所以**闸门/池**那两块（D/E）走满 570 场，**名单形状**那几块（F/G/H/I）走 569 场。
GRID_BUY = [(kk, i) for kk, i in enumerate(rebal) if i > 0]
NG, NB = len(rebal), len(GRID_BUY)
HMID = NB // 2
print(f"[网格] hold={hold} ⇒ 池/闸 {NG} 场｜名单形状 {NB} 场（首日 {days[rebal[0]].date()} 无昨收，"
      f"只进池不进名单）｜{days[rebal[0]].date()} ~ {days[rebal[-1]].date()}｜票 {len(cols)} 只", flush=True)

block("C 拆件锚点：逐场算「池 + 入口」，并证明它喂给 buy_candidates 就是 screen_on_date 那份名单")
t_c = time.time()
pools, entries, prod_head, n_pool_prod = [], [], [], []
n_diff_head = n_diff_stats = n_diff_pool = 0
DIFF_STATS = []
for kk, i in enumerate(rebal):
    sd, d1 = days[i], days[i + 1]
    can_buy = i > 0
    r = S.screen_on_date(sd, d1, mtx, fms, gate_mat,
                         quantile=C.ASHARE_SCREEN_QUANTILE, top_n=C.ASHARE_BUY_TOP_N,
                         buy_min_hits=C.ASHARE_BUY_MIN_HITS, with_buy=can_buy)
    pool, keep_buy, n_lu = entry_of(sd, d1)
    if int(pool.sum()) != int(r["universe"].sum()) or n_lu != int(r["n_limit_up"]):
        n_diff_pool += 1
    if can_buy:
        b, st = S.buy_candidates(sd, mtx, fms, pool, keep_buy, top_n=C.ASHARE_BUY_TOP_N)
        if list(b.index) != list(r["buy"].index):
            n_diff_head += 1
        ref = {k: v for k, v in r["buy_stats"].items() if k in st}
        if st != ref and not same_stats(st, ref):
            n_diff_stats += 1
            for k in st:                        # NaN != NaN 也算差，先把差在哪个键上打出来
                if st[k] != ref[k]:
                    DIFF_STATS.append(f"{sd.date()} {k}: 拆件 {st[k]!r} vs 生产 {ref[k]!r}")
        prod_head.append(list(b.index))
    else:
        prod_head.append(None)
    pools.append(pool)
    entries.append(keep_buy)
    n_pool_prod.append(int(pool.sum()))
    if kk % 100 == 0:
        print(f"  [{kk + 1:>3}/{NG}] {sd.date()} 池 {int(pool.sum()):>5,}"
              f"｜入口 {int(keep_buy.sum()):>5,}｜名单 {len(prod_head[-1] or [])} 只"
              f"｜已用 {time.time() - t_c:.0f}s", flush=True)
print(f"  逐场对账（池/闸 {NG} 场全查、名单 {NB} 场全查，不是抽几场）：名单不同 {n_diff_head} 场"
      f"｜形状账不同 {n_diff_stats} 场｜池/涨停剔除数不同 {n_diff_pool} 场 ⇒ "
      f"{'✅ 拆件与生产同一条路' if n_diff_head + n_diff_stats + n_diff_pool == 0 else '❌ 拆件走样，档位表作废'}")
if n_diff_head or n_diff_stats or n_diff_pool:
    FAIL.append(f"C 拆件不等价（名单差 {n_diff_head} 场 / 形状账差 {n_diff_stats} 场 / "
                f"池差 {n_diff_pool} 场）⇒ D~I 所有档位表作废")
print(f"  生产档日均：过闸池 {np.mean(n_pool_prod):,.0f} 只｜待买入入口 "
      f"{np.mean([int(x.sum()) for x in entries]):,.0f} 只｜名单 {C.ASHARE_BUY_TOP_N} 只"
      f"｜C 块耗时 {time.time() - t_c:.0f}s", flush=True)

RUNGS = {}


def ladder(tag, rows, cols_out):
    pd.DataFrame(rows).to_csv(os.path.join(OUT, f"{tag}.csv"), index=False)
    RUNGS[tag] = rows
    print(f"  → {tag}.csv（{len(rows)} 档｜列 {cols_out}）")


block("D 次新闸 ASHARE_PORT_MIN_LISTED=" + str(C.ASHARE_PORT_MIN_LISTED) +
      "：往两侧各挪一档，并把这道闸开到底/关到底（走 tradable_mask 本体）")
RUNGS_D = [0, 30, C.ASHARE_PORT_MIN_LISTED, 120, 250, 10 ** 9]
rows_d = []
for t in RUNGS_D:
    with patched(ASHARE_PORT_MIN_LISTED=t):
        n = [int(S.tradable_mask(days[i], days[i + 1], gate_mat, mtx)[0].sum()) for i in rebal]
    rows_d.append({"min_listed_days": t, "pool_mean": float(np.mean(n)),
                   "pool_p05": float(np.percentile(n, 5)), "pool_min": int(np.min(n))})
    tag = "  ←生产" if t == C.ASHARE_PORT_MIN_LISTED else ("  ←闸开到底" if t == 0 else
           ("  ←闸关到底" if t == 10 ** 9 else ""))
    print(f"  上市天数 ≥{t:>11,} 天：日均过闸 {rows_d[-1]['pool_mean']:>6,.0f} 只"
          f"｜最冷一场 {rows_d[-1]['pool_min']:>5,}｜比第 0 档少挡/多挡 "
          f"{rows_d[-1]['pool_mean'] - rows_d[0]['pool_mean']:>+8,.0f}{tag}", flush=True)
pm = [x["pool_mean"] for x in rows_d]
mono_d = all(a >= b for a, b in zip(pm, pm[1:]))
print(f"  单调性（阈值越高压的越多）：{'✅' if mono_d else '❌ 不单调 ⇒ 尺子坏了'}"
      f"｜闸关到底池子 {pm[-1]:,.0f} 只（必须 0）"
      f"｜闸开到底 {pm[0]:,.0f} 只 = 这道闸全历史每天平均挡掉 {pm[0] - pm[2]:,.0f} 只")
if not mono_d:
    FAIL.append("D 不单调：次新闸拨严了池子反而更大")
if pm[-1] != 0:
    FAIL.append(f"D 极端档失败：MIN_LISTED=10^9 竟还剩 {pm[-1]:,.0f} 只 ⇒ 这道闸没在读 listed_days")
ladder("d_min_listed", rows_d, ["pool_mean", "pool_p05", "pool_min"])

block("E 容量闸 ASHARE_PORT_MIN_AMOUNT=" + f"{C.ASHARE_PORT_MIN_AMOUNT:.3g}" +
      "：真钱口径，从「闸不存在」铺到「谁都别买」")
RUNGS_E = [-np.inf, 0.0, 5e6, C.ASHARE_PORT_MIN_AMOUNT, 5e7, 1e8, 2e8, np.inf]
rows_e = []
for t in RUNGS_E:
    with patched(ASHARE_PORT_MIN_AMOUNT=t):
        n = [int(S.tradable_mask(days[i], days[i + 1], gate_mat, mtx)[0].sum()) for i in rebal]
    rows_e.append({"min_amount": t, "pool_mean": float(np.mean(n)), "pool_min": int(np.min(n))})
    tag = ("  ←生产" if t == C.ASHARE_PORT_MIN_AMOUNT else
           ("  ←闸开到底（连 0 成交都不挡）" if t == -np.inf else
            ("  ←闸关到底" if t == np.inf else "")))
    print(f"  20 日均额 ≥{t:>13,.0f}：日均过闸 {rows_e[-1]['pool_mean']:>6,.0f} 只"
          f"｜最冷一场 {rows_e[-1]['pool_min']:>5,}｜比底档少 "
          f"{rows_e[0]['pool_mean'] - rows_e[-1]['pool_mean']:>8,.0f}{tag}", flush=True)
pe = [x["pool_mean"] for x in rows_e]
mono_e = all(a >= b for a, b in zip(pe, pe[1:]))
print(f"  单调性：{'✅' if mono_e else '❌'}｜这道闸在生产档每天挡掉 {pe[0] - pe[3]:,.0f} 只"
      f"｜闸底两档（−inf vs 0）差 {pe[0] - pe[1]:,.0f} 只（= 日均额恰好为 0 的那批）")
if not mono_e:
    FAIL.append("E 不单调：容量闸拨严了池子反而更大")
if pe[-1] != 0:
    FAIL.append(f"E 极端档失败：MIN_AMOUNT=+inf 竟还剩 {pe[-1]:,.0f} 只")
mid_i = rebal[NG // 2]
amt = mtx["amount20"].loc[days[mid_i]].to_numpy(dtype="float64")
q = np.nanquantile(amt, [0.01, 0.05, 0.10, 0.25, 0.50])
print(f"  参考分布（{days[mid_i].date()} 全市场 20 日均额）：p1={q[0] / 1e7:.2f}千万 "
      f"p5={q[1] / 1e7:.2f}千万 p10={q[2] / 1e7:.2f}千万 p25={q[3] / 1e7:.2f}千万 "
      f"中位={q[4] / 1e7:.1f}千万")
ladder("e_min_amount", rows_e, ["pool_mean", "pool_min"])

block("F 名单长度 ASHARE_BUY_TOP_N=" + str(C.ASHARE_BUY_TOP_N) +
      "：席位凑满率、回填只数，以及「加长名单到底加进谁」")
RUNGS_F = [0, 10, 20, C.ASHARE_BUY_TOP_N, 100, 200, 400]
heads_f, rows_f = {}, []
for n in RUNGS_F:
    ln, short, back, cand, fills, hh = [], 0, [], [], [], []
    for kk, i in GRID_BUY:
        b, st = S.buy_candidates(days[i], mtx, fms, pools[kk], entries[kk], top_n=n)
        ln.append(len(b))
        back.append(st["list"]["n_backfilled"])
        cand.append(st["n_cand"])
        if st["list"]["n_short"] > 0:
            short += 1
        fills.append(sum(1 for bq in st["list"]["quota"]
                         if st["list"]["n_leg_by_board"].get(bq, 0) >= st["list"]["quota"][bq]))
        hh.append(list(b.index))
    heads_f[n] = hh
    tag = "  ←生产" if n == C.ASHARE_BUY_TOP_N else ("  ←必须空名单" if n == 0 else "")
    print(f"  名单 {n:>4} 只：实际给出 日均 {np.mean(ln):>6.1f}｜凑不满的场次 {short}/{NB}"
          f"｜回填 日均 {np.mean(back):>5.2f} 只｜入口候选 日均 {np.mean(cand):,.0f}"
          f"｜四段席位填满的段数 日均 {np.mean(fills):.2f}/4{tag}", flush=True)
    rows_f.append({"top_n": n, "list_mean": float(np.mean(ln)), "n_short_days": int(short),
                   "backfill_mean": float(np.mean(back)), "cand_mean": float(np.mean(cand)),
                   "legs_full_mean": float(np.mean(fills))})
pf = [x["list_mean"] for x in rows_f]
mono_f = all(a <= b for a, b in zip(pf, pf[1:]))
contain = all(set(heads_f[C.ASHARE_BUY_TOP_N][kk]) <= set(heads_f[100][kk])
              for kk in range(NB))
print(f"  单调性（名单越长给得越多）：{'✅' if mono_f else '❌'}"
      f"｜TOP_N=0 给出 {pf[0]:.0f} 只（必须 0）"
      f"｜50 名是不是 100 名的子集：{'✅ 是 ⇒ 名单从 50 放到 100 不新增候选，只把回填那一截摊开' if contain else '❌ 否'}")
if not mono_f:
    FAIL.append("F 不单调：名单加长反而给得更少")
if pf[0] != 0:
    FAIL.append(f"F 极端档失败：TOP_N=0 竟然给出 {pf[0]:.0f} 只")
if not contain:
    FAIL.append("F 机制读数存疑：生产 50 席不是 100 席的子集 ⇒ 席位扫法与理解不同")
ladder("f_top_n", rows_f, ["list_mean", "n_short_days", "backfill_mean", "cand_mean", "legs_full_mean"])

block("G 板块席位 ASHARE_LIST_QUOTA：改分配不改总长度（含旧口径 global 那一行）")
BASE_Q = dict(S.ASHARE_LIST_QUOTA)
LADS = {
    "生产 20/10/10/10": {"主板": 20, "创业板": 10, "科创板": 10, "北交所": 10},
    "全给主板 50/0/0/0": {"主板": 50, "创业板": 0, "科创板": 0, "北交所": 0},
    "均分 13/13/12/12": {"主板": 13, "创业板": 13, "科创板": 12, "北交所": 12},
    "只给主板 20（其余 0）": {"主板": 20, "创业板": 0, "科创板": 0, "北交所": 0},
}
heads_g, rows_g = {}, []
G_RUNGS = list(LADS.items()) + [("旧口径 global（不设席位）", None)]
for lab, qv in G_RUNGS:
    back, main, other, ln, hh, short = [], [], [], [], [], 0
    final_other, close_ratio, identity = [], [], []
    for kk, i in GRID_BUY:
        if qv is None:
            with patched(ASHARE_LIST_SCHEME="global"):
                b, st = S.buy_candidates(days[i], mtx, fms, pools[kk], entries[kk],
                                         top_n=C.ASHARE_BUY_TOP_N)
        else:
            with patched(ASHARE_LIST_QUOTA=qv):
                b, st = S.buy_candidates(days[i], mtx, fms, pools[kk], entries[kk],
                                         top_n=C.ASHARE_BUY_TOP_N)
        lst = st["list"]
        nb = lst["n_by_board"]
        # `n_backfilled` 是「打算补几只」（= top_n − 席位腿取到的），名单凑不满时它比
        # **真补进去的**大 ⇒ 回填归因要用「最终构成 − 席位腿」，两者之差恰好是 n_short。
        # 这条恒等式逐场查（下面 identity 计数），空名单那一场（供给为 0）跳过闭合比。
        leg = lst["n_leg_by_board"]
        seat_main = leg.get("主板", 0)
        seat_other = sum(v for k2, v in leg.items() if k2 != "主板")
        real_back = len(b) - (seat_main + seat_other)
        main.append(seat_main)
        other.append(seat_other)
        final_other.append(sum(v for k2, v in nb.items() if k2 != "主板"))
        close_ratio.append(sum(nb.values()) / len(b) if len(b) else np.nan)
        identity.append(1 if lst["n_backfilled"] - real_back == lst["n_short"] else 0)
        back.append(lst["n_backfilled"])
        ln.append(len(b))
        hh.append(list(b.index))
        if lst["n_short"] > 0:
            short += 1
    heads_g[lab] = hh
    seats = sum(qv.values()) if qv is not None else 0
    n_empty = sum(1 for x in ln if x == 0)
    print(f"  {lab:<22}：名单 日均 {np.mean(ln):>5.2f} 只｜凑不满 {short}/{NB} 场"
          f"（其中空名单 {n_empty} 场）｜席位腿主板 {np.mean(main):>5.2f}"
          f"｜席位腿三段 {np.mean(other):>5.2f}｜席位腿合计 {np.mean(main) + np.mean(other):.2f}/{seats}"
          f"｜最终构成非主板 {np.mean(final_other):.2f}"
          f"｜想补 {np.mean(back):.2f}｜构成闭合 {np.nanmean(close_ratio):.4f}"
          f"｜回填恒等式成立 {sum(identity)}/{NB} 场", flush=True)
    rows_g.append({"rung": lab, "list_mean": float(np.mean(ln)), "n_short": int(short),
                   "n_empty": int(n_empty), "backfill_mean": float(np.mean(back)),
                   "main_mean": float(np.mean(main)), "other_mean": float(np.mean(other)),
                   "final_other_mean": float(np.mean(final_other)),
                   "close_mean": float(np.nanmean(close_ratio)),
                   "n_identity": int(sum(identity))})
only_main = [x for x in rows_g if x["rung"] in ("全给主板 50/0/0/0", "只给主板 20（其余 0）")]
for kv in only_main:
    print(f"  有牙（席位真的是顶）：{kv['rung']} 档｜席位腿主板 {kv['main_mean']:.2f} 只"
          f"｜席位腿非主板 {kv['other_mean']:.2f} 只｜名单 日均 {kv['list_mean']:.2f} 只"
          f"｜最终构成非主板 {kv['final_other_mean']:.2f} 只（全来自回填）"
          f"｜想补 {kv['backfill_mean']:.2f}｜构成闭合 {kv['close_mean']:.4f}"
          f"｜回填恒等式 {kv['n_identity']}/{NB} 场")
    if abs(kv["close_mean"] - 1.0) > 1e-9:
        FAIL.append(f"G 构成不闭合：{kv['rung']} 的分段相加 {kv['close_mean']:.4f} ≠ 名单全长")
    if kv["n_identity"] != NB:
        FAIL.append(f"G 回填口径不闭合：{kv['rung']} 只有 {kv['n_identity']}/{NB} 场满足"
                    f"「想补 − 真补 = 缺的席位」⇒ 回填的账读不准")
    if kv["other_mean"] > 1e-9:
        FAIL.append(f"G 有牙失败：{kv['rung']} 档席位腿还吃进 {kv['other_mean']:.2f} 只非主板"
                    f" ⇒ 0 席位等于没设闸")
ladder("g_list_quota", rows_g, ["list_mean", "backfill_mean", "main_mean", "other_mean"])

block("H 贴涨停裕度 ASHARE_LIMIT_NEAR=" + str(C.ASHARE_LIMIT_NEAR) +
      "：两道闸分开记账（㊽ 已量过全历史账单，这里补「名单到底换几只」）")
RUNGS_H = [0.90, C.ASHARE_LIMIT_NEAR, 0.99]
heads_h, rows_h = {}, []


def gate_vec(near):
    with patched(ASHARE_LIMIT_NEAR=near):
        gv = S.gate_vector(mtx["open"].columns)      # flat 档返回标量、board/dated 返回向量
    return np.asarray(gv if np.isscalar(gv) else gv.to_numpy(), dtype="float64")


vec90, vec99 = gate_vec(0.90), gate_vec(0.99)
for near in RUNGS_H:
    pn, nlu, nch, ln, hh = [], [], [], [], []
    for kk, i in enumerate(rebal):
        sd, d1 = days[i], days[i + 1]
        with patched(ASHARE_LIMIT_NEAR=near):
            pool, keep_buy, n_lu = entry_of(sd, d1)
            pn.append(int(pool.sum()))
            nlu.append(n_lu)
            if i > 0:                        # 首日无昨收 ⇒ 只进池/闸读数，不进名单读数
                b, st = S.buy_candidates(sd, mtx, fms, pool, keep_buy,
                                         top_n=C.ASHARE_BUY_TOP_N)
                nch.append(st["n_chase"])
                ln.append(len(b))
                hh.append(list(b.index))
    heads_h[near] = hh
    tag = "  ←生产" if abs(near - C.ASHARE_LIMIT_NEAR) < 1e-12 else ""
    print(f"  裕度 {near}：日均过闸（{NG} 场）{np.mean(pn):>6,.0f} 只"
          f"｜建仓日开盘跳升被挡（回测第 4 道） 日均 {np.mean(nlu):>5.2f}"
          f"｜当日收盘已贴板被挡（名单第 3 道，{NB} 场） 日均 {np.mean(nch):>5.2f}"
          f"｜名单 日均 {np.mean(ln):.2f} 只{tag}", flush=True)
    rows_h.append({"limit_near": near, "pool_mean": float(np.mean(pn)),
                   "n_limit_d1_mean": float(np.mean(nlu)), "n_chase_mean": float(np.mean(nch)),
                   "list_mean": float(np.mean(ln))})
ph = [x["pool_mean"] for x in rows_h]
mono_h = all(a <= b for a, b in zip(ph, ph[1:]))
pl = [x["n_limit_d1_mean"] for x in rows_h]
mono_h2 = all(a >= b for a, b in zip(pl, pl[1:]))
print(f"  单调性：池随裕度放宽变大 {'✅' if mono_h else '❌'}"
      f"｜建仓日被挡只数随裕度放宽变少 {'✅' if mono_h2 else '❌'}"
      f"｜接线有牙：0.90 档阈值 {vec90[0]:.4f} vs 0.99 档 {vec99[0]:.4f}"
      f"（比值 {np.nanmax(vec99 / vec90):.3f}，必须 = 0.99/0.90 = {0.99 / 0.90:.3f}）")
if not mono_h:
    FAIL.append("H 不单调：裕度放宽后池子反而更小 ⇒ 阈值缓存没清（见模块 docstring 的坑）")
if not mono_h2:
    FAIL.append("H 第 4 道闸不单调：裕度放宽后被挡只数反而更多")
if abs(float(np.nanmax(vec99 / vec90)) - 0.99 / 0.90) > 1e-12:
    FAIL.append("H 接线有牙失败：gate_vector 的阈值没跟着 ASHARE_LIMIT_NEAR 走")
ladder("h_limit_near", rows_h, ["pool_mean", "n_limit_d1_mean", "n_chase_mean", "list_mean"])

block(f"I 有牙：五张档位表必须真的换人（{NB} 场逐个比，不是抽一场）")
BUY_DAYS = [days[i] for _kk, i in GRID_BUY]
comps = [
    ("生产配额 vs 全给主板", heads_g["生产 20/10/10/10"], heads_g["全给主板 50/0/0/0"], True),
    ("生产配额 vs 均分", heads_g["生产 20/10/10/10"], heads_g["均分 13/13/12/12"], True),
    ("生产配额 vs 只给主板 20", heads_g["生产 20/10/10/10"], heads_g["只给主板 20（其余 0）"], True),
    ("配额档 vs 旧口径 global", heads_g["生产 20/10/10/10"], heads_g["旧口径 global（不设席位）"], True),
    # 裕度那两行**只报读数不判红**：闸判的是「当天涨得贴近涨停」，而名单按「近 20 日
    # 成交量最小」排 ⇒ 被挡的票本来就在轴的远端，挤不进前 50 是常态不是尺子坏了。
    # 这根旋钮的牙在上面的「接线有牙」（gate_vector 阈值随档变）与 H 的 n_chase/n_limit。
    (f"裕度 {C.ASHARE_LIMIT_NEAR} vs 0.90", heads_h[C.ASHARE_LIMIT_NEAR], heads_h[0.90], False),
    (f"裕度 {C.ASHARE_LIMIT_NEAR} vs 0.99", heads_h[C.ASHARE_LIMIT_NEAR], heads_h[0.99], False),
]
for lab, ha, hb, must in comps:
    diff_days = sum(1 for kk in range(NB) if ha[kk] != hb[kk])
    inter = len(set(ha[HMID]) & set(hb[HMID]))
    print(f"  {lab:<28}：换人的场次 {diff_days:>3}/{NB}（{diff_days / NB:>5.1%}）"
          f"｜中间那场（{BUY_DAYS[HMID].date()}）共同席位 {inter}/{len(ha[HMID])}"
          f"{'' if must else '　（只报读数）'}")
    if must and diff_days == 0:
        FAIL.append(f"I 有牙失败：{lab} {NB} 场名单一只没变 ⇒ 这根旋钮是装饰")
diff50 = sum(1 for kk in range(NB)
             if set(heads_f[C.ASHARE_BUY_TOP_N][kk]) != set(heads_f[20][kk]))
print(f"  名单 {C.ASHARE_BUY_TOP_N} 只 vs 20 只：换人的场次 {diff50}/{NB}（TOP_N 这根旋钮的牙）")
if diff50 == 0:
    FAIL.append("I 有牙失败：TOP_N 从 50 压到 20 名单居然一样")
pd.DataFrame({"date": [str(days[i].date()) for i in rebal],
              "pool_n_prod": n_pool_prod,
              "entry_n": [int(x.sum()) for x in entries],
              "list_n": [len(h) if h is not None else -1 for h in prod_head]}).to_csv(
    os.path.join(OUT, "grid_by_day.csv"), index=False)
print(f"  逐场明细 → {OUT}/grid_by_day.csv（{len(rebal)} 行）")

block("汇总")
print(f"  总耗时 {time.time() - t0:.0f}s｜峰值 RSS {peak_gib():.2f} GiB｜产物 {OUT}")
if FAIL:
    print("  ❌ 未过：")
    for x in FAIL:
        print("    -", x)
    sys.exit(1)
print("  ✅ 锚点复现 + 拆件等价 + 五根旋钮的档位表已出（六根里 RET_LIMIT 在 A 块、刀口在 B 块）")
