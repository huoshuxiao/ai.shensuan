# -*- coding: utf-8 -*-
"""`replay_manual_delist()`：③ 的 upsert 复活人工下架 ⇒ 链路紧跟压回去（09-29 选项二）

背景读数：09-29 重跑日更时，丁2 上午刚下架的 `reversal_5` / `volatility_20` 被
`[4/9]` 的 `batch_upsert(..., status="active")` 翻回 active（库 27→29），而共享层
`core/factor_library.py:49` 对已在库条目是**无条件**写 status 的。这一支用例钉住的
就是"重放只认人工下架理由、且不误伤别的条目"。

用例不碰真库也不碰 git：喂进去的是鸭子类型的假 lib（只实现被调用的那三件事），
`conftest.py` 已把 `ETF_DATA_DIR` 指到临时目录、把 `src/` 塞进 `sys.path` 并关掉了
因子库 git 自动提交，这里连它都不需要。
"""

import run_etf_daily_chain as chain        # noqa: E402  (路径由 conftest 负责)


class FakeLib:
    """只实现 `replay_manual_delist` 真正调用的三件事：factors / mark_status / save_markdown"""

    def __init__(self, factors):
        self.factors = factors
        self.saved = 0
        self.marked = []

    def mark_status(self, name, status, reason=""):
        self.marked.append((name, status, reason))
        self.factors[name]["status"] = status
        self.factors[name]["status_reason"] = reason

    def save_markdown(self):
        self.saved += 1


TAG = chain.MANUAL_DELIST_TAG
REASON = f"{TAG}(09-29)：与 mom_5 的逐日截面 |Spearman|=0.9990 >= 0.99"


def _f(status, reason=None):
    d = {"status": status}
    if reason is not None:
        d["status_reason"] = reason
    return d


def test_复活的条目被压回_inactive_并落盘一次():
    lib = FakeLib({"reversal_5": _f("active", REASON),
                   "volatility_20": _f("active", REASON)})
    hit, wrote = chain.replay_manual_delist(lib=lib)
    assert sorted(hit) == ["reversal_5", "volatility_20"], hit
    assert wrote and lib.saved == 1
    for name in hit:
        f = lib.factors[name]
        assert f["status"] == "inactive"
        # 理由必须一字不改地留档：那是这张名单的唯一出处
        assert f["status_reason"] == REASON


def test_没有下架理由的_active_条目一律不碰():
    """负对照：判据不在这一步里。误压一条＝替库做一次没做过的下架决定。"""
    lib = FakeLib({"gp_0": _f("active"),                       # 正常在册，无理由
                   "other": _f("active", "别的口径：衰减观察")})  # 有理由但不是这一类
    hit, wrote = chain.replay_manual_delist(lib=lib)
    assert hit == [] and wrote is False
    assert lib.saved == 0 and lib.marked == []
    assert all(f["status"] == "active" for f in lib.factors.values())


def test_已经_inactive_的条目不会被重复记账():
    """幂等：第二天再跑一次，一个字节都不该动。"""
    lib = FakeLib({"reversal_5": _f("inactive", REASON)})
    hit, wrote = chain.replay_manual_delist(lib=lib)
    assert hit == [] and wrote is False and lib.saved == 0


def test_apply_为_False_只念名单不落盘():
    lib = FakeLib({"reversal_5": _f("active", REASON)})
    hit, wrote = chain.replay_manual_delist(apply=False, lib=lib)
    assert hit == ["reversal_5"]
    assert wrote is False and lib.saved == 0
    assert lib.factors["reversal_5"]["status"] == "active"
