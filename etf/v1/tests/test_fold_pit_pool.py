# -*- coding: utf-8 -*-
"""WP-2：折内候选池改成点时挑法（修幸存者 + 前视泄漏入口）。

泄漏入口在 `common/src/data/etf/etf_universe.py:build()` —— 它按**今天**的 20 日
成交额中位数把 871 只截成 100 只，这 100 只再原样喂给每一折 ⇒ 折 1（2010~2014）
的"可投宇宙"实际是"活到 2026 年且今天还很活跃的那批"。`fold_pool.py` 把挑池
这件事搬到**本折训练段内**，本文件钉的是这几格语义：

① 关闸时一根手指都不碰加载器（`_fold_wide_pool` 返回 None）；
② 开闸时原样把加载结果交出去；加载器抛异常要**出声**告知"泄漏入口这一场没关掉"；
③ 接线有牙：`stage_validation` 必须把宽面板**交进** `walk_forward_run` 的
   `wide_pool` 形参（这格是 10-01 全套跑出来的真红 —— 改了被调方签名，旧夹具的
   `fake_run` 不收 `wide_pool`，当场 TypeError ⇒ 这条腿原本没人看）；
④ 前视免疫：把 `tr_e` 之后的成交额放大一千倍，入选表与读数逐格不变；
⑤ 波动闸吃的是**年化**值（日频 std 直接比会严 √252≈16 倍，09-30 探针就是这么
   把早折池子量成 0 只的）；成交额闸、训练段长度闸、min_share 各自有牙；
⑥ `FoldUniverse` 只认"该标的自己的首根 bar + 满 min_list_days"，不漏池外代码；
⑦⑧ 主循环两臂对拍：开点时池 ⇒ 折表标「点时」、挖掘层宽度取点时池、新面孔进池；
   关（或没传宽面板）⇒ 仍「今日」；开着却没传宽面板必须出声；
⑨ 点时池在该段挑不出标的 ⇒ 本折跳过并出声，不拿空池假装跑了一折。
"""

import numpy as np
import pandas as pd
import pytest

import fold_pool
import main
from config import WALK_FORWARD, ETF_FILTER, STRATEGY_PBO, PBO_TIMELINE, \
    MULTI_STRATEGY
from walk_forward import walk_forward_run
from fold_pool import FoldUniverse, select_pit_pool, tradable_mask
from synth import wf_pool_bars

START = "2010-01-04"          # 早于任何折的训练段起点，让上市满一年这道闸测得出差别


def _df(idx, daily_vol, amount, seed=0):
    """确定性合成日线：close 走指定日波动，amount 恒定"""
    rng = np.random.default_rng(seed)
    close = 10.0 * np.cumprod(1 + rng.normal(0, daily_vol, len(idx)))
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": amount / 10.0,
                         "amount": np.full(len(idx), float(amount))},
                        index=idx)


def _panel(codes, n_bars=900, daily_vol=0.012, amount=2e8):
    idx = pd.bdate_range(START, periods=n_bars)
    return {c: _df(idx, daily_vol, amount, seed=i)
            for i, c in enumerate(codes)}, idx


def _pit(**over):
    cfg = {"enabled": True, "min_share": 0.0, "max_codes": 100}
    cfg.update(over)
    return cfg


# ========== ① ② 开关两侧 ==========

def test_pit_disabled_never_touches_the_loader(monkeypatch):
    """关闸必须是**真关**：负对照把加载器换成"调用即失败"。"""
    monkeypatch.setitem(WALK_FORWARD, "pit_pool", {"enabled": False})
    monkeypatch.setattr(fold_pool, "load_wide_panel",
                        lambda *a, **k: pytest.fail("关闸还在补宽面板"))
    pool, _ = _panel(["510300"], n_bars=40)
    assert main._fold_wide_pool(pool) is None


