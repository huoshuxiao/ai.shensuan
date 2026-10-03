# -*- coding: utf-8 -*-
"""各候选模型的「机器占用」账单：同一份提示词，边跑边采 CPU/内存/交换/磁盘读。

为什么要单独一支探针：09-28 前两轮网格（`probe_llm_4b_think_0928.py`、
`probe_llm_model_grid_0928.py`）只记了墙钟和出词，没记资源。而本机是 **GTX 965M 2GB、
显存实测只用了 6MiB ⇒ 推理全在 CPU 上**，15Gi 内存 + swap 已经用满 2.0Gi。
这种机器上"换个模型"的真实代价不是秒数，是**驻留要占多少物理内存、把系统挤不挤进 swap**
——挤进 swap 就会拖慢同一台机器上的日更链（16G 上限是硬约束）。

每 2 秒一拍、跑一次冷调用，采这些量（公式写在名字后）：
  进程CPU%   = Δ(utime+stime)/100 / Δt × 100   （100 = 一个核满载；本机 8 核）
  系统CPU%   = 1 − Δidle/Δtotal（/proc/stat，含别人的进程）
  RSS_MB     = llama-server/ollama 各进程 VmRSS 之和（驻留的物理内存，取峰值+均值）
  SwapOut_MB = 同批进程 VmSwap 之和（模型被换出到 swap = 已经在挤）
  搬进内存   = Δ次缺页 × 4KB（模型 mmap 的页从页缓存搬进来多少）；真读盘 = Δ主缺页 × 4KB
  可用MB低值 = MemAvailable 全程最低（挤给别的日更步骤还剩多少）
  load1峰    = /proc/loadavg 1 分钟值（>8 = 排核）
  驻留GB     = ollama /api/ps 的 size；显存GB = size_vram（本机 965M 不参与，逐格坐实）
  子进程数分布 = 这一格里 llama-server 子进程数的集合；**只有 {0,1} 才是单模型**。

v1 跑完之后抓出的两个表病（都在这版修了，读数以 v2 为准）：
  ① "冷启"是假的：上一版只等「下一个要用的模型」不在场，而 ollama 默认 keep_alive=5min
     ⇒ 上一个模型还驻留，M2/M3/M4 三格实测全程有 2 个 llama-server 并存，
     那三格的 RSS 峰（8.7/11.8/11.0GB）与"可用只剩 204MB"是**两个模型之和**，不可引用。
     现在每格起手先把所有驻留卸掉，等到 `/api/ps` 真空 + 一个子进程都不剩才开跑。
  ② "盘读"是死表：`/proc/<pid>/io` 的 read_bytes 四格全 0.0——mmap 按页缺页搬运，不走那本账。
     改用 Δ次缺页/Δ主缺页（4KB/页）反推搬进内存与真读盘的字节数。

四格（顺序=便宜的在前；每格之前都先卸载并确认 `/api/ps` 里没了 ⇒ 全是冷启）：
  M1 qwen3.5:4b         关思考 · 生产 /v1 口
  M2 qwen2.5-coder:7b   关思考 · 生产 /v1 口
  M3 qwen3.5:9b         关思考 · 生产 /v1 口（生产现况 = 对照基准）
  M4 qwen3.5:4b         开思考 · ollama 原生口（只有它认 `num_ctx`，/v1 会悄悄忽略）
     ⇒ 请求体直接从 `probe_llm_think_native_0928.py` import 那几个常量，
        与上一跑那次 660.5s/3351 词逐字同体；本轮只补"资源"那一列，不重问契约。

M1/M2/M3 的用户提示词也与前两轮逐字同体（`场次 …请挑 5 只…`）⇒ 秒数可与
`smoke.csv`/`coder_grid.log` 并排读，不另起口径。

尺子先验再花钱：import 上一跑的 5 个夹具，全绿才调模型。
只读生产归档（`daily_signal/buy_*.csv`），不装面板，不写 `stock/v1/data/` 任何文件。
不并发：起跑前若 `/api/ps` 非空、或有别的 `probe_llm`/`rdagent` 进程在 ⇒ 直接退出。
`make_openai_client(max_retries=0)`：**必须关重试**，否则超时被 SDK 重试乘进墙钟。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_machine_cost_0928.py --selftest
       ⇒ 只验这台"表"本身（6s 静默 + 8s 人造满载，不调模型）：负对照=静默期既不报
          假满载也不恒零、且找得到被采的进程；正对照=造满载后系统CPU% 必须抬起来。
    /usr/bin/python3.10 stock/v1/temp/probe_llm_machine_cost_0928.py
    /usr/bin/python3.10 stock/v1/temp/probe_llm_machine_cost_0928.py --rows M1,M3
产物：stock/v1/temp/tmp_llm_machine_0928/{machine_cost.csv, raw.jsonl, samples.jsonl}
"""
import csv
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "src"))
# 三道闸门故意失效（load_dotenv 是 override=False ⇒ 进程环境变量优先），请求体自己写。
os.environ["STOCK_LLM_MAX_TOKENS"] = "0"
os.environ["STOCK_LLM_REASONING_EFFORT"] = ""

