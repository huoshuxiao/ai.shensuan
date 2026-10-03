# -*- coding: utf-8 -*-
"""甲-A 生产场验收（10-01，2 折 + 折内点时池）——边算边打，任一格红即退出码 1

七条尺子各自可失败：
  C1 覆写面闭合   —— 本场动过的产物必须全在批准那几张里，多一张就报红
  C2 快照对表     —— 起前 16 份 sha256 逐份比：哪些字节没动、哪些被本场覆写
  C3 折表几何     —— 2 行、每行池口径=点时、折 1 宽度/有牙、折 2 宽度/有牙
  C4 判据一行没改 —— DSR 线还是 0.95，且三处读数都是 False（不许恒真）
  C5 试验数不变式 —— 折表 n_trials 单调、末值==账本；缓存按设计留存且条数==「1 主线+2 折」、
                     逐条自带作用域指纹，原子写的 `.part` 零残留（夹具先数到 1 再数生产目录）
  C6 official 腿  —— 本场它到底跑没跑：前置检查红项 / 链路自陈 / 产出数三处要互相咬合
                     （跑没跑都必须照实念，不许把"没跑"念成"跑对"）
  C7 可对照范围   —— 与两臂 M2 之间：池级四读数逐格等才算接线生效；因子集/N 不同就别比 DSR
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LINE = os.path.dirname(HERE)                      # etf/v1
REPO = os.path.dirname(os.path.dirname(LINE))     # 仓库根
SNAP = os.path.join(HERE, "snap_before_wp_prod_1001")
LOG = os.path.join(HERE, "wf_prod_1001.log")
T0 = 1759304880  # 15:48:00 起链那一分（下面用 mtime>=T0 划"本场动过"）

RESULTS = os.path.join(LINE, "data", "results")
LIB = os.path.join(LINE, "data", "library")
BASE = os.path.join(REPO, "common", "data", "etf")
BASE_CACHE = os.path.join(BASE, "cache")
MIRROR = os.path.join(BASE, "universe_all")
RISK = os.path.join(BASE, "risk")

# 起点时刻**从起前快照那份 sha.txt 的 mtime 反推**，不写死日期：
# 写死过一次就踩坑了——第一版把 epoch 算成 2025-10-01，整条 C1 把 09-30 那批旧产物
# 全念成"本场越界写入"（102 张），红得毫无信息。
T0 = os.path.getmtime(os.path.join(SNAP, "sha.txt"))

# ③ 主进程起于何时：只认链路自己在收尾盘点里打的「HH:MM:SS 起」。
# 第一版在这里踩过坑——想用日志文件的 ctime 当"创建时间"，可 ext4 的 ctime 是
# **inode 改动时间**，每次追加写都把它往前推，于是 15:48 起链被念成 16:37（= 最后一次写入）。
log = open(LOG, encoding="utf-8", errors="replace").read()
_start = re.search(r"③ 分析面\s*本场：([0-9:]{8}) 起", log)
FIELD_START = _start.group(1) if _start else "（场未收尾，跑完这一格自动填上）"

fails = []


def say(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


def ok(name, cond, detail):
    say("✅" if cond else "❌", f"{name}：{detail}")
    if not cond:
        fails.append(name)


def info(name, detail):
    say("ℹ️", f"{name}：{detail}")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ───────── C1 覆写面闭合 ─────────
print(f"\n──────── C1 覆写面闭合（**起前快照** "
      f"{datetime.fromtimestamp(T0):%H:%M:%S} 之后被动过的才算这一场）────────",
      flush=True)


def newer_than_t0(root, pattern=None, rel=False):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if pattern and not fn.endswith(pattern):
                continue
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getmtime(p) >= T0:
                    out.append(os.path.relpath(p, root) if rel else p)
            except OSError:
                pass
    return out


# C1a：批准的第一条边界是「① 数据面一个字节不动」——镜像与风险长表是它的领地
mirror_hit = newer_than_t0(MIRROR) + newer_than_t0(RISK)
ok("C1a ① 数据面（全市场镜像 + 风险长表）未被本场写入", not mirror_hit,
   "一张没动" if not mirror_hit else f"被写了 {len(mirror_hit)} 张：{mirror_hit[:5]}")
# 行情 csv 缓存也在 ① 的地盘里（trial_counter / 断点缓存是 ③ 的，单独放行）
cache_hit = [p for p in newer_than_t0(BASE_CACHE)
             if os.path.basename(p) not in ("trial_counter.json",
                                            "run_checkpoint_daily.pkl",
                                            "remining_state.json")]
ok("C1b ① 的行情缓存（逐只 csv / 池表 / 上市日）未被本场改写", not cache_hit,
   f"{len(cache_hit)} 张被动过：{[os.path.basename(p) for p in cache_hit[:6]]}"
   if cache_hit else "逐只日线缓存全部字节未动 ⇒ 两臂与本场吃的是同一批数")
# C1c：② 反馈面（日报 / 影子盘）按批准不该起
fb_hit = newer_than_t0(os.path.join(LINE, "data", "report")) \
    + newer_than_t0(os.path.join(LINE, "data", "live"))
ok("C1c ② 反馈面（report/ 与 data/live/）确实没起", not fb_hit,
   "一个文件没新增/改动" if not fb_hit else f"竟有 {len(fb_hit)} 张：{fb_hit[:5]}")
# C1d：归因水位——本场时间窗内共享源码被谁动过（动过 ⇒ 折表不能全归因于我这一批改动）
src_hit = newer_than_t0(os.path.join(LINE, "src"), ".py") \
    + newer_than_t0(os.path.join(REPO, "common", "src"), ".py")
ok("C1d 本场期间没有源码被改（读数可归因于同一份代码）", not src_hit,
   "src/ 与 common/src/ 无 .py 在本场窗口内变动" if not src_hit
   else "本场跑的中途有人改了共享源码 ⇒ 折表读数的归因要带这一句："
        + "、".join(f"{os.path.relpath(p, REPO)}@"
                    f"{time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(p)))}"
                    for p in src_hit))
if src_hit:
    # 归因要落到"这一场到底吃的是改前还是改后那一版"：③ 主进程一次性顶层
    # `from factor_dsl import compute_ic`，import 只发生一次 ⇒ 吃的是**进程起那一刻**
    # 的版本；.pyc 变新只说明之后有**别的进程**重新编译过，与本场无关。
    pyc = os.path.join(REPO, "common", "src", "core", "__pycache__",
                       "factor_dsl.cpython-310.pyc")
    newest = max(os.path.getmtime(p) for p in src_hit)
    info("C1d 补一句归因",
         f"③ 主进程起于 {FIELD_START}｜被改源码里最新一次 mtime "
         f"{time.strftime('%H:%M:%S', time.localtime(newest))}｜其 .pyc "
         f"{time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(pyc))) if os.path.exists(pyc) else '无'}"
         f" ⇒ 本场吃的是**改动前**那一版 DSL（改后那版由别的进程编译，不进本场读数）")
# ③ 自己的产物面只报张数，不当判据（九步本来就要写这一大片）；
# RD-Agent 容器工作区单独念一行——那是 pregen 重建面板写的，不是容器挖因子写的
for d in (RESULTS, LIB):
    fns = sorted(f for f in newer_than_t0(d, rel=True)
                 if not f.startswith("rdagent_output"))
    info(f"{os.path.basename(d)}/ 本场被写", f"{len(fns)} 张（③ 九步的产物面）："
         + "、".join(fns[:10]) + ("…" if len(fns) > 10 else ""))
rd = [f for f in newer_than_t0(RESULTS, rel=True) if f.startswith("rdagent_output")]
info("rdagent_output/ 被写", f"{len(rd)} 张（前置重建 b) 那一步写的面板与工作区，"
     "official 没起容器 ⇒ 这一片不代表挖出了新候选）")


# ───────── C2 快照对表 ─────────
print("\n──────── C2 起前快照 sha256 逐份对表 ────────", flush=True)
pairs = [  # (快照相对名, 现文件绝对路径)
    ("results/signals_daily.csv", f"{RESULTS}/signals_daily.csv"),
    ("results/equity_daily.csv", f"{RESULTS}/equity_daily.csv"),
    ("results/trades_daily.csv", f"{RESULTS}/trades_daily.csv"),
    ("results/dsr_daily.csv", f"{RESULTS}/dsr_daily.csv"),
    ("results/walk_forward_daily.csv", f"{RESULTS}/walk_forward_daily.csv"),
    ("results/multi_summary.csv", f"{RESULTS}/multi_summary.csv"),
    ("results/shap_timeline.csv", f"{RESULTS}/shap_timeline.csv"),
    ("pbo_result.json", f"{RESULTS}/pbo_result.json"),
    ("optimized_params_daily.json", f"{RESULTS}/optimized_params_daily.json"),
    ("library/factor_library_index.json", f"{LIB}/factor_library_index.json"),
    ("library/factor_library.csv", f"{LIB}/factor_library.csv"),
    ("cache/trial_counter.json", f"{BASE_CACHE}/trial_counter.json"),
    ("rdagent/factors.json", f"{RESULTS}/rdagent_output/factors.json"),
]
same = changed = missing = 0
for rel, cur in pairs:
    old_p = os.path.join(SNAP, rel)
    if not (os.path.exists(old_p) and os.path.exists(cur)):
        missing += 1
        say("⚪", f"{rel}：快照或现件缺一个，不比")
        continue
    a, b = sha(old_p), sha(cur)
    if a == b:
        same += 1
        say("🔒", f"{rel}：字节未动（{os.path.getsize(cur):,}B）")
    else:
        changed += 1
        say("✏️", f"{rel}：本场被覆写（{os.path.getsize(old_p):,}B → "
                  f"{os.path.getsize(cur):,}B）")
ok("C2 对表跑通", missing == 0, f"未动 {same} / 被覆写 {changed} / 无法比 {missing}")

# ───────── C3 折表几何 ─────────
print("\n──────── C3 折表几何 ────────", flush=True)
import pandas as pd  # noqa: E402

wf = pd.read_csv(f"{RESULTS}/walk_forward_daily.csv", encoding="utf-8-sig")
cols = list(wf.columns)
ok("C3a 折数==2（WP-3 新几何）", len(wf) == 2, f"实际 {len(wf)} 行")
ok("C3b 每行都念了池口径", "池口径" in cols and set(wf["池口径"]) == {"点时"},
   f"列在={('池口径' in cols)}，取值={sorted(set(wf['池口径'])) if '池口径' in cols else '无此列'}")
f1, f2 = wf.iloc[0], wf.iloc[1]
ok("C3c 折 1 宽度仍是物理天花板（6 只 / 有牙 0%）",
   int(f1["挖掘层宽度"]) == 6 and float(f1["截面有牙天%"]) == 0.0,
   f"宽度 {int(f1['挖掘层宽度'])}、日均厚 {f1['日均厚']}、有牙 {f1['截面有牙天%']}%、"
   f"新面孔 {int(f1['点时新面孔'])}")
ok("C3d 折 2 截面尺子全长牙（100 只 / 有牙 100%）",
   int(f2["挖掘层宽度"]) == 100 and float(f2["截面有牙天%"]) == 100.0,
   f"宽度 {int(f2['挖掘层宽度'])}、日均厚 {f2['日均厚']}、有牙 {f2['截面有牙天%']}%、"
   f"新面孔 {int(f2['点时新面孔'])}")
ok("C3e 两折测试段真的分开（不是一折空跑）",
   str(f1["回测区间"]) != str(f2["回测区间"]) and int(f2["fold"]) == 2,
   f"折 1 {f1['回测区间']}｜折 2 {f2['回测区间']}")
print(wf[["fold", "回测区间", "夏普比率", "总收益率", "交易次数", "n_trials",
          "DSR", "DSR通过", "折内因子数", "折内因子来源", "池口径",
          "挖掘层宽度", "截面有牙天%"]].to_string(index=False), flush=True)

# ───────── C4 判据一行没改 ─────────
print("\n──────── C4 判据一行没改 ────────", flush=True)
dsr_py = os.path.join(LINE, "src", "backtest", "dsr.py")
line = ""
if os.path.exists(dsr_py):
    line = next((l for l in open(dsr_py, encoding="utf-8")
                 if "dsr > 0.95" in l), "")
ok("C4a 门槛还是那句 `dsr > 0.95`（没被我偷偷放宽）", bool(line),
   f"{os.path.relpath(dsr_py, REPO)}：{line.strip() or '这一行不见了——要么被改要么被搬'}")

merged = pd.read_csv(f"{RESULTS}/walk_forward_oos_daily.csv", encoding="utf-8-sig").iloc[0]
ok("C4b 三处结论都是 False（折1/折2/合并）",
   bool(f1["DSR通过"]) is False and bool(f2["DSR通过"]) is False
   and bool(merged["passed"]) is False,
   f"折 1={f1['DSR通过']}({f1['DSR']})｜折 2={f2['DSR通过']}({f2['DSR']})｜"
   f"合并={merged['passed']}({round(float(merged['dsr']), 4)})")
ok("C4c 合并那把尺子段数==2（不是把一折当全场）",
   int(merged["n_segments"]) == 2,
   f"n_segments={int(merged['n_segments'])}、n_samples={int(merged['n_samples'])}")

# ───────── C5 试验数不变式 ─────────
print("\n──────── C5 试验数账本不变式 ────────", flush=True)
tc = json.load(open(f"{BASE_CACHE}/trial_counter.json", encoding="utf-8"))
tc_now = tc.get("count") if isinstance(tc, dict) else tc
tc_old = json.load(open(f"{SNAP}/cache/trial_counter.json", encoding="utf-8"))
tc_old = tc_old.get("count") if isinstance(tc_old, dict) else tc_old
if tc_now is None or tc_old is None:
    raise SystemExit(f"[C5 坏表] 账本读不出 count 键：现={tc} 快照={tc_old} ⇒ "
                     "不是判红，是尺子自己失效，先修尺子再谈结论")
ok("C5a 账本只增不减（本场确实往裡记了）", int(tc_now) >= int(tc_old),
   f"起前 {tc_old} → 现 {tc_now}（净增 {int(tc_now) - int(tc_old)}）")
ok("C5b 折表 n_trials 单调、末值==账本",
   int(f1["n_trials"]) <= int(f2["n_trials"]) == int(tc_now),
   f"折 1={int(f1['n_trials'])}｜折 2={int(f2['n_trials'])}｜账本={tc_now}｜"
   f"合并表 n_trials={int(merged['n_trials'])}")
ckp = os.path.join(BASE_CACHE, "run_checkpoint_daily.pkl")
# ⚠️ 这一格**第二版换了判据**，换的原因要照实念：第一版写的是「跑完零残留（正常收尾的场
#    该自己清断点缓存）」——那是我一个**没核过代码的前提**。读完 `src/run_checkpoint.py` 才
#    看清：这个类**没有任何删除路径**，只有 `_write()` 的 `.part` → `os.replace`（:215-223），
#    设计就是**留着给下一场续传**（本场收尾那行自己念「现有 3 条」）。⇒ 原判据是一条
#    **永远无法成立**的死尺子（与 `or True` 同病的反方向），当场换掉。
#    现在这三条都能红：①条数不等于「1 主线 + 2 折」；②某条条目没带自己的 `fp`（WP-1 逐条
#    作用域退化成就白炸）；③缓存目录里躺着 `.part`（原子写半路崩）。
import pickle  # noqa: E402

try:
    ck = pickle.load(open(ckp, "rb"))
except Exception as ex:                                    # 读不出也要出声，不许静默变绿
    raise SystemExit(f"[C5c 坏表] 断点缓存读不出：{ex} ⇒ 尺子瞎，不是判红")
ents = ck.get("entries", {})
ok("C5c 缓存条数==「1 主线 + 2 折」，且逐条自带作用域指纹（WP-1 的形状）",
   len(ents) == 3 and set(ents) == {"主线多源因子", "折 1 因子", "折 2 因子"}
   and all(isinstance(v, dict) and "fp" in v for v in ents.values()),
   f"{len(ents)} 条：{'、'.join(ents)}｜每条带 fp="
   + str([("fp" in v) for v in ents.values()])
   + f"｜全场指纹 n_splits={ck.get('fingerprint', {}).get('n_splits')}"
     f"、pit_pool={ck.get('fingerprint', {}).get('pit_pool')}")


def count_parts(root):
    return sum(1 for _d, _s, fs in os.walk(root) for f in fs if f.endswith(".part"))


_teeth = os.path.join(HERE, "tmp_c5c_teeth_1001")
os.makedirs(_teeth, exist_ok=True)
try:
    open(os.path.join(_teeth, "run_checkpoint_daily.pkl.part"), "wb").close()
    can_count = count_parts(_teeth) == 1        # 尺子有牙：造一枚它必须数到 1
finally:
    import shutil                               # noqa: E402
    shutil.rmtree(_teeth, ignore_errors=True)
ok("C5d 原子写零残留（`.part` 只在崩溃时才留下；先造一枚证明这把尺子数得到）",
   can_count and count_parts(BASE_CACHE) == 0,
   f"夹具数到 1={can_count}（False⇒尺子没牙，别把 0 读成干净）｜"
   f"{os.path.relpath(BASE_CACHE, REPO)}/ 实数 {count_parts(BASE_CACHE)} 枚")
_bk = re.search(r"断点: (.*)", log)
info("C5c 补一句", "缓存**留着**是设计（下一场 `--resume` 要吃它），不是这一场没收尾："
     "链路自己那行念的是「" + (_bk.group(1).strip() if _bk else "本场日志里没有「断点:」那行") + "」")

# ───────── C6 official 这条腿 ─────────
print("\n──────── C6 official（RD-Agent）这条腿 ────────", flush=True)
after = re.search(r"\[重建之后\] (.*)", log)
ok("C6a 前置重建把新鲜度闸敲绿了", bool(after) and "✅ 2/2" in after.group(1),
   after.group(1).strip() if after else "日志里没有「重建之后」那行")
# 只认**那一段前置检查**里的红项：第一版拿全场 `❌` 通配，把重建之前那句「2/2 道落后」
# 和逐条因子的 IC 判红都扫进来了（结论碰巧对，明细全是噪音——这种尺子下次换了日志形状就骗人）。
# ⚠️ 红项在块内抓、产出数**必须在块外抓**：上面那个 `(?=official\s+产出)` 的前瞻会把
#    「✅ official   产出 0 个因子」这一行**挡在块外**（前瞻不消费），拿块文去搜它就恒为 None
#    ⇒ 格子因为尺子瞎而判红，看着像"日志对不上"，其实是"我没看见"。第二版踩过这一坑。
mainline = log.split("--- 折 1/2 ---")[0]
pre_blk = re.search(r"RD-Agent\(Q\) 前置检查:(.*?)(?=official\s+产出)", mainline, re.S)
bad_pre = re.findall(r"❌ (\S[^\n]*)", pre_blk.group(1)) if pre_blk else []
prod = re.search(r"official\s+产出 (\d+) 个因子", mainline)
n_off = int(prod.group(1)) if prod else None
# 链路自己在块末念了一句「本次未运行（前置依赖缺失: X）」——拿它当**第二条独立证据**，
# 不靠我从 ❌ 行数推断。两条都指同一处才算读数诚实。
self_skip = re.search(r"RD-Agent\(Q\) 本次未运行（前置依赖缺失: (.+)）", mainline)
ok("C6b official 这条腿：前置检查红项、链路自陈、产出数三者对得上（没把「没跑」念成「跑对」）",
   pre_blk is not None and n_off is not None
   and ((bool(bad_pre) and n_off == 0 and self_skip is not None)
        or (not bad_pre and n_off > 0 and self_skip is None)),
   f"前置检查段抓到={pre_blk is not None}｜红项={[b[:34] for b in bad_pre] or '无'}｜"
   f"链路自陈={self_skip.group(1) if self_skip else '未念「本次未运行」'}｜"
   f"official 产出={n_off} 个因子｜⇒ 本场这一腿"
   + ("**没真跑容器循环 ⇒ 属「未验到」，不许写成「验过」**" if bad_pre else "真跑了容器")
   + f"（链路在闸上自己预告的是「墙钟按小时排」，实际收在分钟级，原因见上面那条红项）")
info("折内 official", "两折都念了「已在折内停用（丙-2）」⇒ 折表里没有它，"
     + ("主线这一腿又没跑 ⇒ " if bad_pre else "") + "本场唯一动过的挖掘腿是 llm + registry")
lib_now = json.load(open(f"{LIB}/factor_library_index.json", encoding="utf-8"))
lib_old = json.load(open(f"{SNAP}/library/factor_library_index.json", encoding="utf-8"))
new_keys = sorted(set(lib_now) - set(lib_old))
gone_keys = sorted(set(lib_old) - set(lib_now))
new_src = {}
for k in new_keys:
    rec = lib_now[k] if isinstance(lib_now[k], dict) else {}
    new_src[str(rec.get("source", "?"))] = new_src.get(str(rec.get("source", "?")), 0) + 1
# official 这一腿产出 0 ⇒ 库里净增的那些条**不许**自称来自 official，
# 自称了就是「静默跳过还记账」那一类病（与 C6b 同一族，两条尺子互相盯着）
ok("C6c 因子库净增的来源与 official 那条腿不矛盾",
   not (n_off == 0 and any(s.lower().startswith(("official", "rdagent"))
                           for s in new_src)),
   f"起前 {len(lib_old)} 条 → 现 {len(lib_now)} 条｜净增 {len(new_keys)} "
   f"{new_src or ''}｜被删/改名 {len(gone_keys)}"
   + (f"：{gone_keys[:4]}" if gone_keys else ""))
info("C6d 净增条数与折表的关系",
   f"折内那条 registry 腿吃的是**当下这一版库**（现 {len(lib_now)} 条），"
   f"而两臂那场是 {len(lib_old)} 条 ⇒ 折表的因子集差异有一部分来自库净增，不全是池子改动的功劳")

# ───────── C7 与两臂 M2 的可对照范围 ─────────
print("\n──────── C7 与昨晚两臂 M2 之间，什么能比、什么不能比 ────────", flush=True)
vlog = open(os.path.join(HERE, "wf_pit_verify_0930.log"),
            encoding="utf-8", errors="replace").read()
m2 = vlog.split("臂 M2：")[1]
pool_rows = re.findall(r"点时池：截断前 (\d+) 只 → 入选 (\d+) 只｜日均厚 ([\d.]+) 只｜"
                       r"截面尺子有牙（≥30 只）的天数 ([\d.]+)%", m2)
# ⚠️ 这条尺子的基准是从**另一场的日志**正则抓出来的：抓空了就会拿 () == (生产两行) 判红，
#    但"抓空"和"抓到了但不同"是两种病，必须分开念——否则下一次改正则的人无从知道是谁坏了。
pool_ok_shape = len(pool_rows) == 2
_m2_pool = [(r[1], r[2], r[3]) for r in pool_rows]
_prod_pool = [(int(f1["挖掘层宽度"]), f1["日均厚"], f1["截面有牙天%"]),
              (int(f2["挖掘层宽度"]), f2["日均厚"], f2["截面有牙天%"])]
ok("C7a 池级四读数逐格相同（这才叫接线在生产里生效）",
   pool_ok_shape
   and int(pool_rows[0][1]) == int(f1["挖掘层宽度"])
   and float(pool_rows[0][2]) == float(f1["日均厚"])
   and float(pool_rows[0][3]) == float(f1["截面有牙天%"])
   and int(pool_rows[1][1]) == int(f2["挖掘层宽度"])
   and float(pool_rows[1][2]) == float(f2["日均厚"])
   and float(pool_rows[1][3]) == float(f2["截面有牙天%"]),
   (f"基准抓到的行数={len(pool_rows)}（要 2 才有得比）｜"
    if not pool_ok_shape else "")
   + f"两臂 M2={_m2_pool}｜生产场={_prod_pool}")
src1 = re.findall(r"\[对拍折 1\] 引擎=(\[.*?\])", m2)
info("C7b 因子集不同处", f"两臂折内引擎={src1[0] if src1 else '?'}（只 registry），"
     f"生产场折内=registry+multi_source ⇒ 折内因子数 {int(f1['折内因子数'])}/"
     f"{int(f2['折内因子数'])}，来源列念的是「{f1['折内因子来源']}」")
info("C7c N 不同处", f"两臂合并 N=14，生产场 N={int(merged['n_trials'])}"
     "⇒ 运气门槛年化不同，DSR 与夏普**跨场不可比**，只作本场自陈")

# ───────── 收尾 ─────────
print("\n──────── 验收结论 ────────", flush=True)
print(f"  合并样本外：段数 {int(merged['n_segments'])}｜bar {int(merged['n_samples'])}｜"
      f"年化夏普 {round(float(merged['sharpe_annual']), 4)}｜DSR {round(float(merged['dsr']), 4)}｜"
      f"运气门槛年化 {round(float(merged['sr0_annual']), 4)}｜N {int(merged['n_trials'])}｜"
      f"通过 {bool(merged['passed'])}", flush=True)
if fails:
    print(f"\n❌ {len(fails)} 格判红：{fails}", flush=True)
    sys.exit(1)
print("\n✅ 全部格绿（每格都可失败；C6 那条「未验到」是照实念，不算绿也不算红）", flush=True)
