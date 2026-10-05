# -*- coding: utf-8 -*-
"""RD-Agent(Q) 官方循环的四处衔接回归：coding 轮数注入、驱动 PATH、数据新鲜度体检、失败轮回收

这些链路都发生在子进程与真实文件系统边界上，此前只能靠一轮 ≈50min 的真实
循环才暴露（09-22 的 CoSTEER 轮数耗尽、09-23 的超时轮颗粒无收、10-04 的
`python: not found` 让三场净增全 0），故在此把判据固定成离线用例：
构造目录 mtime、假的子进程退出码与真实子进程调用即可复现。
"""

import json
import os
import subprocess
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


# ---------------- 驱动 PATH：因子求值那句字面量 `python` 要落得到人 ----------------

def test_driver_env_path_prepend_conda_env_bin():
    """factor 求值跑的是字面量 `python`（FactorCoSTEERSettings.python_bin 默认值），
    它只能从驱动子进程的 PATH 里解析 ⇒ 环境自己的 `bin` 必须在里面。

    10-04 傍晚那场的执行反馈原文是 `/bin/sh: 1: python: not found`：只挂 condabin
    （那里只有 `conda` 一个文件）不够；本机 `/usr/bin` 只有 python3、没有 python。
    """
    conda = o._find_conda()
    if conda is None:
        pytest.skip("本机没有 conda，这条 PATH 注入分支不成立")
    env_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(conda))),
                           "envs", o.RDAGENT_CONDA_ENV, "bin")
    assert env_bin in o._ENV_DRIVER["PATH"].split(":"), o._ENV_DRIVER["PATH"]
    assert os.access(os.path.join(env_bin, "python"), os.X_OK)


