# -*- coding: utf-8 -*-
"""验收 I 格（`signals_daily.csv`／`trades_daily.csv` 塌方闸）的七格夹具 + 拔牙负对照（10-06）

来历：用户 10-06 指出「现在 B 格只看净值行数，signals 少了 1,006 行、trades 少了 2,991 行
没人拦」并裁「补」。I 格的口径写死在 `run_etf_daily_chain.py:709-748`：
**减行本身不是事故**（③ 天然不可幂等，逐日换血是设计内的），只拦两种事故形状——
①覆盖的交易日少一格（尾巴被截／某段崩在半路）；②行数跌破上一场的一半（只剩骨架）。
两张表都没有上一场 ⇒ ⚪ 无基线不拦。

为什么要有这一份：一条新加的拒绝型判据，如果只有"真产物喂进去是绿的"这一条读数，
它既可能是"闸会放"也可能是"闸压根没牙"。所以七格必须**同时**成立：

    C1 换血放行（真字节，signals 掉 1,006 行／trades 掉 2,991 行，日期一张不丢）⇒ ✅
    C2 signals 丢一个交易日（掉最末日那一批）                    ⇒ ❌ 且红在「交易日少 1 格」
    C3 trades 丢一个交易日                                       ⇒ ❌ 且红只念在 trades 那一支
    C4 signals 行数腰斩、4,067 个日期全留（每日只保第一行）        ⇒ ❌ 且红在「跌破一半」
    C5 trades 整张表在 after 里不见了                             ⇒ ❌ 且红在「整张表不见了」
    C6 两张表都没有 before                                        ⇒ ⚪（不许红、也不许绿）
    C7 只有 trades 没有 before                                    ⇒ ✅ 且证据念出「无基线跳过」
       ——这一格钉的是 `len(no_base_i) == 2` 那个 2：写成 `>= 1` 时 C6/C7 都给 ⚪，
         只有 C7 回到 ✅ 才证明这条边界真的在数"两张都缺"而不是"缺了就算"。

三处口径细节，都是为了"读数不是恒真"：
· 输入是**从生产目录 `shutil.copy2` 拷来的真实 CSV 字节**，扰动在 /tmp 的副本上做，
  再让**链路自己那把 `stamp()`** 去读（不是手写 rows/n_dates 凑 dict）⇒ 尺子与真产物同源。
· C2/C3/C4 除了"要红"还钉**红在哪一条**（证据里必须出现那一句、且不出现另外两句）⇒
  防的是"日期分支恒不触发、所有红都由腰斩那一支代劳"这种假绿。
· 拔牙负对照（I-NOOP）：把 I 那一段的累积列表在判读前抹成空表（`bad_i = []`）的**源码副本**
  载进来，拿同一批扰动输入再走一遍 ⇒ C2～C5 必须**全从 ❌ 变回 ✅**。
  没有这一支，"四条红"可能出自别处（比如 A/B 那两格），归因不成立。

生产只读自证：开跑前先记 11 份产物字节的 sha256，收尾后再记一次，只要有一个变了就判红
——这份夹具写的全是 /tmp，不许碰 `etf/v1/data/` 与 `common/data/etf/`。
"""
import hashlib
import importlib.util
import os
import shutil
import sys

import pandas as pd

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(REPO, "etf/v1/src")
sys.path.insert(0, SRC)
os.chdir(SRC)
sys.argv = ["run_etf_daily_chain.py", "--audit"]     # 只 import，不触发 main
import run_etf_daily_chain as chain                   # noqa: E402

T = "/tmp/row_gate_1006"
PRISTINE = os.path.join(T, "pristine")
KEYS = chain.CORE_ARTIFACTS + chain.AUX_ARTIFACTS
PROD_DATA, PROD_BASE = chain.DATA_DIR, chain.BASE_DATA_DIR
SUBJECT = ("results/signals_daily.csv", "results/trades_daily.csv")

FAIL = []


def prod_path(rel):
    root = PROD_BASE if rel.split(os.sep)[0] in ("cache", "risk", "universe_all") else PROD_DATA
    return os.path.join(root, rel)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# 10-06 同批（链路加了 C-1 甲的候选归档，`AUX_ARTIFACTS` 因此多一项）：那一项要**链路真跑一场**
