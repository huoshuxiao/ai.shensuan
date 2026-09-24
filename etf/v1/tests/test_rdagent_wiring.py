# -*- coding: utf-8 -*-
"""RD-Agent(Q) 官方循环的三处衔接回归：coding 轮数注入、数据新鲜度体检、失败轮回收

这些链路都发生在子进程与真实文件系统边界上，此前只能靠一轮 ≈50min 的真实
循环才暴露（09-22 的 CoSTEER 轮数耗尽、09-23 的超时轮颗粒无收），故在此把
判据固定成离线用例：构造目录 mtime 与假的子进程退出码即可复现。
"""

import json
import os
import time

import pytest

import config
import official_rdagent as o
import multi_source_mining as msm


# ---------------- coding 演化轮数注入 ----------------

def test_costeer_max_loop_injected_into_driver_env():
    """ETF 线把 CoSTEER_MAX_LOOP 配成 8，必须真的出现在子进程环境里。

    驱动侧 `load_dotenv(".env")` 默认不覆盖已有环境变量，所以这里给的值
    压过工作区 .env 那份手改的 4 —— 这是 09-22 coding 不收敛的单一主旋钮。
    """
    assert config.RDAGENT_COSTEER_MAX_LOOP == "8"
    assert o._ENV_DRIVER["CoSTEER_MAX_LOOP"] == "8"
    # 环境隔离位仍要在（user-site 与 rdagent 依赖树版本冲突）
    assert o._ENV_DRIVER["PYTHONNOUSERSITE"] == "1"


def test_no_injection_when_unset(monkeypatch):
    """配置留空表示沿用 .env，不得凭空造出一个 CoSTEER 变量盖住本地设置"""
    monkeypatch.setattr(o, "RDAGENT_COSTEER_MAX_LOOP", "")
    assert "CoSTEER_MAX_LOOP" not in o._driver_env()
    monkeypatch.setattr(o, "RDAGENT_COSTEER_MAX_LOOP", "9")
    assert o._driver_env()["CoSTEER_MAX_LOOP"] == "9"


# ---------------- 数据新鲜度体检 ----------------

@pytest.fixture
def rdagent_tree(tmp_path, monkeypatch):
    """搭一棵最小目录树：行情源 csv + qlib bin 日历 + coding 面板 h5"""
    src = tmp_path / "universe_all"
    src.mkdir()
    csv = src / "510050_daily.csv"
    csv.write_text("date,open,high,low,close,volume\n")
    provider = tmp_path / "qlib" / "qlib_data" / "cn_data"
    (provider / "calendars").mkdir(parents=True)
    day_txt = provider / "calendars" / "day.txt"
    day_txt.write_text("2026-09-22\n")
    out = tmp_path / "rdagent_output"
    panel = out / "git_ignore_folder" / "factor_implementation_source_data"
    panel.mkdir(parents=True)
    (panel / "daily_pv.h5").write_bytes(b"x")
    monkeypatch.setattr(o, "RDAGENT_SOURCE_DIR", str(src))
    monkeypatch.setattr(o, "RDAGENT_QLIB_PROVIDER", str(provider))
    monkeypatch.setattr(o, "RDAGENT_OUTPUT_DIR", str(out))
    return {"src": src, "csv": csv, "day_txt": day_txt, "out": out,
            "panel": panel / "daily_pv.h5"}


def _set_mtime(path, ts):
    os.utime(path, (ts, ts))


def test_data_checks_pass_when_artifacts_are_newer(rdagent_tree):
    """bin 与面板都晚于行情缓存 → 两项检查都过，detail 打印各自末次时间"""
    t = rdagent_tree
    base = time.time()
    _set_mtime(t["csv"], base - 3600)
    _set_mtime(t["day_txt"], base - 600)
    _set_mtime(t["panel"], base - 300)
    checks = {n: (ok, d) for n, ok, d in o._rdagent_data_checks(str(t["out"]))}
    assert checks["qlib bin 新鲜度"][0] is True
    assert checks["源数据面板 daily_pv.h5"][0] is True
    assert "末次 dump" in checks["qlib bin 新鲜度"][1]