import _bootstrap  # noqa: F402,E402  先挂 sys.path，否则 config 解析到无名命名空间包
import requests  # noqa: E402
from config import LLM_BASE_URL, LLM_MODEL  # noqa: E402
from core.llm_client import make_openai_client  # noqa: E402

sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "temp"))
from llm_evidence_common import SYSTEM_ORDER  # noqa: E402
from probe_llm_4b_think_0928 import load_pool, pool_text  # noqa: E402
from probe_llm_picks_0927 import build_fixtures, check_compliance  # noqa: E402
import probe_llm_think_native_0928 as NAT  # noqa: E402  开思考那格的同体请求常量

OUT_ROOT = os.path.join(_ROOT, "stock", "v1", "temp", "tmp_llm_machine_0928")
OLLAMA = "http://localhost:11434"
CALL_TIMEOUT = 1800.0
SAMPLE_SEC = 2.0
N_CPU = os.cpu_count() or 8
TICK = 100.0  # /proc/<pid>/stat 的单位是 USER_HZ=100 tick/秒

ROWS = [
    ("M1 4b·关思考·/v1", "qwen3.5:4b", dict(route="v1")),
    ("M2 coder7b·关思考·/v1", "qwen2.5-coder:7b", dict(route="v1")),
    ("M3 9b·关思考·/v1", "qwen3.5:9b", dict(route="v1")),
    ("M4 4b·开思考·原生口16384", NAT.MODEL, dict(route="native")),
]

# 报表列：(采样列名, 统计法, 输出列名)
PEAKS = [("RSS_MB", "max", "RSS峰MB"), ("RSS_MB", "mean", "RSS均MB"),
         ("SwapOut_MB", "max", "换出swap峰MB"), ("进程CPU%", "max", "进程CPU%峰"),
         ("系统CPU%", "mean", "系统CPU%均"), ("可用MB", "min", "可用MB最低"),
         ("load1", "max", "load1峰"), ("线程", "max", "线程峰"),
         ("磁盘读MB", "max", "盘读末MB")]


# ---------- 进程发现与读计数器（全部只读 /proc） ----------