def test_pit_enabled_hands_back_the_loaded_panel(monkeypatch):
    sentinel = {"510300": pd.DataFrame()}
    seen = {}

    def _loader(loader, pool):
        seen["pool"] = pool
        return sentinel

    monkeypatch.setitem(WALK_FORWARD, "pit_pool", _pit())
    monkeypatch.setattr(fold_pool, "load_wide_panel", _loader)
    pool, _ = _panel(["510300"], n_bars=40)
    assert main._fold_wide_pool(pool) is sentinel
    assert seen["pool"] is pool, "补数必须拿本线现池当基准（否则新面孔数无从算起）"


def test_load_failure_is_loud_and_falls_back(monkeypatch, capsys):
    """加载失败 ⇒ 返回 None 且出声。静默退回今日池是最坏结局：折表看着像点时
    口径，其实一个字节都没改。"""
    def _boom(loader, pool):
        raise RuntimeError("镜像目录不存在")

    monkeypatch.setitem(WALK_FORWARD, "pit_pool", _pit())
    monkeypatch.setattr(fold_pool, "load_wide_panel", _boom)
    pool, _ = _panel(["510300"], n_bars=40)
    assert main._fold_wide_pool(pool) is None
    assert "没关掉" in capsys.readouterr().out


# ========== ③ 接线 ==========

def test_stage_validation_forwards_wide_pool(monkeypatch):
    import walk_forward as wf_mod
    pool, idx = _panel([f"5103{i:02d}" for i in range(3)],
                       n_bars=wf_pool_bars())
    sentinel = dict(pool)
    got = {}

    monkeypatch.setitem(WALK_FORWARD, "pit_pool", _pit())
    monkeypatch.setattr(main, "_fold_wide_pool", lambda p: sentinel)
    monkeypatch.setattr(
        wf_mod, "walk_forward_run",
        lambda p, universe, factor_fn, backtest_fn, trial_counter=None,
        checkpoint=None, wide_pool=None: (
            got.__setitem__("wide_pool", wide_pool)
            or {"folds": [], "summary": {}, "dsr_list": [], "pbo": {}}))
    for cfg in (STRATEGY_PBO, PBO_TIMELINE, MULTI_STRATEGY):
        monkeypatch.setitem(cfg, "enabled", False)
    monkeypatch.setitem(WALK_FORWARD, "enabled", True)

    main.stage_validation([], pool, None, idx, None, None,
                          factors=[{"name": "x", "expr": "close",
                                    "mean_ic": 0.05, "icir": 0.5,
                                    "impl": {c: {"factor": pool[c]["close"],
                                                 "ic": 0.05} for c in pool},
                                    "source": "simple"}])
    assert got["wide_pool"] is sentinel, \
        "折内点时池没接到主循环 ⇒ 幸存者泄漏入口仍未关（生产路径靠这一格把关）"


# ========== ④ 前视免疫 ==========

def test_selection_is_blind_to_beyond_the_window():
    """tr_e 之后的数据无论怎么改都不许影响挑池 —— 这条不成立就不是点时。"""
    panel, idx = _panel(["510300", "510301", "510302"], n_bars=900)
    for c, amt in zip(panel, (1e9, 2e8, 5e7)):
        panel[c]["amount"] = float(amt)
    tr_s, tr_e = idx[300], idx[899]
    sel_a, read_a = select_pit_pool(panel, tr_s, tr_e, min_share=0.0)
    assert sel_a == ["510300", "510301", "510302"], \
        f"排序键不是段内成交额中位数（{sel_a}）"

    leaked = {c: d.copy() for c, d in panel.items()}
    for c in leaked:
        leaked[c].loc[leaked[c].index > tr_e, "amount"] *= 1000.0
    sel_b, read_b = select_pit_pool(leaked, tr_s, tr_e, min_share=0.0)
    assert sel_a == sel_b, f"段外数据改变了入选：{sel_a} vs {sel_b}"
    assert read_a == read_b, "读数被段外数据污染（排序键不是段内中位数）"