def test_data_checks_flag_stale_bin_and_panel(rdagent_tree):
    """行情缓存刷新在两个产物之后 → 双双判负，并把该跑的重建命令写进说明

    这正是「拉了最新数据却忘了重建 bin/面板」的情形：循环会在旧面板上
    编码，白烧一轮 LLM 时间，所以必须判❌而不是打一行警告。
    """
    t = rdagent_tree
    base = time.time()
    _set_mtime(t["day_txt"], base - 7200)
    _set_mtime(t["panel"], base - 3600)
    _set_mtime(t["csv"], base)                       # 行情最新，产物全旧
    checks = {n: (ok, d) for n, ok, d in o._rdagent_data_checks(str(t["out"]))}
    bin_ok, bin_detail = checks["qlib bin 新鲜度"]
    h5_ok, h5_detail = checks["源数据面板 daily_pv.h5"]
    assert bin_ok is False and "dump_qlib_bin.py" in bin_detail
    assert h5_ok is False and "pregen_source_data.py" in h5_detail
    # 面板命令要把本线 provider 与工作区带上，照抄即可执行
    assert t["src"].parent.name in str(t["src"])
    assert "QLIB_PROVIDER_URI=" + str(o.RDAGENT_QLIB_PROVIDER) in h5_detail
    assert str(t["out"]) in h5_detail


def test_data_checks_only_ignore_non_daily_csv(rdagent_tree):
    """universe 缓存等非行情文件的时间不参与比对，否则体检天天误报过期"""
    t = rdagent_tree
    base = time.time()
    _set_mtime(t["csv"], base - 600)                    # 日线缓存是旧的
    _set_mtime(t["day_txt"], base - 300)
    _set_mtime(t["panel"], base - 300)
    (t["src"] / "etf_universe_cache.csv").write_text("code\n510050\n")
    _set_mtime(t["src"] / "etf_universe_cache.csv", base)   # 只有它在动
    assert all(ok for _n, ok, _d in o._rdagent_data_checks(str(t["out"])))


def test_data_checks_skipped_without_source_dir(monkeypatch):
    """股票线用社区全市场包、无本地 csv 源 → 整组跳过，不制造假失败"""
    monkeypatch.setattr(o, "RDAGENT_SOURCE_DIR", "")
    assert o._rdagent_data_checks("/tmp/whatever") == []
    monkeypatch.setattr(o, "RDAGENT_SOURCE_DIR", "/nonexistent/dir")
    assert o._rdagent_data_checks("/tmp/whatever") == []


def test_preflight_carries_freshness_items(monkeypatch, rdagent_tree):
    """新检查必须真的挂在 rdagent_preflight 的检查表里（否则永远不生效）"""
    t = rdagent_tree
    base = time.time()
    _set_mtime(t["csv"], base)
    _set_mtime(t["day_txt"], base - 600)
    _set_mtime(t["panel"], base - 600)
    monkeypatch.setattr(o, "_find_conda", lambda: None)
    monkeypatch.setattr(o, "_docker_access", lambda: (False, False, "跳过"))
    names = [n for n, _ok, _d in o.rdagent_preflight(str(t["out"]))]
    assert "qlib bin 新鲜度" in names
    assert "源数据面板 daily_pv.h5" in names


# ---------------- 体检拦住时不得拉起循环 ----------------

class _Recorder:
    """记录是否真走到了拉子进程那一步（体检判负时必须一步都不走）"""

    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append(a)
        raise AssertionError("不该被调用")