# 才会落盘 ⇒ 夹具不许替它造一份空的冒充生产字节。缺的就明说缺，且只允许缺白名单里那几项。
# 为什么不写死条数：`KEYS` 是从链路那两张清单派生的，写死"11 份"就等于清单再长一项时这里先红
# （或者更糟——用切片把它削掉，那条新加的只会"绿着漏"，10-06 在 H 的开关那格刚踩过）。
_ALLOWED_MISSING = {"archive/official_candidates/index.jsonl"}
_missing = [k for k in KEYS if not os.path.exists(prod_path(k))]
if not set(_missing) <= _ALLOWED_MISSING:
    raise SystemExit(f"[夹具坏了] 生产里缺了不在白名单内的产物：{sorted(set(_missing) - _ALLOWED_MISSING)}"
                     "⇒ 拷贝面自己就不全，别拿它当 pristine")
KEYS = [k for k in KEYS if os.path.exists(prod_path(k))]
print(f"  [夹具盘点] pristine 拷 {len(KEYS)} 份；生产尚未落盘（按白名单放行）：{_missing or '无'}")

PROD_SHA_BEFORE = {k: sha(prod_path(k)) for k in KEYS}
if not all(os.path.exists(prod_path(k)) for k in SUBJECT):
    raise SystemExit("[夹具坏了] 生产目录里两张被测表就不存在 ⇒ 这份夹具没有可拷的字节")


