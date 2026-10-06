# -*- coding: utf-8 -*-
"""C-1 两半的夹具（10-06 用户裁「第①步：丙＝两半同批／第②步：只报」）

吃的是链路入口模块**自己**的 `verify_analysis` / `print_checks` /
`official_candidates_snapshot` / `factor_library_keys` / `archive_official_candidates`
（判据本体不在这份文件里重写 ⇒ 与链路同一把尺，链路口径一改这里必须响）。

28 格分三组：
  乙（J 格）  G1 放行 / G2 判红 / G2b 判红但不进红名单 / G3＋G3b 只报≠没渲染 /
    G4 候选净增 0 个名字 / G5–G7 三种缺取数 / G8 接线（十条 A–J）/ G9a–G9c 拔牙
  取数       G10a–G10c 生产 factors.json 与库索引真读得出（正对照）/
    G11a–G11c 指到空处必须是 None（noop，防的就是"数不到却报有"）
  甲（归档）  A1a–A1c 落盘逐位相同 / A2 同 sha 不重写 / A3a–A3c 换内容只追加不覆写 /
    A4 生产目录字节指纹前后一致（A4b 是它的牙：多一枚文件必须被看见）

⚠️ G2b 与 G3b 是这一批最要紧的两支：「只报」有两种坏法——把红糊成 ⚪（等于没这一格），
和把红照收进红名单（就成了拦停，与裁令相反）。G2b 钉死后一种，G3b 钉死前一种。
⚠️ G9a 那句"除 J 外都不许变"按**判据名摘掉 J** 再逐条比，不用切片（10-06 的教训：
条数再长一行只会"绿着漏"），G9c 再喂一片人造的「I 被顺手动了」证这把比尺看得见差异。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

SRC = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git/etf/v1/src"
sys.path.insert(0, SRC)
os.chdir(SRC)
sys.argv = ["run_etf_daily_chain.py", "--audit"]
import run_etf_daily_chain as chain                   # noqa: E402  (必须先：它 import _bootstrap)
import config                                         # noqa: E402
import core.factor_library as flib                    # noqa: E402

CORE, AUX, T_START = chain.CORE_ARTIFACTS, chain.AUX_ARTIFACTS, 1_000.0
results = []


def mk(rows=100, last_date="2026-09-28", last_value=100.0, mtime=2_000.0,
       nbytes=1000):
    return {"exists": True, "mtime": mtime, "bytes": nbytes,
            "when": "01-01 00:00:00", "rows": rows, "last_date": last_date,
            "last_value": last_value, "n_dates": rows}


def good_pair():
    before = {k: mk(mtime=500.0, rows=90, last_date="2026-09-25") for k in CORE + AUX}
    after = {k: mk(mtime=2_000.0, rows=100) for k in CORE + AUX}
    return before, after


def arm(tag, want, got, note=""):
    ok = (got == want)
    results.append((tag, ok))
    print(f"  {'✅' if ok else '❌'} {tag:<44} 实得={got!r} 要求={want!r}  {note}")


# 产出行 + 「本场净增 2」那行 ⇒ G 与 H 都绿，剩下的动静才只可能是 J 自己的。
LOG = ("  最终资金      : 100.00\n"
       "  ✅ official 本场净增 2 个因子\n"
       "  ✅ official   产出 9 个因子\n  多源挖掘追加: 2\n")
LOG_BAD_D = LOG.replace("100.00", "4321.00")
B, A = good_pair()
OLD = ("alpha_a", "alpha_b")
NEW = ("alpha_a", "alpha_b", "alpha_c")     # 本场回收层点名的净增＝alpha_c


def bridge(before=OLD, after=NEW, keys=NEW, keys_before=OLD, archive=None):
    """给 J 的取数。三样里任何一样传 None 就是"这一样读不出"（G5/G6 走的就是这条路）"""
    return {"before": None if before is None else frozenset(before),
            "after": None if after is None else frozenset(after),
            "library_after": None if keys is None else frozenset(keys),
            "library_before": None if keys_before is None else frozenset(keys_before),
            "archive": archive}


def cells(log, br=None, **kw):
    return chain.verify_analysis(B, A, T_START, log, official_candidates=br, **kw)


def j_cell(cs):
    return [c for c in cs if c[0].startswith("J ")][0][1]


J_NAME = [c[0] for c in cells(LOG)][-1]
if not J_NAME.startswith("J "):
    raise SystemExit(f"[夹具坏了] 验收表最后一条不是 J（实得 {J_NAME}）")

print("\n──── 乙：J 格 ────")
g1 = cells(LOG, bridge())
arm("G1 净增那枚在库键里 ⇒ ✅", True, j_cell(g1))
g2 = cells(LOG, bridge(keys=OLD))                      # 把 alpha_c 从库里摘掉
arm("G2 净增那枚不在库 ⇒ 只报红哨兵", chain.REPORT_ONLY_RED, j_cell(g2))
arm("G2b 同一片喂 print_checks ⇒ 红名单为空（不拦 ②）", [],
    chain.print_checks("自检 G2b", g2))
g3 = cells(LOG_BAD_D, bridge(keys=OLD))
arm("G3 同一片再把 D 弄坏 ⇒ 红名单只含 D（J 天生进不去）",
    [[c[0] for c in g3][3]], chain.print_checks("自检 G3", g3))
arm("G3b J 那格照样渲染成红（只报≠糊成 ⚪）", chain.REPORT_ONLY_RED, j_cell(g3))
arm("G4 候选净增 0 个名字 ⇒ ⚪（那一半归 H）", None, j_cell(cells(LOG, bridge(after=OLD))))
for tag, br in (("G5 候选后读不出", bridge(after=None)),
                ("G6 库键读不出", bridge(keys=None)),
                ("G7 压根没传取数", None)):
    arm(f"{tag} ⇒ ⚪", None, j_cell(cells(LOG, br)))
all_names = [c[0] for c in cells(LOG)]
arm("G8 验收表十条 A–J、末条是 J", (10, True),
    (len(all_names), all_names[-1].startswith("J ")), f"表={all_names}")
o1 = [c for c in g1 if c[0] != J_NAME]
o2 = [c for c in g2 if c[0] != J_NAME]
arm("G9a 摘掉 J 后其余九条逐字相同（差异只活在 J 里）", True, o1 == o2, f"{len(o1)} 条")
arm("G9b 只改库键那一个数 ⇒ J 跟着翻（尺子有牙）", True,
    j_cell(g1) is True and j_cell(g2) == chain.REPORT_ONLY_RED)
leaked = [(n, (chain.REPORT_ONLY_RED if n.startswith("I ") else ok), ev)
          for (n, ok, ev) in o1]
arm("G9c 喂一片人造的「I 被顺手动了」⇒ 比对看得见（G9a 不是瞎的）", False, leaked == o1)

print("\n──── 取数：真字节正对照 ＋ noop 负对照 ────")
snap = chain.official_candidates_snapshot()
if snap is None:
    raise SystemExit("[夹具坏了] 生产 rdagent_output/factors.json 读不出 ⇒ 正对照没对象")
raw = open(snap["path"], "rb").read()
rows_json = json.loads(raw.decode("utf-8"))
arm("G10a sha == 对该文件字节现算的 sha", True,
    snap["sha256"] == hashlib.sha256(raw).hexdigest(), f"sha={snap['sha256'][:12]}")
arm("G10b 名字集合 == 文件里逐条 name（不是条数像就算过）",
    frozenset(r.get("name") for r in rows_json), snap["names"], f"{snap['n']} 条")
lib = chain.factor_library_keys()
arm("G10c 库键集合 == 索引文件顶层键集合（逐位）",
    frozenset(json.loads(open(os.path.join(chain.DATA_DIR, "library",
                                            "factor_library_index.json"),
                              encoding="utf-8").read())), lib, f"{len(lib or ())} 键")
_real_out, _real_idx = config.RDAGENT_OUTPUT_DIR, flib.FACTOR_LIBRARY["index_path"]
_empty = tempfile.mkdtemp(prefix="c1_empty_")
try:
    config.RDAGENT_OUTPUT_DIR = _empty
    arm("G11a 候选目录换空处 ⇒ snapshot()=None（不是空集合）", None,
        chain.official_candidates_snapshot())
    flib.FACTOR_LIBRARY = {**flib.FACTOR_LIBRARY,
                           "index_path": os.path.join(_empty, "no_such_index.json")}
    arm("G11b 库索引指到不存在 ⇒ keys()=None", None, chain.factor_library_keys())
finally:
    config.RDAGENT_OUTPUT_DIR = _real_out
    flib.FACTOR_LIBRARY = {**flib.FACTOR_LIBRARY, "index_path": _real_idx}
    shutil.rmtree(_empty, ignore_errors=True)
arm("G11c 拆完夹具能读回来（夹具没把机器改坏）", True,
    chain.official_candidates_snapshot() is not None
    and chain.factor_library_keys() is not None)

print("\n──── 甲：每场归档候选清单（只追加、不覆写、同 sha 不重写；全程落 /tmp）────")
_work = tempfile.mkdtemp(prefix="c1_archive_")
_out = os.path.join(_work, "rdagent_out")
os.makedirs(_out)
_real_arch = chain.OFFICIAL_ARCHIVE_DIR


def _dir_sig(d):
    """目录内容的字节指纹 ⇒ 排序后的 [(文件名, 字节 sha256)]；目录不存在 ⇒ None

    A4 用它判"夹具没碰过生产归档目录"。**不许拿"目录不存在"当判据**：那是把
    "这台机器还没跑过带归档的那一场"当成"夹具是干净的"——两种状态在这格里被压成同一个
    读数，而真日更一跑就会把目录建出来，那一格从此天天红（10-06 实测：手工落了一枚真归档
    进去做白名单对拍，它就把整份夹具打死在 A4 上，红的是夹具的守卫、不是生产被写坏）。
    """
    if not os.path.isdir(d):
        return None
    return sorted((f, hashlib.sha256(
        open(os.path.join(d, f), "rb").read()).hexdigest())
        for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))


_pre_real_arch_sig = _dir_sig(_real_arch)      # 起夹具前先拍一张，末尾逐字节比回来
chain.OFFICIAL_ARCHIVE_DIR = os.path.join(_work, "archive")
config.RDAGENT_OUTPUT_DIR = _out


def archived():
    d = chain.OFFICIAL_ARCHIVE_DIR
    js = sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []
    idx = [json.loads(ln) for ln in open(os.path.join(d, "index.jsonl"),
                                         encoding="utf-8") if ln.strip()] \
        if os.path.exists(os.path.join(d, "index.jsonl")) else []
    return js, idx


try:
    def write_rows(names):
        with open(os.path.join(_out, "factors.json"), "w", encoding="utf-8") as f:
            json.dump([{"name": n, "expr": "Ref($close,1)/$close-1"} for n in names],
                      f, ensure_ascii=False)

    write_rows(list(OLD))
    r1 = chain.archive_official_candidates()
    if not r1.get("file"):
        raise SystemExit(f"[夹具坏了] 第一份归档就没落盘：{r1}")
    js1, ix1 = archived()
    body = open(os.path.join(chain.OFFICIAL_ARCHIVE_DIR, r1["file"]), "rb").read()
    sha1 = hashlib.sha256(body).hexdigest()
    arm("A1a 落一份 json ＋ index 一行", (1, 1), (len(js1), len(ix1)), str(js1))
    arm("A1b 归档字节与源文件逐位相同", open(os.path.join(_out, "factors.json"), "rb").read(),
        body)
    arm("A1c index 那行带复刻要用的三样（sha／条数／名字，外加源文件时刻）", True,
        ix1[0]["sha256"] == r1["sha256"] == sha1 and ix1[0]["n"] == 2
        and ix1[0]["names"] == list(OLD) and bool(ix1[0]["src_mtime"]))
    r2 = chain.archive_official_candidates()
    js2, ix2 = archived()
    arm("A2 同一份内容再跑 ⇒ skipped，不落第二份、不记第二行", (1, 1),
        (len(js2), len(ix2)), (r2.get("skipped") or r2.get("file") or "")[:56])
    write_rows(list(NEW))
    r3 = chain.archive_official_candidates()
    js3, ix3 = archived()
    arm("A3a 换内容 ⇒ 只追加一份（2 份 json／index 2 行）", (2, 2),
        (len(js3), len(ix3)), str(js3))
    arm("A3b 前一份字节未动（永不覆写）", sha1,
        hashlib.sha256(open(os.path.join(chain.OFFICIAL_ARCHIVE_DIR, r1["file"]),
                            "rb").read()).hexdigest())
    arm("A3c 账本里查得到本场净增那枚", ["alpha_c"],
        [n for n in ix3[-1]["names"] if n not in ix3[0]["names"]])
finally:
    chain.OFFICIAL_ARCHIVE_DIR = _real_arch
    config.RDAGENT_OUTPUT_DIR = _real_out
    shutil.rmtree(_work, ignore_errors=True)
arm("A4 生产归档目录字节指纹前后一致（夹具全程只写 /tmp）", _pre_real_arch_sig,
    _dir_sig(_real_arch))
# A4 的牙：判据得看得见"目录里多了一枚文件"，否则"没碰过"是恒绿的空话。
# 在 /tmp 里造同样两种状态转变（不存在→有内容、有内容→多一枚），不碰生产目录。
_tooth = tempfile.mkdtemp(prefix="c1_a4_tooth_")
try:
    _absent = _dir_sig(os.path.join(_tooth, "没有这个目录"))
    _t0 = _dir_sig(_tooth)
    with open(os.path.join(_tooth, "index.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"tooth": 1}\n')
    _t1 = _dir_sig(_tooth)
    arm("A4b 牙：不存在与空目录不是同一个读数，多一枚文件必须读出差别", True,
        _absent is None and _t0 == [] and _t1 != _t0)
finally:
    shutil.rmtree(_tooth, ignore_errors=True)

print("\n──── 判定 ────")
bad = [tag for tag, ok in results if not ok]
for tag, ok in results:
    print(f"  {tag:<44} {'✅' if ok else '❌'}")
if bad:
    raise SystemExit(f"[自检失败] {bad} ⇒ C-1 那两半（候选归档＋J 格）与它们的对照不一致")
print(f"[自检通过] {len(results)} 格全绿 ⇒ J 只报不拦（G2b）、只报不是没渲染（G3b）、"
      "差异活得下来（G9）、取数不是恒有（G11）、归档只追加（A3）")
