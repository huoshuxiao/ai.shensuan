# -*- coding: utf-8 -*-
"""段级断点续传（run_checkpoint）的十二张判据。

每条各自钉住一种"下一场会以什么样子骗过你"的失败模式：
1. 默认关 ⇒ 行为与这套机器存在前逐字节同（不许有人以为续传是常态）；
2. 全命中 ⇒ 容器循环一次都不许重起，且**试验数一分都不许再加**
   （TrialCounter 是 DSR 运气门槛的分母，重跑加第二遍=判决被重启行为改掉）；
3. 折 2 崩在半路 ⇒ 只补折 2，折 1 既不重挖也不重记，且折 1 的 n_trials
   读数与崩之前那场**逐位相同**（门槛连续性）；
4. 数据指纹一变 ⇒ 整批作废、全部重挖（挡住"拿昨天的因子冒充今天挖的"）。
⑤–⑨ 钉缓存可读性、指纹入参与丙-2 那颗接线开关；
⑩⑪⑫（09-30 新增）钉"作废改成逐条按段落作用域判"：只拨切折几何不许顺带烧掉主线
   那一小时四十分钟，而这层豁免**只**豁免 `FOLD_ONLY_KEYS` 那四项 —— 数据推进一天
   仍要把主线一起作废，拿 main 口径存的条目也不许被 full 口径白拿。
"""

import pickle

import pytest

from dsr import TrialCounter
from run_checkpoint import RunCheckpoint, make_fingerprint
from synth import make_daily_pool, wf_pool_bars
from test_walk_forward import _fake_backtest, _fake_factor
from walk_forward import make_splits, walk_forward_run


def _pool_and_fp(n_codes=3):
    pool = make_daily_pool(n_codes=n_codes, n_bars=wf_pool_bars())
    ts = pool[next(iter(pool))].index
    return pool, make_fingerprint(list(pool), ts)


def _ckpt(path, fp, enabled=True):
    return RunCheckpoint(enabled=enabled, path=str(path)).arm(fp)


def _counting_factor(calls, boom_on=None):
    def factor_fn(p, idx, fold=None):
        if boom_on is not None and fold == boom_on:
            raise RuntimeError(f"模拟折 {fold} 跑到一半被杀")
        calls.append(fold)
        return [_fake_factor(p, tag=f"gp_fold{fold}")]
    return factor_fn


def test_disabled_writes_no_bytes_and_remines_everything(tmp_path):
    """①默认关：一个字节都不落、每折照旧重挖 ⇒ 日常日更的行为没被动过"""
    pool, fp = _pool_and_fp()
    tc = TrialCounter(path=str(tmp_path / "tc.json"))
    calls = []
    n_splits = len(make_splits(pool[next(iter(pool))].index))
    ck = _ckpt(tmp_path / "ck.pkl", fp, enabled=False)
    walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=tc, checkpoint=ck)
    assert len(calls) == n_splits, "关掉续传时每一折都必须真挖"
    assert not (tmp_path / "ck.pkl").exists(), "关掉时不许产生任何缓存字节"
    assert tc.count == n_splits


def test_full_replay_remines_nothing_and_adds_no_trials(tmp_path, capsys):
    """②全命中：第二场一次容器都不起，因子表逐字段同，且账本不再增长"""
    pool, fp = _pool_and_fp()
    ck_path = tmp_path / "ck.pkl"
    tc = TrialCounter(path=str(tmp_path / "tc.json"))

    calls1 = []
    wf1 = walk_forward_run(pool, None, _counting_factor(calls1),
                           _fake_backtest, trial_counter=tc,
                           checkpoint=_ckpt(ck_path, fp))
    after1 = tc.count
    assert len(calls1) == len(wf1["folds"]) and after1 > 0

    calls2 = []
    wf2 = walk_forward_run(pool, None, _counting_factor(calls2),
                           _fake_backtest, trial_counter=tc,
                           checkpoint=_ckpt(ck_path, fp))
    out = capsys.readouterr().out
    assert calls2 == [], "命中续传却仍重起了容器循环 ⇒ 缓存在场内形同不存在"
    assert tc.count == after1, "重复累加试验数：DSR 的分母被重启行为改掉了"
    assert wf2["folds"] == wf1["folds"], "续传把折级判据读数改写了"
    assert wf2["merged_oos_dsr"] == wf1["merged_oos_dsr"]
    assert "整批作废" not in out
    assert out.count("♻️ 续传命中") == len(wf1["folds"])
    with open(ck_path, "rb") as f:
        assert len(pickle.load(f)["entries"]) == len(wf1["folds"])