def build_pristine():
    """把生产上存在的每一份产物拷进 pristine（这一步是夹具唯一一次读生产）"""
    os.makedirs(PRISTINE)
    for rel in KEYS:
        dst = os.path.join(PRISTINE, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(prod_path(rel), dst)


def reset(dst):
    """把 pristine 字节拷成一棵 /tmp 产物树（每格一拷，扰动只发生在拷贝上）"""
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(PRISTINE, dst)
    return dst


def stamp_tree(tree):
    """把 chain 的两个根指向这棵树，用**链路自己的 `stamp()`** 读读数"""
    chain.DATA_DIR, chain.BASE_DATA_DIR = tree, tree
    return chain.stamp_all(KEYS)


def date_col(df):
    return pd.to_datetime(df[df.columns[0]], errors="coerce")


def read_write(path, fn):
    df = pd.read_csv(path, encoding="utf-8-sig")
    before_rows, before_dates = len(df), int(date_col(df).dropna().nunique())
    out = fn(df)
    out.to_csv(path, index=False)
    chk = pd.read_csv(path, encoding="utf-8-sig")
    return before_rows, before_dates, len(chk), int(date_col(chk).dropna().nunique())


# ── 三种扰动：都是"在真实字节上删真行"，不手写读数 ──
def churn(n):
    """换血：从最早的、当日还有 ≥2 行的那些交易日各删 1 行，删满 n 行 ⇒ 日期一张不丢"""
    def fn(df):
        d = date_col(df)
        idx, quota = [], n
        for day in sorted(pd.Series(d.dropna()).unique()):
            if len(idx) >= quota:
                break
            rows = list(df.index[d.to_numpy() == day])
            if len(rows) >= 2:
                idx.append(rows[0])
        assert len(idx) == quota, f"可删的「当日≥2行」交易日不足：得 {len(idx)}/{quota}"
        return df.drop(index=idx)
    return fn


def drop_last_date(df):
    d = date_col(df)
    return df[d.to_numpy() != d.max()]


def skeleton(df):
    """只剩骨架：每个交易日保留第一行 ⇒ 行数=n_dates（本表 25,996→4,067），日期全留"""
    d = date_col(df)
    keep = [list(df.index[d.to_numpy() == day])[0] for day in sorted(pd.Series(d.dropna()).unique())]
    return df.loc[keep]


def i_cell(checks):
    hits = [c for c in checks if c[0].startswith("I ")]
    assert len(hits) == 1, f"I 格在验收表里应当恰好一条，实得 {len(hits)} 条（改名/复制了？）"
    return hits[0]


def expect(tag, checks, want_state, must=(), must_not=()):
    name, ok, ev = i_cell(checks)
    bad = []
    if ok is not want_state:
        bad.append(f"三态={ok}（要 {want_state}）")
    for s in must:
        if s not in ev:
            bad.append(f"证据缺「{s}」")
    for s in must_not:
        if s in ev:
            bad.append(f"证据串了「{s}」")
    print(f"  {'✅' if not bad else '❌'} {tag}  三态={ok}")
    print(f"        证据：{ev}")
    if bad:
        print(f"        不合处：{'；'.join(bad)}")
        FAIL.append(tag)


print("\n──── 前置：把生产 11 份产物拷成 pristine（只读拷来）────")
if os.path.exists(T):
    shutil.rmtree(T)
build_pristine()
print(f"  pristine = {PRISTINE}（{len(os.listdir(os.path.join(PRISTINE, 'results')))} 份 results/ 文件）")

BEFORE = stamp_tree(PRISTINE)
b_sig, b_tr = BEFORE["results/signals_daily.csv"], BEFORE["results/trades_daily.csv"]
print(f"  基线读数：signals {b_sig['rows']} 行／{b_sig['n_dates']} 个日期，"
      f"trades {b_tr['rows']} 行／{b_tr['n_dates']} 个日期")

LOG = "  最终资金      : 100.00\n"      # I 格不看日志；其余格子红不红不在本夹具的管辖内


def run_arm(tag, perturb, want_state, must=(), must_not=(), before_tree=None):
    tree = reset(os.path.join(T, "after_" + tag.split()[0]))
    if perturb:
        perturb(tree)
    after = stamp_tree(tree)
    expect(tag, chain.verify_analysis(before_tree or BEFORE, after, 0.0, LOG),
           want_state, must, must_not)
    return after


print("\n──── C1 换血放行：signals 掉 1,006 行、trades 掉 2,991 行（10-05 实测的那两个数），日期一张不丢────")


def p_churn(tree):
    for rel, n in (("results/signals_daily.csv", 1006), ("results/trades_daily.csv", 2991)):
        r0, d0, r1, d1 = read_write(os.path.join(tree, rel), churn(n))
        assert d0 == d1 and r0 - r1 == n, f"扰动自己没按构造生效：{rel} {r0}→{r1} 行、{d0}→{d1} 日期"
        print(f"        {rel}：{r0}→{r1} 行（−{r0 - r1}，−{(r0 - r1) * 100.0 / r0:.2f}%），"
              f"日期 {d0}→{d1} 格")


c1 = run_arm("C1 换血要放行", p_churn, True,
             must=("没撞到「丢交易日」或「行数腰斩」两种事故形状",), must_not=("跌破上一场的一半",))
print(f"  [附] C1 的 after 读数：{c1['results/signals_daily.csv']['rows']} 行／"
      f"{c1['results/signals_daily.csv']['n_dates']} 个日期")

print("\n──── C2 signals 丢一个交易日（删掉最末日那一批真行）────")


def p_sig_date(tree):
    r0, d0, r1, d1 = read_write(os.path.join(tree, "results/signals_daily.csv"), drop_last_date)
    assert d0 - d1 == 1, f"扰动自己没按构造生效：日期 {d0}→{d1}"
    print(f"        signals_daily.csv：{r0}→{r1} 行、日期 {d0}→{d1} 格")


run_arm("C2 signals 丢交易日必须红", p_sig_date, False,
        must=("signals_daily.csv 覆盖的交易日少 1 格",),
        must_not=("跌破上一场的一半", "trades_daily.csv 覆盖的交易日少", "整张表不见了"))

print("\n──── C3 trades 丢一个交易日────")


def p_tr_date(tree):
    r0, d0, r1, d1 = read_write(os.path.join(tree, "results/trades_daily.csv"), drop_last_date)
    assert d0 - d1 == 1, f"扰动自己没按构造生效：日期 {d0}→{d1}"
    print(f"        trades_daily.csv：{r0}→{r1} 行、日期 {d0}→{d1} 格")


run_arm("C3 trades 丢交易日必须红", p_tr_date, False,
        must=("trades_daily.csv 覆盖的交易日少 1 格",),
        must_not=("跌破上一场的一半", "signals_daily.csv 覆盖的交易日少"))

print("\n──── C4 signals 行数腰斩、4,067 个日期全留（每日只保第一行＝只剩骨架）────")


def p_sig_halve(tree):
    r0, d0, r1, d1 = read_write(os.path.join(tree, "results/signals_daily.csv"), skeleton)
    assert d0 == d1 and r1 * 2 < r0, f"扰动自己没按构造生效：{r0}→{r1} 行、{d0}→{d1} 日期"
    print(f"        signals_daily.csv：{r0}→{r1} 行（剩 {r1 * 100.0 / r0:.1f}%）、日期 {d0}→{d1} 格")


run_arm("C4 signals 腰斩必须红", p_sig_halve, False,
        must=("signals_daily.csv 行数跌破上一场的一半",),
        must_not=("覆盖的交易日少", "整张表不见了"))

print("\n──── C5 trades 整张表在 after 里不见了────")


def p_tr_gone(tree):
    os.remove(os.path.join(tree, "results/trades_daily.csv"))


run_arm("C5 trades 缺表必须红", p_tr_gone, False,
        must=("trades_daily.csv 上一场有、本场整张表不见了",),
        must_not=("覆盖的交易日少", "跌破上一场的一半"))

print("\n──── C6 两张表都没有 before（首场／表原本不存在）⇒ ⚪ 只念不拦────")
nb = reset(os.path.join(T, "before_none"))
for rel in SUBJECT:
    os.remove(os.path.join(T, "before_none", rel))
run_arm("C6 无基线必须 ⚪", None, None,
        must=("两张表都没有上一场可比基线",), must_not=("没撞到",),
        before_tree=stamp_tree(nb))

print("\n──── C7 只有 trades 没有 before ⇒ 仍要 ✅，并在证据里念出「无基线跳过」────")
nb1 = reset(os.path.join(T, "before_one"))
os.remove(os.path.join(T, "before_one", "results/trades_daily.csv"))
run_arm("C7 单表无基线必须放行", None, True,
        must=("没撞到", "无基线跳过：trades_daily.csv"),
        must_not=("两张表都没有上一场可比基线",),
        before_tree=stamp_tree(nb1))

print("\n──── I-NOOP 拔牙负对照：把 bad_i 抹空的那份源码副本，同一批扰动输入必须全变回 ✅────")
# 上面 C2～C5 的四条红到底出自 I 那一段，还是出自别的格子顺手念的？拿"只改那一行"的副本对拍。
src_path = os.path.abspath(chain.__file__)
with open(src_path, encoding="utf-8") as f:
    src = f.read()
anchor = "\n    if bad_i:\n        i_ok, i_ev = False, "
assert src.count(anchor) == 1, f"锚点出现 {src.count(anchor)} 次（应为 1）⇒ I 那段改形了，拔牙支失效"
noop_path = os.path.join(T, "chain_noop.py")
with open(noop_path, "w", encoding="utf-8") as f:
    f.write(src.replace(anchor, "\n    bad_i = []      # 拔牙插件（10-06 夹具）\n" + anchor[1:]))
spec = importlib.util.spec_from_file_location("chain_noop_1006", noop_path)
chain_noop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain_noop)
print(f"  副本已加载（与原文件行数 {src.count(chr(10))} → "
      f"{open(noop_path, encoding='utf-8').read().count(chr(10))}，只多那一行插件）")