def llama_pids():
    """本机所有 llama-server / ollama 守护进程的 pid。"""
    out = []
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(path, "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "llama-server" in cmd or re.search(r"(^|/)ollama( |$)", cmd + " "):
            out.append(path.split("/")[2])
    return out


def read_status_kb(pid, key):
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith(key):
                    return int(line.split()[1])  # kB
    except (OSError, ValueError):
        pass
    return 0


def _stat_fields(pid):
    """返回 state 之后的字段列表。comm 可能含空格和括号 ⇒ 从最后一个 ')' 之后切。
    该列表第 12/13 个（0 起）= utime/stime（整机字段序 14/15）。"""
    with open(f"/proc/{pid}/stat") as fh:
        raw = fh.read()
    return raw[raw.rindex(")") + 2:].split(), raw


def read_cpu_ticks(pid):
    try:
        f, _ = _stat_fields(pid)
        return float(f[11]) + float(f[12])
    except (OSError, ValueError, IndexError):
        return 0.0


def read_read_bytes(pid):
    try:
        with open(f"/proc/{pid}/io") as fh:
            for line in fh:
                if line.startswith("read_bytes"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


def read_faults(pid):
    """返回 (次缺页, 主缺页)。
    ⚠️ 上一版用 /proc/<pid>/io 的 read_bytes 量"冷加载读了多少盘"，四格全是 0.0 = 死表：
    llama.cpp 把模型文件 mmap 进来，按页缺页搬运，不走 writeback 读，所以 read_bytes 不记。
    改用缺页计数反推：次缺页=页缓存里已有（Δ×4KB），主缺页=真去盘上取（Δ×4KB）。
    字段序（state 之后 0 起）：minflt=f[7]，majflt=f[9]。"""
    try:
        f, _ = _stat_fields(pid)
        return float(f[7]), float(f[9])
    except (OSError, ValueError, IndexError):
        return 0.0, 0.0


def llama_server_cmds():
    """当前 llama-server 子进程的完整命令行（里面就有 -c 窗口 与 -t 线程数）。"""
    out = []
    for pid in llama_pids():
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        if "llama-server" in cmd:
            out.append(cmd)
    return out


def meminfo():
    d = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            d[k] = int(v.split()[0])  # kB
    return d


def sys_cpu():
    """返回 (busy, total) tick；idle 含 iowait。"""
    with open("/proc/stat") as fh:
        cols = [float(x) for x in fh.readline().split()[1:]]
    total = sum(cols)
    idle = cols[3] + (cols[4] if len(cols) > 4 else 0.0)
    return total - idle, total


def loadavg1():
    with open("/proc/loadavg") as fh:
        return float(fh.readline().split()[0])


def busy_procs(n=3):
    """整机累计 CPU 最长的 n 个进程：看 LLM 有没有把日更链的核抢光。"""
    rows = []
    for path in glob.glob("/proc/[0-9]*/stat"):
        try:
            f, raw = _stat_fields(path.split("/")[2])
            comm = raw[raw.index("(") + 1:raw.rindex(")")]
            rows.append((float(f[11]) + float(f[12]), comm))
        except (OSError, ValueError, IndexError):
            continue
    rows.sort(reverse=True)
    return [{"进程": c, "累计CPU秒": round(t / TICK, 1)} for t, c in rows[:n]]


# ---------- 采样线程 ----------

class Sampler(threading.Thread):
    def __init__(self, stop):
        super().__init__(daemon=True)
        self.stop_event = stop
        self.samples = []

    def run(self):
        prev = {}
        prev_busy, prev_total = sys_cpu()
        while not self.stop_event.is_set():
            time.sleep(SAMPLE_SEC)
            pids = llama_pids()
            now = time.time()
            rss = swap = thr = 0
            read_bytes = 0
            minf = majf = 0.0
            proc_pct = 0.0
            for pid in pids:
                rss += read_status_kb(pid, "VmRSS:")
                swap += read_status_kb(pid, "VmSwap:")
                thr += read_status_kb(pid, "Threads:")
                read_bytes += read_read_bytes(pid)
                a, b = read_faults(pid)
                minf += a
                majf += b
                ticks = read_cpu_ticks(pid)
                old = prev.get(pid)
                if old is not None and now - old[0] > 0:
                    proc_pct += (ticks - old[1]) / TICK / (now - old[0]) * 100.0
                prev[pid] = (now, ticks)
            for pid in [p for p in prev if p not in pids]:
                prev.pop(pid)
            busy, total = sys_cpu()
            dt_total = total - prev_total
            sys_pct = 100.0 * (busy - prev_busy) / dt_total if dt_total > 0 else 0.0
            prev_busy, prev_total = busy, total
            mi = meminfo()
            self.samples.append({
                "t": round(now, 1), "llama进程数": len(pids),
                "llama子进程数": len(llama_server_cmds()), "线程": thr,
                "进程CPU%": round(proc_pct, 1), "系统CPU%": round(sys_pct, 1),
                "RSS_MB": round(rss / 1024.0, 1),
                "SwapOut_MB": round(swap / 1024.0, 1),
                "可用MB": round(mi["MemAvailable"] / 1024.0, 1),
                "页缓存MB": round(mi["Cached"] / 1024.0, 1),
                "交换已用MB": round((mi["SwapTotal"] - mi["SwapFree"]) / 1024.0, 1),
                "磁盘读MB": round(read_bytes / 1048576.0, 1),
                "次缺页": minf, "主缺页": majf,
                "load1": loadavg1()})


def stat(samples, key, how):
    vals = [s[key] for s in samples if isinstance(s.get(key), (int, float))]
    if not vals:
        return None
    if how == "max":
        return round(max(vals), 1)
    if how == "min":
        return round(min(vals), 1)
    return round(sum(vals) / len(vals), 1)


# ---------- 卸载 / 两种调用 ----------

def ps_models():
    try:
        return requests.get(f"{OLLAMA}/api/ps", timeout=20).json().get("models", [])
    except Exception:
        return []


def quiet_machine(max_wait_s=420.0):
    """把**所有**还驻留的模型 keep_alive:0 卸掉，等到 `/api/ps` 真空 且 一个 llama-server
    子进程都不剩，才算这一格是冷启。
    ⚠️ 上一版只等"下一个要用的模型"不在场 ⇒ 上一个模型还赖在内存里（ollama 默认
    keep_alive=5min），实测 M2/M3/M4 三格采样期间有 2 个 llama-server 并存，
    RSS 峰 11.7GB 与"可用只剩 204MB"是**两个模型之和**，不是单模型的账。
    返回 (是否真空, 开始时的驻留列表, 等了多久)。"""
    residents0 = [m["model"] for m in ps_models()]
    for m in residents0:
        try:
            requests.post(f"{OLLAMA}/api/chat",
                          json={"model": m, "messages": [], "keep_alive": 0},
                          timeout=60)
        except Exception as e:
            print(f"  ⚠️ 卸载 {m} 报错：{type(e).__name__}: {str(e)[:120]}", flush=True)
    waited = 0.0
    while waited < max_wait_s:
        time.sleep(2.0)
        waited += 2.0
        if not ps_models() and not llama_server_cmds():
            return True, residents0, waited
    return False, residents0, waited


def call_v1(cl, model, user_prompt):
    """生产口径：兼容口 /v1，effort=none + max_tokens=1024 + JSON 约束 + temperature=0。"""
    t0 = time.time()
    try:
        resp = cl.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM_ORDER},
                      {"role": "user", "content": user_prompt}],
            temperature=0.0, reasoning_effort="none", max_tokens=1024,
            response_format={"type": "json_object"})
    except Exception as e:
        return {"秒": round(time.time() - t0, 1),
                "错误": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"秒": round(time.time() - t0, 1),
            "finish": resp.choices[0].finish_reason,
            "出词": getattr(resp.usage, "completion_tokens", None),
            "入词": getattr(resp.usage, "prompt_tokens", None),
            "正文": resp.choices[0].message.content or ""}


