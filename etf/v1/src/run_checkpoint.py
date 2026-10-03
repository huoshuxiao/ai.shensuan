# -*- coding: utf-8 -*-
"""跑批段级断点缓存（只在 ETF 线内，默认关）

为什么要这一层：`--with-analysis` 那一场九步里，真正贵的只有"挖因子"——
主线一支实测 ≈1 小时 40 分，样本外每折各再拉一次容器循环（83~120 分钟/折），
整场墙钟是十小时量级。而这十小时里没有任何一段落过盘：机器一重启就从头再来
（09-24 就白烧过一轮 17 分钟，判据一个没落）。

它保什么、保不了什么：
- 保：已完成段的**因子**不再重挖。命中就跳过容器循环，直接进入廉价的下半段
  （信号/回测/DSR 每场照原样重算，**不缓存** —— 把判据读数一起冻住不是本意）。
- 保不了：单个容器循环**内部**（一次约 1 小时）。那要 RD-Agent 自己的 session
  恢复，入口在共享层 `common/rdagent_docker/rdagent_driver.py`，本线不动它。

一条不许破的不变式：**缓存条目存在 ⇔ 这一段的试验次数已记进 TrialCounter**。
所以写入顺序一律是「算 → tc.add → record」，record 紧跟 add（中间只隔一次
毫秒级 pickle 落盘）。反序会造出"跳过 add 的命中"，等于让 DSR 运气门槛的分母
凭空少一截 —— N 是判据的输入，宁可多算一次也不许少算。

作废闸 = 本场数据指纹（频率 / 时间轴首末 / bar 数 / 标的池哈希 / 切折参数 /
挖掘来源 / **折内 official 开关** / **折内点时池开关**，共十二项）。数据一推进指纹就变，
上一场的条目读不到，不存在"拿昨天的因子冒充今天挖出来的"这种静默复用；改挖掘**接线**
（丙-2 那种只动折内源集合的改法）也一样要进指纹，否则 `--resume` 会续传命中 ⇒ 新开关空转
（09-30 实测的坑）。

**作废是逐条判的，不是整批判的**（09-30 改）：每条缓存各自记下"写它时那一段所依赖的
指纹"，取用时按这一段的作用域比。作用域两档 —— `full`=全部键（折内段落，折的几何与
折内源开关都是它的输入），`main`=剥掉 `FOLD_ONLY_KEYS`（主线段落，它不读折不读折内开关）。
为什么：整批作废时"只想重跑样本外那几折"要付一整条主线的钱 —— 09-30 上午为了躲这笔手工删了三条
条目（`temp/ckops_foldonly_0930.py`），那是人做的例外裁决，不该是常态。
新加指纹键**默认落在 full 一侧** ⇒ 主线跟着一起作废（保守方向）；要豁免必须显式加进
`FOLD_ONLY_KEYS` 并给夹具，理由是"少写一行豁免"最多浪费一小时四十分钟，而"多写一行
豁免"会把改了不认账的接线变成空操作。
开关 `ETF_RUN_RESUME=1`（不带 = 一个字节都不写，行为与今天完全一致）。
"""

import hashlib
import os
import pickle
import time

from config import CACHE_DIR, FREQ, MULTI_SOURCE, WALK_FORWARD

ENV_FLAG = "ETF_RUN_RESUME"

# 只有**折内**那几段才读的键：翻它们只该作废各折条目，不该把主线那一小时四十分钟一起拖倒。
# 新键不加进来 = 默认全场作废（保守）；加了就必须同时给夹具（test_run_checkpoint ⑩）。
FOLD_ONLY_KEYS = ("n_splits", "train_ratio", "embargo_bars", "official_in_fold",
                  "pit_pool")


def _truthy(v):
    return str(v or "").lower() in ("1", "true", "yes", "on")


def scoped_fingerprint(fingerprint, scope="full"):
    """按段落作用域裁指纹：`full` 全键（折内），`main` 剥掉 FOLD_ONLY_KEYS（主线）。

    未知 scope 直接抛，不静默当成 full —— 拼错一个字符串就把作废闸整段关掉过。"""
    if scope == "full":
        return dict(fingerprint)
    if scope == "main":
        return {k: v for k, v in fingerprint.items()
                if k not in FOLD_ONLY_KEYS}
    raise ValueError(f"未知断点作用域 scope={scope!r}（只认 'full' / 'main'）")