def _stub_env(monkeypatch, o):
    """把体检里的外部探测全部换成桩，避免测试真去跑 conda/docker 探针"""
    monkeypatch.setattr(o, "_find_conda", lambda: "/bin/true")
    monkeypatch.setattr(o, "_env_probe", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(o, "_docker_access", lambda: (True, False, "ok"))
    monkeypatch.setattr(o, "_sandbox_image_ready", lambda: (True, "img"))


# ---------------- 体检拦住时不得拉起循环 ----------------

def test_stale_data_blocks_subprocess(monkeypatch, rdagent_tree):
    """行情在 bin 之后更新过 → 体检判负，子进程一次都不拉

    挡住的是「拿旧面板跑一轮 ≈50min 循环」这种最贵的错误，所以必须是
    阻塞级（❌ 进 missing 列表）而不是一行警告。
    """
    t = rdagent_tree
    base = time.time()
    (t["out"] / ".env").write_text("LITELLM_CHAT_MODEL=x\n")   # LLM 端点判据
    _set_mtime(t["day_txt"], base - 6000)
    _set_mtime(t["panel"], base - 5000)
    _set_mtime(t["csv"], base)
    _stub_env(monkeypatch, o)
    rec = _Recorder()
    monkeypatch.setattr(o.subprocess, "Popen", rec)
    assert o.try_official_rdagent(str(t["out"])) is None
    assert rec.calls == []
    # 新鲜度过关后同一条路就能走到拉起（证明挡住它的确是这两项检查）
    _set_mtime(t["day_txt"], base + 10)
    _set_mtime(t["panel"], base + 10)
    o.try_official_rdagent(str(t["out"]))
    assert len(rec.calls) == 1


def test_fresh_data_checks_pass_after_rebuild(rdagent_tree):
    """重建产物（把 bin/面板时间推到行情之后）后，两项检查转绿且给出末次时间"""
    t = rdagent_tree
    base = time.time()
    _set_mtime(t["csv"], base)
    _set_mtime(t["day_txt"], base + 60)
    _set_mtime(t["panel"], base + 60)
    checks = {n: ok for n, ok, _d in o._rdagent_data_checks(str(t["out"]))}
    assert checks == {"qlib bin 新鲜度": True, "源数据面板 daily_pv.h5": True}


# ---------------- 失败轮也要回收产物 ----------------

class _FakeProc:
    """非零退出的驱动子进程：stdout 空、wait 立返"""

    def __init__(self, *a, returncode=1, **kw):
        self.returncode = returncode
        self.stdout = iter([])

    def kill(self):
        pass

    def wait(self):
        return self.returncode


def _factors_json(path):
    json.dump([{"name": "5_day_SMA", "expr": "ma(df,5)", "mean_ic": 0.0085,
                "icir": 0.12, "formulation": "\\mathrm{SMA}_{5}"},
               {"name": "NoExpr", "expr": "", "mean_ic": 0.0}],
              open(path, "w", encoding="utf-8"), ensure_ascii=False)


def test_nonzero_exit_still_recovers(monkeypatch, tmp_path):
    """循环退出码非 0 时，驱动 finally 已写好 factors.json，回收必须交回主线

    早先这里 return None，等于把已烧掉的 LLM 时间整份丢掉——ETF 线因子库
    0 条 official 的直接成因之一。
    """
    out = tmp_path / "rd"
    out.mkdir()
    _factors_json(out / "factors.json")
    monkeypatch.setattr(o, "rdagent_preflight",
                        lambda d=None: [("all", True, "ok")])
    monkeypatch.setattr(o, "_find_conda", lambda: "/bin/true")
    monkeypatch.setattr(o, "_docker_access", lambda: (True, False, "ok"))
    monkeypatch.setattr(o.subprocess, "Popen",
                        lambda *a, **kw: _FakeProc(returncode=1))
    got = o.try_official_rdagent(str(out))
    assert [f["name"] for f in got] == ["5_day_SMA", "NoExpr"]
    assert got[0]["expr"] == "ma(df,5)"
    assert got[0]["mean_ic"] == pytest.approx(0.0085)
    # LaTeX 原式随因子带到因子库/报告，供人工核对 DSL 翻译是否走样
    assert got[0]["formulation"] == "\\mathrm{SMA}_{5}"


def test_timeout_still_recovers(monkeypatch, tmp_path):
    """超时轮同理：kill 之后仍走回收，不留空手"""
    out = tmp_path / "rd"
    out.mkdir()
    _factors_json(out / "factors.json")
    monkeypatch.setattr(o, "rdagent_preflight",
                        lambda d=None: [("all", True, "ok")])
    monkeypatch.setattr(o, "_find_conda", lambda: "/bin/true")
    monkeypatch.setattr(o, "_docker_access", lambda: (True, False, "ok"))
    monkeypatch.setattr(o, "RDAGENT_TIMEOUT_SEC", -1)     # 首轮即判超时
    monkeypatch.setattr(o.subprocess, "Popen",
                        lambda *a, **kw: _FakeProc(returncode=-9))
    assert len(o.try_official_rdagent(str(out))) == 2


def test_launch_failure_recovers_previous_round(monkeypatch, tmp_path):
    """子进程根本没拉起来时，沿用历轮攒下的定义（日志会打印其落盘时间）"""
    out = tmp_path / "rd"
    out.mkdir()
    _factors_json(out / "factors.json")
    monkeypatch.setattr(o, "rdagent_preflight",
                        lambda d=None: [("all", True, "ok")])
    monkeypatch.setattr(o, "_find_conda", lambda: "/bin/true")
    monkeypatch.setattr(o, "_docker_access", lambda: (True, False, "ok"))

    def _boom(*a, **kw):
        raise OSError("docker 组没生效")

    monkeypatch.setattr(o.subprocess, "Popen", _boom)
    assert len(o.try_official_rdagent(str(out))) == 2


def test_no_artifacts_at_all_returns_none(monkeypatch, tmp_path):
    """既没跑成也没有历史产物时如实返回 None，不编造因子"""
    out = tmp_path / "rd"
    out.mkdir()
    monkeypatch.setattr(o, "rdagent_preflight",
                        lambda d=None: [("all", True, "ok")])
    monkeypatch.setattr(o, "_find_conda", lambda: "/bin/true")
    monkeypatch.setattr(o, "_docker_access", lambda: (True, False, "ok"))
    monkeypatch.setattr(o.subprocess, "Popen",
                        lambda *a, **kw: _FakeProc(returncode=1))
    assert o.try_official_rdagent(str(out)) is None


# ---------------- official 源 → 因子库的入口衔接 ----------------

def test_official_source_attaches_and_survives_into_library(monkeypatch,
                                                            daily_pool):
    """开关打开后，回收的官方定义要经 _attach_impl 变成主线因子（source=official）

    这是「factors.json 有货但因子库 0 条 official」那条断链的正面用例。
    """
    import official_rdagent as real
    monkeypatch.setattr(msm, "RDAGENT_USE_OFFICIAL_FALLBACK", True)
    monkeypatch.setattr(real, "try_official_rdagent",
                        lambda *a, **kw: [{"name": "5_day_SMA",
                                           "expr": "ma(df,5)",
                                           "mean_ic": 0.0085,
                                           "formulation": "SMA5"}])
    got = msm._run_official(daily_pool)
    assert len(got) == 1
    item = got[0]
    assert item["source"] == "official"
    assert item["formulation"] == "SMA5"
    assert set(item["impl"]) == set(daily_pool)
    assert item["impl"][next(iter(daily_pool))]["factor"].notna().sum() > 0


def test_official_source_skipped_when_disabled(monkeypatch, daily_pool):
    monkeypatch.setattr(msm, "RDAGENT_USE_OFFICIAL_FALLBACK", False)
    assert msm._run_official(daily_pool) == []


def test_expr_less_definition_dropped(monkeypatch, daily_pool):
    """翻译不出 DSL 的定义不进主线（没有 expr 就无法回测），但也不炸"""
    monkeypatch.setattr(msm, "RDAGENT_USE_OFFICIAL_FALLBACK", True)
    import official_rdagent as real
    monkeypatch.setattr(real, "try_official_rdagent",
                        lambda *a, **kw: [{"name": "Weird", "expr": ""}])
    assert msm._run_official(daily_pool) == []