# ========== ⑤ 三道闸 ==========

def test_vol_gate_reads_annualized_not_raw_std():
    """日波动 1.2% ⇒ 年化 19% ≥ 5% 应入选；摘掉 √252 就变成 1.2% < 5% 被挡。
    ⚠️ `min_share` 这里必须 >0 —— 拿 0.0 挑池时"过闸占比"那道闸等于不存在，
    10-01 第一轮拔牙（去掉 √252）就是被这个 0.0 挡在门外、本格照样绿。"""
    panel, idx = _panel(["510300"], n_bars=900, daily_vol=0.012)
    df = panel["510300"]
    assert df["close"].pct_change().std() < ETF_FILTER["min_ann_vol"] < \
        df["close"].pct_change().std() * (252 ** 0.5), \
        "夹具几何不对：这格的牙靠的是「日频 std 在门槛下、年化后在门槛上」"
    sel, _ = select_pit_pool(panel, idx[300], idx[899], min_share=0.5)
    assert sel == ["510300"], "年化闸被写成日频闸 ⇒ 早折池子会被量成 0 只"


def test_amount_gate_bites():
    panel, idx = _panel(["510300", "510301"], n_bars=900, amount=2e8)
    panel["510301"]["amount"] = ETF_FILTER["min_avg_amount"] / 10
    sel, _ = select_pit_pool(panel, idx[300], idx[899], min_share=0.5)
    assert sel == ["510300"], f"低成交额标的混进了点时池：{sel}"


def test_length_gate_matches_the_training_gate():
    """训练段内 ≤240 根的标的不许进池（与 walk_forward 那条 `>240` 同一条闸）。"""
    panel, idx = _panel(["510300", "510301"], n_bars=900)
    panel["510301"] = panel["510301"].iloc[650:]      # 段内只剩 250 根以下
    sel, read = select_pit_pool(panel, idx[300], idx[800], min_share=0.0)
    assert sel == ["510300"], f"短历史标的进了池：{sel} / {read}"


def test_min_share_is_a_real_share_gate():
    """只在本段后半部分可投的标的：过闸占比落在 (0.4,0.6) ⇒ 0.6 该拒、0.4 该收。"""
    panel, idx = _panel(["510300"], n_bars=900)
    tr_s, tr_e = idx[300], idx[899]
    cut = idx[600]                    # 段内前一半（300~599）不可投
    panel["510300"].loc[panel["510300"].index < cut, "amount"] = 1.0
    share = float(tradable_mask(panel["510300"]).loc[tr_s:tr_e].mean())
    assert 0.4 < share < 0.6, f"夹具几何不对（share={share:.3f}），这格测不出门槛"
    hi, _ = select_pit_pool(panel, tr_s, tr_e, min_share=0.6)
    lo, _ = select_pit_pool(panel, tr_s, tr_e, min_share=0.4)
    assert hi == [] and lo == ["510300"], \
        f"min_share 是空挡（hi={hi} lo={lo} share={share:.3f}）"


# ========== ⑥ FoldUniverse ==========

def test_fold_universe_uses_each_codes_own_first_bar():
    idx = pd.bdate_range(START, periods=900)
    panel = {"510300": _df(idx, 0.012, 2e8, seed=0),
             "515300": _df(idx[300:], 0.012, 2e8, seed=1)}
    uni = FoldUniverse(panel, list(panel))
    assert uni.get_tradable_at(idx[310]) == ["510300"], \
        "把未满上市期的代码放进了可投集合"
    assert uni.get_tradable_at(idx[800]) == ["510300", "515300"]
    assert set(uni.get_tradable_at(idx[800])) <= set(panel), "宇宙漏出了池外代码"


# ========== ⑦ ⑧ ⑨ 主循环 ==========