def make_fingerprint(codes, all_ts):
    """本场数据指纹：上面 docstring 里那十二项全参加，缺一项目视就看不出问题"""
    pool_hash = hashlib.sha1(
        "|".join(sorted(str(c) for c in codes)).encode("utf-8")).hexdigest()[:12]
    wf = WALK_FORWARD
    return {
        "freq": FREQ,
        "bars": int(len(all_ts)),
        "ts_first": str(all_ts[0]),
        "ts_last": str(all_ts[-1]),
        "pool_hash": pool_hash,
        "pool_n": len(codes),
        "n_splits": wf.get("n_splits"),
        "train_ratio": wf.get("train_ratio"),
        "embargo_bars": wf.get("embargo_bars"),
        "sources": "+".join(MULTI_SOURCE.get("sources") or []),
        # 折内源集合是**接线**不是数据：只改代码不进指纹 ⇒ --resume 会拿旧因子
        # 冒充"这一折现挖的"，丙-2 那个开关就空转了（09-30 自查抓到的洞）
        "official_in_fold": bool(MULTI_SOURCE.get("official_in_fold", True)),
        # 同上一条理由：折内候选池从"今日挑"换成"段内点时挑"也是接线改动，
        # 不进指纹 ⇒ 旧池挖的因子会被当成点时池挖的交回来（09-30 WP-2）
        "pit_pool": _pit_tag(),
    }


def _pit_tag():
    """点时池开关的读数串（关掉就是 'off'）；折几何那几项由 WALK_FORWARD 供"""
    pit = (WALK_FORWARD.get("pit_pool") or {})
    if not pit.get("enabled"):
        return "off"
    return f"on:{pit.get('min_share')}:{pit.get('max_codes')}"