def call_native(model, sys_prompt, user_prompt, num_ctx, num_predict):
    """原生口 /api/chat：答案在 message.content / message.thinking，顶层只有
    done_reason 与 eval_count——读顶层会得到假的 0（上一轮栽在这儿）。"""
    t0 = time.time()
    try:
        r = requests.post(f"{OLLAMA}/api/chat", timeout=CALL_TIMEOUT, json={
            "model": model, "stream": False, "think": True,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": user_prompt}],
            "options": {"num_ctx": num_ctx, "num_predict": num_predict,
                        "temperature": 0}})
        r.raise_for_status()
        j = r.json()
    except Exception as e:
        return {"秒": round(time.time() - t0, 1),
                "错误": f"{type(e).__name__}: {str(e)[:200]}"}
    m = j.get("message") or {}
    return {"秒": round(time.time() - t0, 1), "finish": j.get("done_reason"),
            "出词": j.get("eval_count"), "入词": j.get("prompt_eval_count"),
            "正文": m.get("content") or "", "思考": m.get("thinking") or ""}


def selftest():
    """尺子的负对照+正对照（不调模型，6 秒）：
      负：静默期系统CPU% 必须 <90（若恒 100 = 公式错）；
      正：另开 核数 个满载**子进程**，系统CPU% 必须明显抬起来（若不动 = 采不到东西）。
    """
    stop = threading.Event()
    smp = Sampler(stop)
    smp.start()
    time.sleep(6.0)
    stop.set()
    smp.join(timeout=20)
    q = [s["系统CPU%"] for s in smp.samples]
    # 负对照两条都要验：满载是假的（不许恒 100），零也是假的（不许恒 0 = 没在采）
    neg_ok = bool(q) and max(q) < 95 and min(q) > 0.0
    print(f"[尺子自检] 静默 6s 采到 {len(q)} 拍；系统CPU% 读数={q}")
    print(f"[尺子自检] 负对照 静默期既无假满载(<95)也非恒零: "
          f"{'✅' if neg_ok else '❌ 公式错：' + str(q)}")
    print(f"[尺子自检] 采样行示例：{json.dumps(smp.samples[-1], ensure_ascii=False)}")

    npid = [s["llama进程数"] for s in smp.samples]
    print(f"[尺子自检] 负对照 找得到被采的人（llama/ollama 进程数={set(npid)}）: "
          f"{'✅' if max(npid or [0]) > 0 else '❌ 找不到进程 ⇒ RSS/CPU/盘读三列会假零'}")
    if max(npid or [0]) <= 0:
        neg_ok = False

    n_burn = N_CPU
    stop2 = threading.Event()
    smp2 = Sampler(stop2)
    smp2.start()
    # 正对照必须用**子进程**造负载：纯 Python 线程被 GIL 卡成单核，不但抬不起系统CPU%，
    # 还会把采样线程饿死（上一版 32 线程实测采到 0 拍 = 假红）。
    burn = [subprocess.Popen(["sha256sum", "/dev/zero"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(n_burn)]
    time.sleep(9.0)
    for b in burn:
        b.terminate()
    stop2.set()
    smp2.join(timeout=20)
    hi = [s["系统CPU%"] for s in smp2.samples]
    peak = max(hi) if hi else 0
    for b in burn:
        b.wait(timeout=10)
    print(f"[尺子自检] 正对照 {n_burn} 个满载子进程 ⇒ 采到 {len(hi)} 拍，"
          f"系统CPU%={hi}（峰值 {peak}）: "
          f"{'✅' if peak >= 60 else '❌ 采不到负载，这台表是坏的'}")
    return 0 if (neg_ok and peak >= 60) else 1


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    only = None
    if "--rows" in sys.argv:
        only = set(sys.argv[sys.argv.index("--rows") + 1].split(","))
    if "--selftest" in sys.argv:
        return selftest()

    resident = [m["model"] for m in ps_models()]
    # 只认「别人」：排除自己这个 pid，也排除把这条命令行原样抄了一遍的包装 shell
    #（上一版被自己的 bash 包装误判成"有别的探针在跑"，一格都没跑）。
    self_base = os.path.basename(os.path.abspath(__file__))
    others = []
    for ln in subprocess.run(["pgrep", "-af", "probe_llm|rdagent"],
                             capture_output=True, text=True).stdout.splitlines():
        pid, _, args = ln.partition(" ")
        if pid == str(os.getpid()) or self_base in args:
            continue
        others.append(ln)
    if resident or others:
        print(f"❌ 「不并发跑本地 LLM」这条被占了：驻留={resident} 别的探针={others}"
              "；退出，一格都没跑")
        return 2

    tag_b, df, det = load_pool()
    rows = df.set_index("code")
    user_prompt = (f"场次 {tag_b}（收盘价截面，T+1 开盘买）。观察名单：\n"
                  f"{pool_text(df)}\n\n请挑 5 只，按你想要的优先级从高到低排列。")

    fixtures = build_fixtures(df, rows, det)
    bad = []
    for name, codes, want in fixtures:
        if codes is None:
            print(f"[尺子自检] {name} ❌ 夹具造不出来 ⇒ 不调模型", flush=True)
            return 1
        got = [list(x) for x in check_compliance(codes, rows)]
        ok = got == [list(w) for w in want]
        print(f"[尺子自检] {name}: {'✅' if ok else '❌ 读数=' + str(got)}", flush=True)
        bad += [] if ok else [name]
    if bad:
        print(f"❌ 尺子脏（{len(bad)}/{len(fixtures)}）⇒ 不调模型", flush=True)
        return 1

    mi0 = meminfo()
    print(f"\n并发闸=通过（驻留={resident}，别的探针={others}）", flush=True)
    print(f"机器：{N_CPU} 核｜总内存 {mi0['MemTotal']/1048576.0:.1f}GB｜"
          f"可用 {mi0['MemAvailable']/1048576.0:.1f}GB｜"
          f"交换已用 {(mi0['SwapTotal']-mi0['SwapFree'])/1048576.0:.1f}/"
          f"{mi0['SwapTotal']/1048576.0:.1f}GB｜负载 {loadavg1()}｜"
          f".env 生产模型={LLM_MODEL}（本探针显式覆盖，.env 一字未改）", flush=True)

    cl = make_openai_client(max_retries=0, timeout=CALL_TIMEOUT, api_key="ollama")
    results, all_samples = [], []
    for tag, model, spec in ROWS:
        if only and tag.split()[0] not in only:
            continue
        print(f"\n===== {tag}｜{model} =====", flush=True)
        quiet, residents0, waited = quiet_machine()
        print(f"  等机器真空：{'✅' if quiet else '❌ 超时，这格的 RSS/秒数会被上一个模型污染'}"
              f"（等 {waited:.0f}s｜起手驻留={residents0}｜现在 llama-server 子进程="
              f"{len(llama_server_cmds())}）", flush=True)

        stop = threading.Event()
        smp = Sampler(stop)
        smp.start()
        time.sleep(SAMPLE_SEC)
        if spec["route"] == "v1":
            got = call_v1(cl, model, user_prompt)
        else:
            got = call_native(model, NAT.SYS,
                              f"场次 {tag_b}。观察名单：\n{NAT.POOL}\n\n请挑 2 只。",
                              NAT.NUM_CTX, NAT.N_PREDICT)
        stop.set()
        smp.join(timeout=30)
        ss = smp.samples

        ps_now = [m for m in ps_models() if m["model"].startswith(model)]
        ps = ps_now[0] if ps_now else {}
        content = got.get("正文", "")
        rec = {"格": tag, "模型": model, "口": spec["route"],
               "秒": got.get("秒"), "finish": got.get("finish"),
               "出词": got.get("出词"), "入词": got.get("入词"),
               "正文字符": len(content), "思考字符": len(got.get("思考", "")),
               "驻留GB": round(ps.get("size", 0) / 1e9, 2),
               "显存GB": round(ps.get("size_vram", 0) / 1e9, 2),
               "窗口": ps.get("context_length"), "错误": got.get("错误")}
        try:
            picks = json.loads(content).get("picks", [])
            codes = [str(p.get("code", "")).upper() for p in picks]
            outside, bo, idup = check_compliance(codes, rows)
            rec.update({"可解析": True, "条数": len(codes), "池外": outside,
                        "板块超限": bo, "行业重复": idup,
                        "与生产重合": len(set(codes) & set(det)), "picks": codes})
        except Exception as e:
            rec["可解析"] = f"否({type(e).__name__})"
        for col, how, out_col in PEAKS:
            rec[out_col] = stat(ss, col, how)
        rec["干净冷启"] = quiet
        rec["子进程数分布"] = sorted({s["llama子进程数"] for s in ss}) if ss else None
        rec["搬进内存MB(页缓存)"] = round((max([s["次缺页"] for s in ss], default=0)
                                       - min([s["次缺页"] for s in ss], default=0))
                                      * 4096 / 1048576.0, 1) if ss else None
        rec["真读盘MB(主缺页)"] = round((max([s["主缺页"] for s in ss], default=0)
                                      - min([s["主缺页"] for s in ss], default=0))
                                     * 4096 / 1048576.0, 1) if ss else None
        rec["旧read_bytes列MB"] = stat(ss, "磁盘读MB", "max")
        rec["占整机%"] = round((rec["进程CPU%峰"] or 0) / (100.0 * N_CPU), 1)
        rec["页缓存末MB"] = stat(ss, "页缓存MB", "max")
        rec["交换已用末MB"] = stat(ss, "交换已用MB", "max")
        rec["推理命令行"] = " ;; ".join(llama_server_cmds())[:400]
        rec["拍数"] = len(ss)
        results.append(rec)
        with open(os.path.join(OUT_ROOT, "raw.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(rec, 正文=content,
                                     思考=got.get("思考", "")), ensure_ascii=False) + "\n")
        print(json.dumps({k: v for k, v in rec.items()}, ensure_ascii=False), flush=True)
        print(f"  此刻整机累计 CPU 前三：{busy_procs(3)}", flush=True)
        all_samples += [dict(s, 格=tag) for s in ss]

    quiet_machine()  # 收尾：不把几百 MB~6GB 留在机器上过夜
    keys = [k for k in results[0]] if results else []
    with open(os.path.join(OUT_ROOT, "machine_cost.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(results)
    with open(os.path.join(OUT_ROOT, "samples.jsonl"), "w", encoding="utf-8") as fh:
        for s in all_samples:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"\n===== 机器占用账单（{len(results)} 格）=====")
    for r in results:
        print(f"{r['格']}: {r['秒']}s｜干净冷启={r['干净冷启']}（子进程数分布 "
              f"{r['子进程数分布']}）｜驻留 {r['驻留GB']}GB（显存 {r['显存GB']}GB）｜"
              f"RSS峰 {r['RSS峰MB']}MB 均 {r['RSS均MB']}MB｜"
              f"换出swap峰 {r['换出swap峰MB']}MB｜搬进内存 {r['搬进内存MB(页缓存)']}MB"
              f"（其中真读盘 {r['真读盘MB(主缺页)']}MB）｜"
              f"进程CPU峰 {r['进程CPU%峰']}% = {r['占整机%']}% 整机容量｜"
              f"系统CPU均 {r['系统CPU%均']}%｜可用最低 {r['可用MB最低']}MB｜"
              f"load1峰 {r['load1峰']}｜可解析={r['可解析']}", flush=True)
    print(f"\n产物：{OUT_ROOT}/machine_cost.csv、raw.jsonl、samples.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