def test_crash_in_second_fold_resumes_only_that_fold(tmp_path):
    """③核心场景：折 2 崩在半路 ⇒ 重启只补折 2，折 1 不重挖也不重记"""
    pool, fp = _pool_and_fp()
    ck_path = tmp_path / "ck.pkl"
    tc = TrialCounter(path=str(tmp_path / "tc.json"))

    with pytest.raises(RuntimeError):
        walk_forward_run(pool, None,
                         _counting_factor([], boom_on=2), _fake_backtest,
                         trial_counter=tc, checkpoint=_ckpt(ck_path, fp))
    assert tc.count == 1, "折 1 该记 1 笔；折 2 崩在开挖前后都不许留第二笔"
    with open(ck_path, "rb") as f:
        entries = pickle.load(f)["entries"]
    assert list(entries) == ["折 1 因子"], "缓存条目必须恰好等于已完成的那一段"

    calls = []
    wf = walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                          trial_counter=tc, checkpoint=_ckpt(ck_path, fp))
    n_splits = len(make_splits(pool[next(iter(pool))].index))
    assert calls == list(range(2, n_splits + 1)), \
        f"续传后只该重挖折 2..{n_splits}，实际挖了 {calls}"
    assert tc.count == n_splits, "补算的那几折各记一笔，不多不少"
    # 折 1 的运气门槛用的是它当时的累计 N：续传不许把它推高
    assert wf["folds"][0]["n_trials"] == 1
    assert [r["fold"] for r in wf["folds"]] == list(range(1, n_splits + 1))


def test_fingerprint_change_voids_the_whole_cache(tmp_path, capsys):
    """④数据推进一天 ⇒ 指纹变 ⇒ 上一场条目整批作废，全部重挖且出声"""
    pool, fp = _pool_and_fp()
    ck_path = tmp_path / "ck.pkl"
    tc = TrialCounter(path=str(tmp_path / "tc.json"))
    walk_forward_run(pool, None, _counting_factor([]), _fake_backtest,
                     trial_counter=tc, checkpoint=_ckpt(ck_path, fp))

    pool2 = make_daily_pool(n_codes=4, n_bars=wf_pool_bars())
    fp2 = make_fingerprint(list(pool2), pool2[next(iter(pool2))].index)
    assert fp2 != fp
    calls = []
    walk_forward_run(pool2, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=tc, checkpoint=_ckpt(ck_path, fp2))
    out = capsys.readouterr().out
    assert len(calls) == len(make_splits(pool2[next(iter(pool2))].index)), \
        "池子已换（多一只标的）却复用了旧池的因子 ⇒ 作废闸没牙"
    assert "整批作废" in out, "作废必须出声，静默重算会让人以为续传失灵"


def test_entry_exists_only_after_the_trial_is_booked(tmp_path):
    """不变式的正面读数：只算完、尚未落账 ⇒ 磁盘上没有条目，下次必须重算"""
    pool, fp = _pool_and_fp()
    ck_path = tmp_path / "ck.pkl"
    ck = _ckpt(ck_path, fp)
    got = ck.resolve("折 9 因子", lambda: [{"name": "x"}], label="只算不记")
    assert got == [{"name": "x"}]
    assert not ck_path.exists(), "resolve 之后不许有字节落盘"
    # 现在补上 add + record，条目才出现
    tc = TrialCounter(path=str(tmp_path / "tc.json"))
    tc.add(1)
    ck.record("折 9 因子", got, tc=tc)
    assert ck_path.exists()
    again = RunCheckpoint(enabled=True, path=str(ck_path)).arm(fp)
    hit = again.resolve("折 9 因子", lambda: pytest.fail("条目已存在却走了现算"),
                        label="折 9")
    assert hit == [{"name": "x"}] and again.replayed