def _factor_fn(pool, idx, fold=None):
    impl = {code: {"factor": df["close"].pct_change(5), "ic": 0.05}
            for code, df in pool.items()}
    return [{"name": f"fake_{fold}", "expr": "pct_change(close,5)",
             "mean_ic": 0.05, "icir": 0.5, "impl": impl, "source": "simple"}]


def _backtest_fn(pool, signals, universe, risk):
    idx = signals.index.unique()
    eq = pd.DataFrame({"equity": 10_000 * np.exp(np.linspace(1e-4, 3e-4,
                                                             len(idx)))},
                      index=idx)
    return {"equity": eq, "trades": pd.DataFrame(),
            "stats": {"总收益率": "10%", "夏普比率": "1.0",
                      "最大回撤": "-5%", "交易次数": "3"}}


def test_two_arms_differ_and_are_labelled(monkeypatch):
    """点时臂 vs 今日臂同一次调用必须**不等**（宽度、标签、factor_fn 看到的标的），
    否则接线就是空操作。"""
    wide, idx = _panel([f"5103{i:02d}" for i in range(12)],
                       n_bars=wf_pool_bars())
    today = {c: wide[c] for c in list(wide)[:3]}
    seen = []

    def factor_fn(p, i, fold=None):
        seen.append(set(p))
        return _factor_fn(p, i, fold=fold)

    monkeypatch.setitem(WALK_FORWARD, "pit_pool", _pit())
    wf_pit = walk_forward_run(today, None, factor_fn, _backtest_fn,
                              wide_pool=wide)
    pit_seen = [set(s) for s in seen]
    seen.clear()
    wf_today = walk_forward_run(today, None, factor_fn, _backtest_fn,
                                wide_pool=None)
    assert wf_pit["folds"] and wf_today["folds"], "两臂都得真跑出折行"
    assert all(r["池口径"] == "点时" for r in wf_pit["folds"])
    assert all(r["挖掘层宽度"] == 12 for r in wf_pit["folds"])
    assert all(r["日均厚"] is not None for r in wf_pit["folds"])
    assert all(r["池口径"] == "今日" for r in wf_today["folds"])
    assert all(r["挖掘层宽度"] == 3 for r in wf_today["folds"])
    assert any(s - set(today) for s in pit_seen), \
        "点时臂的 factor_fn 仍只看到今日那 3 只 ⇒ 宽面板没喂进挖掘"


def test_pit_enabled_without_wide_panel_is_loud(monkeypatch, capsys):
    """开关开着但没传宽面板 ⇒ 出声告知泄漏入口未关，折表仍标「今日」。"""
    monkeypatch.setitem(WALK_FORWARD, "pit_pool", _pit())
    wide, idx = _panel([f"5103{i:02d}" for i in range(4)],
                       n_bars=wf_pool_bars())
    wf = walk_forward_run(wide, None, _factor_fn, _backtest_fn,
                          wide_pool=None)
    out = capsys.readouterr().out
    assert "泄漏入口未关" in out
    assert all(r["池口径"] == "今日" for r in wf["folds"])


def test_empty_pit_pool_skips_the_fold_with_a_reason(monkeypatch, capsys):
    """点时池在该段挑不出标的 ⇒ 本折跳过并出声，不拿空池假装跑了一折。"""
    monkeypatch.setitem(WALK_FORWARD, "pit_pool", _pit(min_share=0.999))
    panel, idx = _panel([f"5103{i:02d}" for i in range(4)],
                        n_bars=wf_pool_bars())
    # 成交额整段打到门槛之下 ⇒ 每一折的点时池都挑不出标的（share 恒 0）
    for c in panel:
        panel[c]["amount"] = 1.0
    wf = walk_forward_run(panel, None, _factor_fn, _backtest_fn,
                          wide_pool=panel)
    out = capsys.readouterr().out
    assert "本折跳过" in out and wf["folds"] == [], \
        f"空池本该整折跳过：folds={wf['folds']}"