arm_after = {tag: stamp_tree(os.path.join(T, "after_" + tag.split()[0]))
             for tag in ("C2 signals 丢交易日必须红", "C3 trades 丢交易日必须红",
                         "C4 signals 腰斩必须红", "C5 trades 缺表必须红")}
before_noop = stamp_tree(PRISTINE)
for tag in ("C2 signals 丢交易日必须红", "C3 trades 丢交易日必须红",
            "C4 signals 腰斩必须红", "C5 trades 缺表必须红"):
    got = i_cell(chain_noop.verify_analysis(before_noop, arm_after[tag], 0.0, LOG))
    ok = got[1] is True and "没撞到" in got[2]
    print(f"  {'✅' if ok else '❌'} {tag}（拔了牙的那一份）三态={got[1]}")
    print(f"        证据：{got[2]}")
    if not ok:
        FAIL.append(f"拔牙 {tag}")

print("\n──── 生产只读自证：11 份产物字节的 sha256 开跑前后必须逐一相同────")
diff = [k for k in KEYS if sha(prod_path(k)) != PROD_SHA_BEFORE[k]]
print(f"  {'✅' if not diff else '❌'} 生产字节未被本夹具改动（比对 {len(KEYS)} 份，变化 {len(diff)} 份）")
if diff:
    FAIL.append(f"生产被写：{diff}")

print("\n──── 判定 ────")
if FAIL:
    for t in FAIL:
        print(f"  ❌ {t}")
    raise SystemExit(f"[夹具失败] {len(FAIL)} 处 ⇒ I 格不是"
                     "「放行 1／判红 4／⚪ 1／边界 1」那副形状，别接进链路")
shutil.rmtree(T, ignore_errors=True)
print("[夹具通过] 七格各归各位：C1 换血放行｜C2/C3 丢交易日红（且红只在各自那一张表）｜"
      "C4 腰斩红（日期一张没少）｜C5 缺表红｜C6 两表无基线 ⚪｜C7 单表无基线仍放行；"
      "拔牙副本把四条红全变回 ✅ ⇒ 那些红确实出自 I 那一段；生产 11 份字节逐位未动")