def test_unreadable_cache_is_discarded_not_trusted(tmp_path):
    """缓存文件坏掉 / 只有 .part 残留 ⇒ 宁可从零算，也不拿半口血的对象当因子"""
    pool, fp = _pool_and_fp()
    ck_path = tmp_path / "ck.pkl"
    ck_path.write_bytes(b"\x80\x04 garbage not a pickle")
    calls = []
    n = len(make_splits(pool[next(iter(pool))].index))
    walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=TrialCounter(path=str(tmp_path / "tc.json")),
                     checkpoint=_ckpt(ck_path, fp))
    assert len(calls) == n, "读不出的缓存被当空跑批用了 ⇒ 全折必须重挖"
    # 只有 .part 残留（写一半被杀）：它不是缓存，一次都不许被读到
    (tmp_path / "ck2.pkl.part").write_bytes(b"\x80\x04 half written")
    assert RunCheckpoint(enabled=True,
                         path=str(tmp_path / "ck2.pkl")).arm(fp).entries == {}


def test_fingerprint_covers_the_inputs_that_change_a_fold(tmp_path):
    """指纹的十二项入参各自都要能挪动读数（少一项就是那道作废闸的缺口）"""
    pool = make_daily_pool(n_codes=3, n_bars=wf_pool_bars())
    ts = pool[next(iter(pool))].index
    base = make_fingerprint(list(pool), ts)
    assert base["ts_last"] == str(ts[-1])
    # 池子换一只
    assert make_fingerprint(list(pool) + ["510309"], ts) != base
    # 数据多一天
    assert make_fingerprint(list(pool), ts[:-1]) != base
    # 同一场里再来一次必须逐位相同（否则续传永远命中不了）
    assert make_fingerprint(list(pool), ts) == base


def test_official_in_fold_switch_is_not_a_no_op(tmp_path, monkeypatch, capsys):
    """⑧丙-2 那颗开关不许空转：只翻 MULTI_SOURCE["official_in_fold"]、数据一格不动，
    指纹必须变 ⇒ 上一场条目整批作废、每折真重挖。
    这是 09-30 自查抓到的洞：折内停 official 是**接线**改动，指纹里原本只有数据与切折
    几何 ⇒ `--resume` 会"续传命中"把旧因子交回来，代码改了、折表一个字节没变。"""
    from run_checkpoint import MULTI_SOURCE as _ms_cfg
    monkeypatch.setitem(_ms_cfg, "official_in_fold", True)   # 夹具自己拨回"折内吃 official"
    pool, fp_on = _pool_and_fp()
    assert fp_on["official_in_fold"] is True, "夹具前提：指纹要读得到这颗开关"
    ck_path = tmp_path / "ck.pkl"
    tc = TrialCounter(path=str(tmp_path / "tc.json"))
    walk_forward_run(pool, None, _counting_factor([]), _fake_backtest,
                     trial_counter=tc, checkpoint=_ckpt(ck_path, fp_on))

    monkeypatch.setitem(_ms_cfg, "official_in_fold", False)
    fp_off = make_fingerprint(list(pool), pool[next(iter(pool))].index)
    assert fp_off != fp_on, "只翻接线、指纹不动 ⇒ 作废闸看不见这次改动"

    capsys.readouterr()
    calls = []
    walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=tc, checkpoint=_ckpt(ck_path, fp_off))
    out = capsys.readouterr().out
    n_splits = len(make_splits(pool[next(iter(pool))].index))
    assert len(calls) == n_splits, "翻了开关却一折都没重挖 ⇒ 丙-2 是空操作"
    assert "整批作废" in out, "作废必须出声（静默重算会让人以为续传失灵）"
    assert "续传命中" not in out