class RunCheckpoint:
    """段级缓存。disabled 时 resolve 恒走 compute、record 恒不落盘。"""

    def __init__(self, enabled=None, path=None):
        self.enabled = (_truthy(os.environ.get(ENV_FLAG))
                        if enabled is None else bool(enabled))
        self.path = (path or os.path.join(CACHE_DIR,
                                          f"run_checkpoint_{FREQ}.pkl"))
        self.fingerprint = None
        self.entries = {}
        self.replayed = False
        self.replay_n_trials = None
        self.hits = 0
        self.misses = 0
        self.discarded = 0
        self.stale = 0

    # ───────── 开闸 ─────────

    def arm(self, fingerprint):
        """载入上一场缓存，**逐条**比指纹：只作废"这一段依赖的输入变了"的那些条"""
        self.fingerprint = fingerprint
        if not self.enabled:
            print(f"[断点续传] 未开（env {ENV_FLAG} 未设）⇒ 九步全部照原样重算，"
                  f"不读写 {os.path.basename(self.path)}")
            return self
        if not os.path.exists(self.path):
            print("[断点续传] 已开，但本场是首次：没有可续的缓存文件")
            return self
        try:
            with open(self.path, "rb") as f:
                blob = pickle.load(f)
        except Exception as e:
            print(f"[断点续传] ⚠️ 缓存读不出（{type(e).__name__}: {e}）"
                  f"⇒ 整批作废，从零算")
            return self
        blob_fp = blob.get("fingerprint") or {}
        raw = blob.get("entries") or {}
        full_fp = scoped_fingerprint(fingerprint, "full")
        main_fp = scoped_fingerprint(fingerprint, "main")
        kept = {}
        for k, ent in raw.items():
            # 本版之前的条目身上没有自己的 fp：它们共享整场 blob_fp（那时作废就是整批作废）
            ent_fp = ent.get("fp") or blob_fp
            if ent_fp == full_fp or ent_fp == main_fp:
                ent["fp"] = ent_fp
                kept[k] = ent
        self.entries = kept
        self.discarded = len(raw) - len(kept)
        if not raw:
            print("[断点续传] 已开，上一场没留下条目（空缓存）⇒ 全场现算")
            return self
        if not kept:
            print(f"[断点续传] ⚠️ 数据指纹已变（上一场 末日 "
                  f"{blob_fp.get('ts_last')} → 本场 {fingerprint['ts_last']}）"
                  f"⇒ {len(raw)} 条旧缓存整批作废")
            return self
        fold_side = [k for k, e in kept.items() if e["fp"] == full_fp]
        main_side = [k for k, e in kept.items() if e["fp"] == main_fp]
        print(f"[断点续传] 已开，载入 {len(raw)} 条上一场缓存 ⇒ 本场可用 {len(kept)} 条"
              f"（折内 {len(fold_side)} 条：{'、'.join(fold_side) or '（空）'}；"
              f"主线 {len(main_side)} 条：{'、'.join(main_side) or '（空）'}）"
              + (f"；{self.discarded} 条因所属段落的指纹已变而作废"
                 if self.discarded else ""))
        return self

    # ───────── 读写 ─────────

    def resolve(self, key, compute, label="", scope="full"):
        """命中 ⇒ 返回缓存并把 self.replayed 置真；未命中 ⇒ 现算

        命中时同时把 `replay_n_trials` 填成**上一场这一笔落账之后的累计试验数**：
        跑完不中断的那场里，这一折的运气门槛用的就是这个值（add 紧跟在开挖之后）。
        续传时账本已经走到全场末尾，不还原的话折 1 会读到整场的 N ⇒ 折表与
        不间断那场对不上。09-29 夹具第一次就是这样抓出来的：续传场折 1 的
        n_trials 从 1 变 3、DSR 0.9989→0.9864。

        scope 必须与 record 时一致：`full` 逐键比（折内段落），`main` 只看主线读得到的
        那几键。条目虽被 arm 留下了，用它的那一段口径与存它时不同 ⇒ 仍算未命中。"""
        self.replayed = False
        self.replay_n_trials = None
        ent = self.entries.get(key) if self.enabled else None
        if ent is not None and ent.get("fp") != scoped_fingerprint(
                self.fingerprint, scope):
            # arm 之后指纹又被挪过（探针改参数），或这一段的作用域与存它时不同
            print(f"  ⏳ {label or key}：缓存指纹与本段口径不符（scope={scope}）⇒ 现算")
            self.stale += 1
            ent = None
        if ent is not None:
            self.replayed = True
            self.replay_n_trials = ent.get("tc_after")
            self.hits += 1
            print(f"  ♻️ 续传命中 {label or key}："
                  f"{_n_of(ent.get('value'))} 个因子，"
                  f"缓存于 {time.strftime('%m-%d %H:%M', time.localtime(ent.get('written', 0)))}"
                  f"（当时累计试验 N={self.replay_n_trials}）⇒ 本段容器循环跳过")
            return ent["value"]
        self.misses += 1
        return compute()

    def record(self, key, value, tc=None, label="", scope="full"):
        """**必须紧跟在 tc.add 之后调用**（见模块 docstring 那条不变式）"""
        if not self.enabled or value is None:
            return
        self.entries[key] = {"value": value,
                             "tc_after": (tc.get() if tc else None),
                             "written": time.time(),
                             "fp": scoped_fingerprint(self.fingerprint, scope)}
        print(f"  💾 断点已存 {label or key}：{_n_of(value)} 个因子，"
              f"累计试验 N={self.entries[key]['tc_after']}（口径 {scope}）")
        self._write()

    def _write(self):
        """原子落盘：先写 .part、回读确认解得开，再 os.replace 顶上"""
        tmp = self.path + ".part"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "wb") as f:
                pickle.dump({"fingerprint": self.fingerprint,
                             "entries": self.entries}, f)
            with open(tmp, "rb") as f:
                pickle.load(f)          # 回读：解不开就等于没存
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"  ⚠️ 断点缓存写盘失败（{type(e).__name__}: {e}）"
                  f"⇒ 本场剩下的段照常算，只是下次没有可续的点")
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ───────── 收尾读数 ─────────

    def summary(self):
        if not self.enabled:
            return ""
        return (f"续传命中 {self.hits} 段 / 现算 {self.misses} 段"
                + (f" / 其中 {self.stale} 段是「缓存留下了但口径不符」" if self.stale else "")
                + f" / 开闸时作废 {self.discarded} 条 / "
                f"缓存文件 {os.path.basename(self.path)} 现有 "
                f"{len(self.entries)} 条")


def _n_of(value):
    return len(value) if isinstance(value, (list, tuple)) else 1