def test_literal_python_command_runs_under_driver_env(tmp_path):
    """按 rdagent 那句调用的原样形（`python factor.py`，shell=True + 驱动环境）跑一遍"""
    probe = tmp_path / "factor.py"
    probe.write_text("import pathlib\npathlib.Path('ok.marker').write_text('1')\n",
                     encoding="utf-8")
    r = subprocess.run("python factor.py", shell=True, cwd=str(tmp_path),
                       env=o._ENV_DRIVER, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "not found" not in r.stderr + r.stdout
    assert (tmp_path / "ok.marker").exists()


# ---------------- CoSTEER 跨场知识库注入（甲-2，10-05 00:0x 落地） ----------------

_KB_READ_KEY = "CoSTEER_KNOWLEDGE_BASE_PATH"
_KB_WRITE_KEY = "CoSTEER_NEW_KNOWLEDGE_BASE_PATH"


def _fake_kb_pickle(path, cls_name, protocol=None):
    """写一份**顶层类名为 cls_name** 的真 pickle，用来喂版本判据（不手写行形）

    rdagent 只装在 conda 环境里、系统 python3.10 import 不到，所以这里在
    `sys.modules` 里挂一个同名假模块、把假类注册进去，让 `pickle.dumps` 自己
    吐出 `rdagent…knowledge_management` + `<cls_name>` —— 字节形态与真品一致；
    被测的 `_kb_pickle_class` 用 pickletools 只读 opcode、不 import 也不反序列化，
    所以这套造假足够真（且不带反序列化风险）。

    `protocol` 是给**两种 opcode 写法**留的：默认（本机 3.10＝协议 5）落
    `STACK_GLOBAL`，`protocol=2` 落 `GLOBAL`。rdagent 那句 dump 不传 protocol，
    所以两种都可能出现在真文件里，判据两种都得认。
    """
    import pickle
    import sys
    import types

    mod_name = "rdagent.components.coder.CoSTEER.knowledge_management"
    registered = []

    def _register(name, is_pkg):
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            if is_pkg:
                mod.__path__ = []        # 当作包，`import a.b.c` 才走得下去
            mod.__spec__ = None
            sys.modules[name] = mod
            registered.append(name)
        return mod

    parts = mod_name.split(".")
    for i in range(1, len(parts)):
        _register(".".join(parts[:i]), True)
    mod = _register(mod_name, False)

    class _Blob:                       # 载荷不重要：判据只看顶层类名
        pass

    _Blob.__module__ = mod_name
    _Blob.__qualname__ = cls_name
    setattr(mod, cls_name, _Blob)
    try:
        data = pickle.dumps(_Blob(), protocol=protocol)
    finally:
        for name in registered:
            sys.modules.pop(name, None)
    path.write_bytes(data)
    return path


def test_costeer_kb_blank_injects_nothing(monkeypatch):
    """留空＝一个键都不发（股票线那份 .env 没这行 ⇒ 与 10-04 之前逐字节相同）"""
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", "")
    env = o._driver_env()
    assert _KB_READ_KEY not in env and _KB_WRITE_KEY not in env
    assert o._COSTEER_KB_NOTE == ""


def test_costeer_kb_relative_path_anchors_to_output_dir(monkeypatch, tmp_path):
    """相对路径挂在本线 rdagent_output/ 底下，绝对路径照原样（两条换臂）"""
    out = tmp_path / "rdagent_output"
    out.mkdir()
    monkeypatch.setattr(o, "RDAGENT_OUTPUT_DIR", str(out))
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", "costeer_kb/kb.pkl")
    env = o._driver_env()
    want = str(out / "costeer_kb" / "kb.pkl")
    assert env[_KB_READ_KEY] == want and env[_KB_WRITE_KEY] == want

    absolute = str(tmp_path / "abs" / "kb.pkl")
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", absolute)
    env = o._driver_env()
    assert env[_KB_READ_KEY] == absolute and env[_KB_WRITE_KEY] == absolute


def test_costeer_kb_v2_file_is_read_and_written(monkeypatch, tmp_path):
    """正对照：读路径已有 V2 知识库 ⇒ 读写两键都发（这才是「记住上一场」那一档）

    两种 opcode 写法各来一枚：默认协议（本机 5＝STACK_GLOBAL，真品就是这副）
    与 protocol 2（GLOBAL）。第一版判据只认 `GLOBAL`，被这一格当场抓出来——
    真品读不出版本 ⇒ 每场降成只写不读，甲-2 白改。
    """
    for name, protocol in (("default", None), ("proto2", 2)):
        kb = _fake_kb_pickle(tmp_path / f"kb_{name}.pkl",
                             "CoSTEERKnowledgeBaseV2", protocol=protocol)
        assert o._kb_pickle_class(kb) == "CoSTEERKnowledgeBaseV2", (name, kb.read_bytes()[:60])
        monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", str(kb))
        env = o._driver_env()
        assert env[_KB_READ_KEY] == str(kb) == env[_KB_WRITE_KEY], name
        assert "V2" in o._COSTEER_KB_NOTE, (name, o._COSTEER_KB_NOTE)


def test_costeer_kb_incompatible_downgrades_to_write_only(monkeypatch, tmp_path):
    """负对照：读路径是旧版本 ⇒ 只发写键，本场从空库起步、收场覆写（探针 D 臂崩法）

    两种 opcode 写法各一枚（`GLOBAL` 与 `STACK_GLOBAL`），因为判据要**认出坏版本**
    才谈得上降级——认不出而返回 None 的话这条臂会假绿。
    """
    for name, protocol in (("default", None), ("proto2", 2)):
        kb = _fake_kb_pickle(tmp_path / f"bad_{name}.pkl",
                             "CoSTEERKnowledgeBaseV1", protocol=protocol)
        assert o._kb_pickle_class(kb) == "CoSTEERKnowledgeBaseV1", name
        monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", str(kb))
        env = o._driver_env()
        assert _KB_READ_KEY not in env, (
            name, "把 V1 当 V2 发出去＝rdagent 抛 ValueError、整场崩在 coding 之前")
        assert env[_KB_WRITE_KEY] == str(kb), name
        assert "⚠️" in o._COSTEER_KB_NOTE and "CoSTEERKnowledgeBaseV1" in o._COSTEER_KB_NOTE


def test_costeer_kb_unreadable_downgrades_without_raising(monkeypatch, tmp_path):
    """坏字节（截断/非 pickle）也只降为只写不读，不许在起场前抛"""
    kb = tmp_path / "broken.pkl"
    kb.write_bytes(b"this is not a pickle at all")
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", str(kb))
    env = o._driver_env()          # 抛异常即测试失败
    assert _KB_READ_KEY not in env and env[_KB_WRITE_KEY] == str(kb)


def test_costeer_kb_injection_is_the_teeth(monkeypatch, tmp_path):
    """牙：判据的差别必须来自那把旋钮本身（设了→发、摘了→不发、换坏版本→少发）"""
    kb = _fake_kb_pickle(tmp_path / "kb.pkl", "CoSTEERKnowledgeBaseV2")
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", str(kb))
    assert _KB_READ_KEY in o._driver_env()
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", "")
    assert _KB_READ_KEY not in o._driver_env()
    monkeypatch.setattr(o, "RDAGENT_COSTEER_KB_PATH", str(kb))
    for protocol in (None, 2):        # 同一把尺子量两种 opcode 写法的坏版本
        _fake_kb_pickle(kb, "CoSTEERKnowledgeBaseV1", protocol=protocol)
        assert _KB_READ_KEY not in o._driver_env(), protocol


@pytest.mark.skipif(o._find_env_python(o._find_conda()) is None,
                    reason="本机没有 rdagent 那个 conda 环境，消费侧对拍不成立")
def test_costeer_kb_keys_are_what_rdagent_reads(tmp_path):
    """键名拼写必须由**消费侧**盖章：本仓库发的这两个键，rdagent 真读得到。

    上面几格只证到「我们把键放进了子进程环境」，这一格拿环境里的真解释器
    import `CoSTEERSettings`（site-packages 的 pydantic-settings，前缀 `CoSTEER_`）
    现读一遍——同一次调用里读两回：带注入 ⇒ 两个字段是路径；把这两个键从
    `os.environ` 摘掉再实例化 ⇒ 回到 None。差异落在消费侧，才算接上。
    同一次调用里再用**真类**落一枚真字节知识库，交回父进程读本仓库那把尺子
    （造假字节只证到 opcode 形态，这一格证到 rdagent 自己 dump 出来的东西）。
    """
    env_py = o._find_env_python(o._find_conda())
    kb = tmp_path / "kb.pkl"
    code = (
        "from rdagent.components.coder.CoSTEER.config import CoSTEERSettings\n"
        "a = CoSTEERSettings()\n"
        "import os\n"
        "os.environ.pop('CoSTEER_KNOWLEDGE_BASE_PATH', None)\n"
        "os.environ.pop('CoSTEER_NEW_KNOWLEDGE_BASE_PATH', None)\n"
        "b = CoSTEERSettings()\n"
        "print('WITH=%s|%s' % (a.knowledge_base_path, a.new_knowledge_base_path))\n"
        "print('WITHOUT=%s|%s' % (b.knowledge_base_path, b.new_knowledge_base_path))\n"
        # 顺手用**真类**落一枚真字节的知识库（空组件表⇒不打嵌入 API，见 10-04 探针 C/F 臂），
        # 交回父进程用本仓库那把尺子读版本——造假字节只证到形态，这一格才证到真品。
        "import pathlib, pickle\n"
        "from rdagent.components.coder.CoSTEER.knowledge_management "
        "import CoSTEERKnowledgeBaseV2\n"
        "pathlib.Path('real_kb_v2.pkl').write_bytes("
        "pickle.dumps(CoSTEERKnowledgeBaseV2(init_component_list=[])))\n"
    )
    env_py_args = [env_py, "-c", code]
    child_env = {**o._driver_env(), "RDAGENT_COSTEER_KB_PATH": str(kb)}
    child_env[_KB_READ_KEY] = str(kb)
    child_env[_KB_WRITE_KEY] = str(kb)
    r = subprocess.run(env_py_args, env=child_env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    with_line = [ln for ln in r.stdout.splitlines() if ln.startswith("WITH=")][0]
    without_line = [ln for ln in r.stdout.splitlines()
                    if ln.startswith("WITHOUT=")][0]
    assert with_line == f"WITH={kb}|{kb}", with_line
    assert without_line == "WITHOUT=None|None", without_line
    real = tmp_path / "real_kb_v2.pkl"
    assert real.exists() and real.stat().st_size > 0
    assert o._kb_pickle_class(real) == "CoSTEERKnowledgeBaseV2", (
        "rdagent 真品字节被自己的尺子认不出⇒生产里每场都会悄悄降成只写不读")


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


# ---------------- 回收读数只把「净增」当产出 ----------------

def _stub_launch(monkeypatch, popen):
    monkeypatch.setattr(o, "rdagent_preflight",
                        lambda d=None: [("all", True, "ok")])
    monkeypatch.setattr(o, "_find_conda", lambda: "/bin/true")
    monkeypatch.setattr(o, "_docker_access", lambda: (True, False, "ok"))
    monkeypatch.setattr(o.subprocess, "Popen", popen)


class _FakeProcWrites:
    """退出码 0、且真往 factors.json 里追加了一条的驱动

    追加动作放在 stdout 生成器里＝一定发生在基线抄好之后，这样才测得出
    「净增」是拿起场前的名单做差，不是拿收尾时的文件长度当产出。
    """

    def __init__(self, factors_path, returncode=0, *a, **kw):
        self.path = factors_path
        self.returncode = returncode
        self.stdout = self._stream()

    def _stream(self):
        data = json.load(open(self.path, encoding="utf-8"))
        data.append({"name": "New_This_Run", "expr": "roc(df,5)",
                     "mean_ic": 0.011})
        json.dump(data, open(self.path, "w", encoding="utf-8"),
                  ensure_ascii=False)
        yield "factor loop done\n"

    def wait(self):
        return self.returncode


def test_stale_archive_is_not_reported_as_this_runs_output(
        monkeypatch, tmp_path, capsys):
    """负对照：本场一条没写，读数必须念「净增 0」并把旧档标出来

    10-03 与 10-04 两场都由旧措辞「官方产出 12 个因子」蒙过去一次，而 `cmp`
    对起场前快照逐字节相同＝本场 0 写入。返回值仍交回旧档（那是刻意行为，
    见 test_launch_failure_recovers_previous_round），改的只有读数口径。
    """
    out = tmp_path / "rd"
    out.mkdir()
    _factors_json(out / "factors.json")
    _stub_launch(monkeypatch, lambda *a, **kw: _FakeProc(returncode=1))
    got = o.try_official_rdagent(str(out))
    assert [f["name"] for f in got] == ["5_day_SMA", "NoExpr"]
    text = capsys.readouterr().out
    assert "本场净增 0 个因子" in text
    assert "既有产物累计 2 个因子" in text
    assert "本场 0 写入" in text


def test_net_new_counts_only_names_absent_before_launch(
        monkeypatch, tmp_path, capsys):
    """正对照：真新增一条时净增＝1，且累计仍是 3（两个数不能混成一个）"""
    out = tmp_path / "rd"
    out.mkdir()
    _factors_json(out / "factors.json")
    _stub_launch(monkeypatch,
                 lambda *a, **kw: _FakeProcWrites(out / "factors.json"))
    got = o.try_official_rdagent(str(out))
    assert len(got) == 3
    text = capsys.readouterr().out
    assert "本场净增 1 个因子" in text and "New_This_Run" in text
    assert "既有产物累计 3 个因子" in text


def test_baseline_empty_when_no_archive(monkeypatch, tmp_path):
    """没旧档时基线是空集而不是 None，净增才会等于全部而非「无法判定」"""
    out = tmp_path / "rd"
    out.mkdir()
    assert o._existing_factor_names(str(out)) == set()
    _factors_json(out / "factors.json")
    assert o._existing_factor_names(str(out)) == {"5_day_SMA", "NoExpr"}
    (out / "factors.json").write_text("{ 不是合法 JSON", encoding="utf-8")
    assert o._existing_factor_names(str(out)) == set()


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