def test_frozen_pre_fix_fingerprint_would_have_missed_it(tmp_path, monkeypatch):
    """⑨反证（有牙的那一半）：把 official_in_fold 从指纹里摘掉（= 复刻修复前的指纹形状，
    两边都不带这个键）⇒ 翻开关后指纹逐位相同 ⇒ 「续传命中」把旧因子当本折现挖的交回来、
    一折都不重挖。钉的是「这个键为什么必须在指纹里」。"""
    import pickle
    import run_checkpoint as rc
    pool = make_daily_pool(n_codes=3, n_bars=wf_pool_bars())
    ts = pool[next(iter(pool))].index
    real_fp = rc.make_fingerprint

    def _stripped(codes, all_ts):        # 修复前的指纹：没有 official_in_fold 这一项
        d = dict(real_fp(codes, all_ts))
        d.pop("official_in_fold", None)
        return d

    monkeypatch.setattr(rc, "make_fingerprint", _stripped)
    ck_path = tmp_path / "ck.pkl"
    tc = TrialCounter(path=str(tmp_path / "tc.json"))
    calls0 = []
    walk_forward_run(pool, None, _counting_factor(calls0), _fake_backtest,
                     trial_counter=tc, checkpoint=_ckpt(ck_path, _stripped(list(pool), ts)))
    assert len(calls0) == len(make_splits(ts)), "第一臂就没把缓存写进去 ⇒ 反证无从谈起"

    monkeypatch.setitem(rc.MULTI_SOURCE, "official_in_fold", False)
    blob = pickle.load(open(ck_path, "rb"))
    assert "official_in_fold" not in blob["fingerprint"]
    calls = []
    walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=tc, checkpoint=_ckpt(ck_path, _stripped(list(pool), ts)))
    assert calls == [], "摘掉键之后仍重挖了 ⇒ 这两臂本来就没对上，反证无效"


def _both_sides(tmp_path, fp):
    """造一枚「主线 + 折内」各存一条的缓存：主线那条按 main 口径裁，折内按 full 口径裁"""
    ck = _ckpt(tmp_path / "ck.pkl", fp)
    tc = TrialCounter(path=str(tmp_path / "tc.json"))
    tc.add(1)
    ck.record("主线多源因子", [{"name": "main"}], tc=tc, label="主线", scope="main")
    ck.record("折 1 因子", [{"name": "fold1"}], tc=tc, label="折 1")
    return ck


def test_fold_only_switch_spares_the_main_line(tmp_path, monkeypatch, capsys):
    """⑩段级作废的正读数：只拨隔离带宽度（embargo_bars，折内段落的输入、主线的无关量），
    折内条目整批作废，但主线那条**必须还活着** ⇒ 重铺折表不再顺带烧掉一小时四十分钟。
    钉的是 WP-1 这次改动本身：作废从"整批"改成"逐条按段落作用域判"。"""
    from config import WALK_FORWARD as _wf
    pool, fp1 = _pool_and_fp()
    assert fp1["embargo_bars"] == _wf["embargo_bars"]
    ck = _both_sides(tmp_path, fp1)
    assert set(ck.entries) == {"主线多源因子", "折 1 因子"}

    monkeypatch.setitem(_wf, "embargo_bars", _wf["embargo_bars"] + 1)
    fp2 = make_fingerprint(list(pool), pool[next(iter(pool))].index)
    assert fp2 != fp1, "夹具前提：这颗键真的进指纹"

    capsys.readouterr()
    ck2 = _ckpt(tmp_path / "ck.pkl", fp2)
    out = capsys.readouterr().out
    assert "整批作废" not in out, "主线那条还能用 ⇒ 不许再喊整批作废"
    assert "本场可用 1 条" in out, f"开闸读数没说出留了哪几条：{out}"
    assert set(ck2.entries) == {"主线多源因子"}, "折内条目该作废、主线条目该留下"

    # 主线这一段：命中，一次容器都不起
    got = ck2.resolve("主线多源因子",
                      lambda: pytest.fail("折几何变了就该重挖主线"),
                      label="主线", scope="main")
    assert got == [{"name": "main"}] and ck2.replayed, "主线续传失灵 ⇒ 白烧一场"

    # 折内那一段：条目早就不在了，走现算
    calls = []
    walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=TrialCounter(path=str(tmp_path / "tc2.json")),
                     checkpoint=ck2)
    n_splits = len(make_splits(pool[next(iter(pool))].index))
    assert len(calls) == n_splits, "翻了隔离带却复用旧折的因子 ⇒ 逐条作废没牙"


def test_main_scope_exemption_is_not_a_blanket_pass(tmp_path, monkeypatch, capsys):
    """⑪反证（有牙的那一半）：`scope="main"` 只豁免 FOLD_ONLY_KEYS 那五项，
    两种情形都必须照样判红——
    ⑪a 拿 main 口径存过的条目去问 full 口径 ⇒ 不作命中（口径不能白借）；
    ⑪b **数据**推进一天 ⇒ 主线那条一起作废（日更不许被折几何那层豁免护住）。
    少了这两格，⑩ 的正读数可以是"把闸整段关掉"换来的。"""
    pool, fp1 = _pool_and_fp()
    ck = _both_sides(tmp_path, fp1)

    # ⑪a：同一个条目，换个作用域问就不许给
    capsys.readouterr()
    got = ck.resolve("主线多源因子", lambda: [{"name": "现算"}],
                     label="主线（按折内口径问）", scope="full")
    out = capsys.readouterr().out
    assert got == [{"name": "现算"}], "main 存的条目被 full 口径白拿 ⇒ 豁免过头"
    assert not ck.replayed and "指纹与本段口径不符" in out

    # ⑪b：数据动一天 ⇒ 主线条目一起作废（日更链每天就是这一步）
    ts = pool[next(iter(pool))].index
    fp_day_later = make_fingerprint(list(pool), ts[:-1])
    assert fp_day_later != fp1
    capsys.readouterr()
    ck3 = _ckpt(tmp_path / "ck.pkl", fp_day_later)
    out = capsys.readouterr().out
    assert ck3.entries == {}, "数据推进一天后主线缓存仍在 ⇒ 幸存者泄漏挡不住"
    assert "整批作废" in out, "整批都不能用时必须照旧出声"


def test_pit_pool_switch_is_fold_scoped(tmp_path, monkeypatch, capsys):
    """⑫WP-2 那颗点时池开关不许空转，也不许拖主线陪葬：
    翻 `WALK_FORWARD["pit_pool"]` ⇒ 指纹变（折内条目整批作废、必须重挖），
    但主线那条按 main 口径仍命中。两个读数缺一都不算：
    只验前者 = 丙-2 那个洞重演（改了不认账）；只验后者 = 又回到整批作废的老账。"""
    from config import WALK_FORWARD as _wf
    pool, fp_on = _pool_and_fp()
    pit_on = dict(_wf.get("pit_pool") or {})
    assert fp_on["pit_pool"] != "off", f"夹具前提：开关要在指纹里，实际 {fp_on['pit_pool']}"
    ck = _both_sides(tmp_path, fp_on)

    monkeypatch.setitem(_wf, "pit_pool", {**pit_on, "enabled": False})
    fp_off = make_fingerprint(list(pool), pool[next(iter(pool))].index)
    assert fp_off != fp_on, "翻了点时池开关、指纹不动 ⇒ 作废闸看不见这次改动"

    capsys.readouterr()
    ck2 = _ckpt(tmp_path / "ck.pkl", fp_off)
    out = capsys.readouterr().out
    assert set(ck2.entries) == {"主线多源因子"}, "折内条目该作废 / 主线条目该留下"
    assert "整批作废" not in out

    got = ck2.resolve("主线多源因子",
                      lambda: pytest.fail("关掉点时池不该重挖主线"),
                      label="主线", scope="main")
    assert got == [{"name": "main"}] and ck2.replayed

    calls = []
    walk_forward_run(pool, None, _counting_factor(calls), _fake_backtest,
                     trial_counter=TrialCounter(path=str(tmp_path / "tc2.json")),
                     checkpoint=ck2)
    n_splits = len(make_splits(pool[next(iter(pool))].index))
    assert len(calls) == n_splits, "翻了开关却一折都没重挖 ⇒ 这颗开关空转"
    assert ck2.hits == 1 and ck2.misses == n_splits, \
        f"主线命中 1 段、折内现算 {n_splits} 段才对，实际 命中 {ck2.hits}/现算 {ck2.misses}"
