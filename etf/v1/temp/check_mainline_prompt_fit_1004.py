# -*- coding: utf-8 -*-
"""一次性读数（10-04 丁）：主线那 21 个 LLM 调用点自己的提示词到底有多大。

大背景：`/v1` 兼容口不认窗口参数（`check_num_ctx_v1_1004.py` 四臂已钉死），所以
`ETF_LLM_NUM_CTX=16384` 对主线是**空转**。空转要不要修，取决于一个还没量过的事实：
**主线自己发出去的那些提示词，有没有一条大到需要 16384**（官方支那条 5502 token 是量过的，
主线这 21 个调用点一条都没量过）。

两段：

**甲段＝零成本、不碰模型**（几秒）
 1. 每个调用点的**体积窗口**＝`.create` 那个壳 **＋** 同文件里把 messages 递给它的调用方。
    这一步不能省：这些模块的形状一律是 `_chat/_call/_call_one` 薄壳（壳里一个常数都没有），
    `[:4000]` 全写在调用方那一侧 ⇒ 只扫壳会把 14 个点假报成「无界」。窗口里正则抽出
    **体积闸**（`[:4000]` 这类，<1000 字符的是日期切片不算）、**条数闸**（`[-10:]` 这类：
    条数封顶、单条长度不封顶）、以及同函数 system 段的实测字符数 ⇒ 该点的**代码允许上界**。
    system 段是个局部变量时抽不出长度，那一格如实标成**下界**，不假装是上界。
 2. **入口可达性**＝从 `etf/v1/*.py`＋`etf/v1/src/*.py` 出发走 import 的传递闭包。一跳 grep 会漏
    （`llm_factor_agent` 在本线没人直接 import，它经 `multi_source_mining` 挂链）。可达＝图上有路，
    ≠ 本场真执行——发不发由配置闸那一列说，两列合起来才判得出「这条臂今天会不会真发一句出去」。
 3. 对**当前真会发出去**的那几路，用生产归档里的**真件**重建 user 文本 ⇒ 今天的**实际值**
    （哪一格是合成形状会逐行标注，不混进真件里）。
 4. 三把尺子的换算先不承诺：cl100k 在 10-03 那件真提示词上比 ollama 少约 8%，
    所以甲段所有 token 数都叫**估算**，进乙段现量的三条才算数。

**乙段＝付三次 prefill**（原生 `/api/chat` + `num_predict=1`，只进模型不解码）
 B0 **正对照**：10-03 逐字节缓存的那条真提示词，ollama 当晚报过 5502 ⇒ 这次必须落在
    ±5% 内，不落就判"尺子坏了"，乙段三条读数一律不作数。
 B1 甲段里**今天实际最大**的那一条真件。
 B2 甲段里**体积闸最大的可达调用点**，把闸按中文填满（编的中文，量的是"闸允许多大"不是"今天多大"）。
 B3 同 B2 那句话，但把窗口压回生产默认 4096 ⇒ 顺手量 10-03 那格「5502 为何只进 2050」
    的猜想（4096 是不是被默认输出预算占掉了 ~2048）。

判据（不恒真）：B2 < 3072（＝4096 − `ETF_LLM_MAX_TOKENS`）⇒ 主线连最坏情况都碰不到窗口，
空转不付代价；B2 ≥ 3072 ⇒ 有牙，"一把旋钮看着管两条腿、实际只管一条"这件事必须处理。
B1 与 B2 是两个层次，都得念：B1 说**今天**付没付代价，B2 说**代码允许不允许**付。

⚠️ 现场翻车过一次（10-04 首轮跑完）：3072 是「窗口减输出预算」这道**算式**，不是服务端的
实际行为。B3（同一句、4096 窗口、`num_predict=1`＝没留 2048 的输出位）与 10-04 凌晨那三臂
**两种长度的提示词都停在同一个 2050** ⇒ 真限是常量。所以判读要同时报**两条线**：
算式线 3072 与现量线（＝当场 B3 的读数），B1/B2 各自对两条线各念一次，不许只报算式那条。

⚠️ 只读：全程不写生产目录（产物只落 `etf/v1/temp/`），不起 LLM 之外的任何东西。
⚠️ 乙段要让 qwen3.5:9b 驻留（≈6.1GB，16384 再 +0.5GB），起前先过 `MemAvailable`
   与「有没有别人的作业在跑」。

用法：/usr/bin/python3.10 -u etf/v1/temp/check_mainline_prompt_fit_1004.py
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(REPO, "etf", "v1", "src")
TMP = os.path.join(REPO, "etf", "v1", "temp")
CONTROL_PROMPT = os.path.join(TMP, "probe_real_prompt_hypothesis_gen_1003.txt")
NATIVE_URL = "http://localhost:11434/api/chat"
V1_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "qwen3.5:9b"
SERVER_WINDOW = 4096          # /v1 实际给的窗口（10-04 四臂实测）
FAILS = []

for _p in (os.path.join(SRC, "config"), os.path.join(SRC, "core"),
           os.path.join(REPO, "common", "src", "core"),
           os.path.join(REPO, "common", "src", "report"),
           os.path.join(REPO, "common", "src", "feedback"),
           os.path.join(REPO, "common", "src", "optimizer"),
           os.path.join(REPO, "common", "src")):
    sys.path.insert(0, _p)


def say(m):
    print(m, flush=True)


def post(url, payload, timeout=1500):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def resident_ctx():
    out = subprocess.run(["curl", "-s", "--max-time", "8",
                          "http://localhost:11434/api/ps"],
                         capture_output=True, text=True).stdout
    for m in json.loads(out or "{}").get("models", []):
        if m["name"].startswith(MODEL):
            return m.get("context_length")
    return None


# ---------------------------------------------------------------- 甲段：静态上界
# 21 个调用点（10-04 现 grep 得到，行号= `.create(` 那一行）。
# gate：本线哪个配置闸决定它发不发出去（None＝无闸，恒活）；kind：活/关/手工/无调用者
SITES = [
    ("factor_agent", "common/src/core/llm_factor_agent.py", 51, None),
    ("shap_explainer", "common/src/core/llm_shap_explainer.py", 77, None),
    ("crossover_op", "common/src/core/llm_crossover_operator.py", 49, "GENETIC"),
    ("mutation_op", "common/src/core/llm_mutation_operator.py", 49, "GENETIC"),
    ("genetic_hybrid", "common/src/core/llm_genetic_hybrid.py", 60, "LLM_GENETIC_HYBRID"),
    ("ortho_optimizer", "common/src/core/orthogonal_optimizer.py", 39, "ORTHO_LLM"),
    ("hypothesis_roles", "common/src/core/hypothesis_roles.py", 160, "HYPOTHESIS_ROLES"),
    ("report_generator", "common/src/report/llm_report_generator.py", 40, None),
    ("multi_gen_daily", "common/src/report/multi_llm_generator.py", 36, None),
    ("multi_gen_eval", "common/src/report/multi_llm_generator.py", 157, None),
    ("report_merger", "common/src/report/report_merger.py", 34, "MERGE_CONFIG"),
    ("quarterly_review", "common/src/report/quarterly_review.py", 34, None),
    ("prompt_optimizer", "common/src/report/prompt_optimizer.py", 25, None),
    ("monthly_review", "common/src/report/monthly_review.py", 41, None),
    ("self_evaluator", "common/src/report/self_evaluator.py", 45, None),
    ("ab_test_prompt", "common/src/report/ab_test.py", 37, None),
    ("ab_test_score", "common/src/report/ab_test.py", 77, None),
    ("multi_voter", "common/src/report/multi_llm_voter.py", 45, None),
    ("blind_eval", "common/src/report/multi_llm_blind_eval.py", 33, None),
    ("joint_optimizer", "common/src/optimizer/joint_optimizer.py", 42, "JOINT_LLM"),
    ("research_planner", "common/src/optimizer/llm_research_planner.py", 31,
     "LLM_RESEARCH_PLANNER"),
]

SYS_RE = re.compile(r'^(\w+)\s*=\s*(?:"""|\'\'\')(.*?)\2', re.S | re.M)
CAP_BIG = 1000          # 小于这个数的切片是日期/短语（`[:10]` 取年月），不是提示词体积闸


def def_blocks(lines):
    """把源码切成 def 块：块尾＝下一个同级或更外层 def。返回 [(indent, name, start, text)]"""
    marks = []
    for i, ln in enumerate(lines):
        m = re.match(r"(\s*)def (\w+)\(", ln)
        if m:
            marks.append((len(m.group(1)), m.group(2), i))
    out = []
    for indent, name, start in marks:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].strip().startswith("def ") and \
                    (len(lines[j]) - len(lines[j].lstrip())) <= indent:
                end = j
                break
        out.append((indent, name, start, "\n".join(lines[start:end])))
    return out


def owner_window(lines, idx):
    """调用点的「体积窗口」＝它自己那个 def ＋同文件里把 messages 递给它的调用方。

    这些模块的形状都一样：`.create` 包在 `_chat/_call/_call_one` 这类薄壳里，壳里
    一个截断常数都没有；`[:4000]` 全写在调用方那一侧。只扫壳 ⇒ 18 个点会假报「无界」。
    """
    blocks = def_blocks(lines)
    mine = None
    for indent, name, start, text in blocks:
        if start <= idx:
            mine = (indent, name, start, text)
    if mine is None:
        return [], ""
    indent, name, _, text = mine
    # 调用方要认得带点的受体：factor_agent 的形状是 `self.llm.chat([...])`，
    # 只写 `self\.chat\(` 会漏掉整条 `_generate`（体积闸就藏在那一侧）
    callers = [b for b in blocks
               if b[2] != mine[2] and re.search(r"(?<!\w)[\w.]*\.?%s\(" % name, b[3])]
    return callers, text


def _import_graph_index():
    """扁平导入名 → 文件路径（本线是 sys.path 平铺 import，不能按包路径解析）"""
    idx = {}
    for root in (os.path.join(REPO, "etf", "v1", "src"),
                 os.path.join(REPO, "common", "src")):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("temp", "tests", "__pycache__")]
            for fn in filenames:
                if fn.endswith(".py"):
                    idx.setdefault(fn[:-3], os.path.join(dirpath, fn))
    return idx


GRAPH_CACHE = {}


def build_reach():
    """从本线入口（etf/v1/*.py + etf/v1/src/*.py）出发走 import 的传递闭包。

    一跳 grep 会漏：`llm_factor_agent` 不在本线里被直接 import，它是经
    `multi_source_mining` 挂上链的（那一路的 llm 源）。所以这里必须走图。
    注意：图上有路 ≠ 本场真执行——闸那一列才管这件事。
    """
    if GRAPH_CACHE:
        return GRAPH_CACHE
    idx = _import_graph_index()
    roots = glob.glob(os.path.join(REPO, "etf", "v1", "*.py")) + \
        glob.glob(os.path.join(REPO, "etf", "v1", "src", "*.py"))
    seen, frontier = {}, []
    for r in roots:
        stem = os.path.splitext(os.path.basename(r))[0]
        seen.setdefault(stem, "入口")
        frontier.append(r)
    IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w\.]*)", re.M)
    while frontier:
        cur = frontier.pop()
        try:
            src = open(cur, encoding="utf-8").read()
        except Exception:
            continue
        for name in IMPORT_RE.findall(src):
            stem = name.split(".")[0]
            path = idx.get(stem)
            if path is None or stem in seen:
                continue
            seen[stem] = "%s:%s" % (os.path.splitext(os.path.basename(cur))[0], stem)
            frontier.append(path)
    GRAPH_CACHE.update(seen)
    return seen


def reachable(module_path):
    """返回 (本线入口可达?, 经哪一跳)——不可达＝这个调用点在 ETF 链上压根没有入口"""
    stem = os.path.splitext(os.path.basename(module_path))[0]
    seen = build_reach()
    return (stem in seen, seen.get(stem, ""))


def static_rows():
    rows = []
    for label, rel, line_no, gate in SITES:
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            FAILS.append("缺文件 " + rel)
            continue
        src = open(path, encoding="utf-8").read()
        lines = src.splitlines()
        if line_no > len(lines) or "completions.create" not in lines[line_no - 1]:
            say("🛑 %s:%d 已不是 `.create` 那一行 ⇒ 点位表过期，本行读数作废"
                % (rel, line_no))
            FAILS.append("点位漂移 " + label)
            continue
        callers, own = owner_window(lines, line_no - 1)
        window = own + "\n" + "\n".join(b[3] for b in callers)
        allcaps = sorted(set(int(n) for n in re.findall(r"\[:(\d+)\]", window)))
        big = [c for c in allcaps if c >= CAP_BIG]
        counts = sorted(set(int(n) for n in re.findall(r"\[-(\d+):\]", window)))
        # system 段：调用点写的是标识符（`SYSTEM_PROMPT`）或被包了一层的取用
        # （`fill(SYSTEM_PROMPT, …)`），两种都要认，只认前者会把 system 体积整个漏掉
        sysname = sorted(set(re.findall(r'"role":\s*"system",\s*"content":\s*(?:\w+\(\s*)?(\w+)',
                                        window)))
        syslens = {}
        if sysname:
            consts = dict(SYS_RE.findall(src))
            syslens = {n: len(consts[n]) for n in sysname if n in consts}
        inline = re.findall(r'"role":\s*"system",\s*"content":\s*"""(.*?)"""', window, re.S)
        for k, t in enumerate(inline):
            syslens["inline%d" % k] = len(t)
        # 无体积闸时，如实把「长度由谁说话」那行原文抓出来，不许留成"无界"两个字
        var = re.findall(r'"role":\s*"user",\s*"content":\s*(\w+)', window)
        builder = ""
        if var:
            m = re.search(r"^\s*%s\s*=.*?(?=\n\s*\S)" % var[-1], window, re.S | re.M)
            builder = " ".join((m.group(0) if m else "").split())[:90]
        n_ok, who = reachable(path)
        unresolved = [n for n in sysname if n not in syslens]
        rows.append({"label": label, "rel": rel, "line": line_no, "gate": gate,
                     "caps": big, "caps_all": allcaps, "counts": counts,
                     "sys": syslens, "builder": builder, "unresolved": unresolved,
                     "callers": [b[1] for b in callers],
                     "reach": n_ok, "reach_by": who,
                     "cap_chars": sum(big) + sum(syslens.values()),
                     "unbounded": not big})
    return rows


# ---------------------------------------------------------------- 甲段：真件重建
def live_texts():
    """当前真会发出去的那几路，用生产归档里的真件把 user 文本重建出来（只读）"""
    out = {}
    d = os.path.join(REPO, "etf", "v1", "data", "live")

    fb = os.path.join(d, "feedback_report.json")
    if os.path.isfile(fb):
        raw = json.load(open(fb, encoding="utf-8"))
        # 与生产那一行同形（`indent=2` 会把体积撑大，省了就是假下界）
        out["report_generator"] = json.dumps(raw, ensure_ascii=False, indent=2)[:3000]

    rd = sorted(glob.glob(os.path.join(d, "report_daily_*.json")))
    if rd:
        blob = json.load(open(rd[-1], encoding="utf-8"))
        text = ""
        for k in ("report", "text", "content"):
            if isinstance(blob.get(k), str):
                text = blob[k]
                break
        out["self_evaluator"] = ("# 日报\n%s\n\n# 原始数据\n```json\n%s\n```"
                                 % (text[:4000],
                                    json.dumps(blob, ensure_ascii=False)[:2500]))
        out["_daily_report_len"] = len(text)

    try:
        from llm_shap_explainer import _build_prompt
        import pandas as pd
        fac = {"name": "vol_breakout_momentum", "expr": "std($volume, 20) / $close",
               "mean_ic": 0.0312, "icir": 0.44}
        res = {"top_lags": pd.DataFrame(
                    {"feature": ["lag_1_std", "lag_2_std", "lag_3_std",
                                 "lag_4_std", "lag_5_std"],
                     "importance": [0.31, 0.22, 0.15, 0.11, 0.08]}),
               "root_cause": "量能放大后 2~3 日的动量延续",
               "ic_series": pd.Series([0.03, 0.02, 0.04] * 8)}
        out["shap_explainer"] = _build_prompt(fac, res)
    except Exception as e:
        say("  shap 重建失败（不影响上界那一格）：%s: %s" % (type(e).__name__, e))

    # factor_agent 的 history 是运行期内存件（每轮追加评估过的因子），
    # 这里用**真库 66 条的最后 10 条**当尺寸替身——expr 长度、ic/icir 都是真件
    libf = os.path.join(REPO, "etf", "v1", "data", "library",
                        "factor_library_index.json")
    if os.path.isfile(libf):
        lib = json.load(open(libf, encoding="utf-8"))
        vals = list(lib.values()) if isinstance(lib, dict) else lib
        slim = [{"name": h.get("name", ""), "expr": h.get("expr", ""),
                 "mean_ic": h.get("ic", 0), "icir": h.get("icir", 0)}
                for h in vals[-10:]]
        out["factor_agent"] = ("历史因子IC：%s\n请生成 3 条新的因子表达式。"
                               % json.dumps(slim, ensure_ascii=False))
        out["_library_n"] = len(vals)
    else:
        say("  ⚠️ 因子库索引不在（%s）⇒ factor_agent 那一格没有现值读数" % libf)
    out["_note"] = {
        "report_generator": "真件（feedback_report.json 全文过 `[:3000]`）",
        "self_evaluator": "真件（最新一份 report_daily_*.json 的正文＋原始数据）",
        "shap_explainer": "**合成形状**（top_lags 那 5 行是编的，只给量级不给现值）",
        "factor_agent": "真件（库索引末 10 条的 expr/ic，条数与生产 `history[-10:]` 同）",
    }
    return out


def template_texts():
    """甲段 3：`multi_gen_daily`/`ab_test_prompt` 那两格的 user 段＝模板函数，直接对真件调用。

    这两条臂在源码里**没有体积闸**（`build_daily_prompt` 把 slippage/strategy 两段整块拼进去），
    所以甲段 1 给不出上界。但它们是可算的：生产读的就是 `data/live/feedback_report*.json`。
    今天的读数很小 ⇒ 必须同时念出**为什么**小：那些子字典是空的（影子盘零成交）。
    """
    d = os.path.join(REPO, "etf", "v1", "data", "live")
    try:
        from report_templates import ReportTemplate
    except Exception as e:
        say("  ⚠️ 模板模块 import 失败（%s）⇒ 这一格没有读数" % e)
        return {}
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "feedback_report*.json"))):
        rep = json.load(open(f, encoding="utf-8"))
        txt = ReportTemplate.build_daily_prompt(rep)
        empty = [k for k, v in rep.items() if isinstance(v, dict) and not v]
        out[os.path.basename(f)] = (txt, empty)
    return out


def worst_case(rows):
    """把体积闸按中文填满＝该调用点**代码允许发出去**的最大一句话。

    填充用的是编的中文（不是真日报），因为这里要量的是「闸允许多大」而不是
    「今天多大」——后者由甲段 2 的真件重建负责。只在**入口可达**的点里挑最大：
    不可达那一格纸面值再大也付不出账单。
    """
    pool = ("今日组合相对基准超额为正，换手维持在中位；量能族对回撤的贡献"
            "仍集中在两个窗口内，价格族未通过样本外检验，维持剔除。" * 400)
    live = [r for r in rows if r["reach"] and r["cap_chars"] > 0]
    if not live:
        FAILS.append("没有可达且带体积闸的调用点 ⇒ B2 无句可填")
        return None, ""
    top = max(live, key=lambda r: r["cap_chars"])
    parts = [pool[:n] for n in top["caps"]]
    head = sum(top["sys"].values())
    if head:
        parts.insert(0, pool[:head])       # system 段也占窗口
    return top, "\n\n".join(parts)


def tks(text):
    """cl100k 估算（10-03 对同一件真提示词比 ollama 少约 8%，只当量级用）"""
    if text is None:
        return None
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception as e:
        say("  （tiktoken 不可用：%s）" % e)
        return None


# ---------------------------------------------------------------- 乙段：现量
def native_arm(label, text, num_ctx):
    t0 = time.time()
    try:
        d = post(NATIVE_URL, {"model": MODEL,
                              "messages": [{"role": "user", "content": text}],
                              "stream": False, "think": False,
                              "options": {"num_ctx": num_ctx, "num_predict": 1}})
    except Exception as e:
        say("  %-4s 异常 %s: %s" % (label, type(e).__name__, str(e)[:200]))
        return None
    secs = round(time.time() - t0, 1)
    n = d.get("prompt_eval_count")
    say("  %-4s 真进=%-6s 驻留窗口=%-6s 墙钟=%ss（这句 %s 字符）"
        % (label, n, resident_ctx(), secs, len(text)))
    return n


def main():
    say("== 起点 ==")
    for k in ("MemAvailable", "SwapFree"):
        say("  %s = %s" % (k, re.search(k + r":\s*(\d+)",
                                        open("/proc/meminfo").read()).group(1)) + " kB")
    say("  驻留窗口起点=%s" % resident_ctx())

    from config import (LLM_MAX_TOKENS, LLM_NUM_CTX, LLM_MODEL, LLM_BASE_URL)
    gates = {}
    for gname in ("GENETIC", "GENETIC_MULTI_OBJECTIVE", "LLM_GENETIC_HYBRID",
                  "ORTHO_LLM", "JOINT_LLM", "LLM_RESEARCH_PLANNER",
                  "MERGE_CONFIG", "HYPOTHESIS_ROLES"):
        try:
            import config as _c
            gates[gname] = getattr(_c, gname)
            continue
        except AttributeError:
            pass
        try:
            import generation_config as _g
            gates[gname] = getattr(_g, gname)
        except Exception:
            gates[gname] = None            # 读不出来＝未知，不折成 False
    if isinstance(gates.get("HYPOTHESIS_ROLES"), str):
        gates["HYPOTHESIS_ROLES"] = {"enabled": bool(gates["HYPOTHESIS_ROLES"].strip())}
    say("  生产口径：模型=%s 端点=%s 上限=%s 窗口旋钮=%s（对 /v1 空转）"
        % (LLM_MODEL, LLM_BASE_URL, LLM_MAX_TOKENS, LLM_NUM_CTX))
    say("  输入预算线 = %d − %d = %d token"
        % (SERVER_WINDOW, LLM_MAX_TOKENS, SERVER_WINDOW - (LLM_MAX_TOKENS or 0)))

    say("\n== 甲段 1：21 个调用点的静态上界（体积窗口＝`.create` 那个壳 ＋ 同文件里喂它 messages 的调用方） ==")
    rows = static_rows()
    say("%-18s %-24s %-16s %-9s %-14s %s"
        % ("调用点", "闸", "体积闸(字符)", "上界字符", "入口可达", "估算token"))
    pool = ("今日组合相对基准超额为正，换手维持在中位；量能族对回撤的贡献"
            "仍集中在两个窗口内，价格族未通过样本外检验，维持剔除。" * 400)
    for r in rows:
        gate = r["gate"]
        state = "—"
        if gate:
            g = gates.get(gate)
            state = ("关" if isinstance(g, dict) and not g.get("enabled", True)
                     else "开" if isinstance(g, dict) else str(g))
        caps = "+".join(str(c) for c in r["caps"]) if r["caps"] else "无"
        say("%-18s %-24s %-16s %-9s %-14s %s"
            % (r["label"], "%s/%s" % (gate or "无", state), caps,
               r["cap_chars"], ("经 " + r["reach_by"]) if r["reach"] else "不可达",
               tks(pool[:r["cap_chars"]]) if r["cap_chars"] else "-"))
        if r["counts"]:
            say("      ↳ 按**条数**截断 %s（条数封顶、单条长度不封顶）" % r["counts"])
        if r["unresolved"]:
            say("      ↳ system 段这几个名字没解析出长度：%s ⇒ 这一格只能当**下界**"
                % r["unresolved"])
        if r["unbounded"]:
            say("      ↳ 窗口内无体积闸；user 那一段＝`%s`" % (r["builder"] or "?"))
    n_cap = sum(1 for r in rows if not r["unbounded"])
    say("  窗口内带体积闸 %d 个／长度由数据说话 %d 个：%s"
        % (n_cap, len(rows) - n_cap,
           ", ".join(r["label"] for r in rows if r["unbounded"])))
    dead = [r["label"] for r in rows if not r["reach"]]
    say("  ⚠️ 从本线入口（etf/v1/*.py＋etf/v1/src/*.py）走 import 闭包**到不了**的 %d 个：%s"
        % (len(dead), ", ".join(dead) or "无"))
    say("  （可达＝图上有路，≠ 本场真执行；闸那一列管这件事。这一列只用来剔掉压根没接线的臂）")

    say("\n== 甲段 2：当前真会发出去的那几路，用生产归档真件重建 ==")
    live = live_texts()
    if "_daily_report_len" in live:
        say("  真日报正文（report_daily_*.json 最新一份）= %d 字符"
            % live.pop("_daily_report_len"))
    if "_library_n" in live:
        say("  因子库现数（factor_agent 的历史件来源）= %d 条，取末 10 条"
            % live.pop("_library_n"))
    notes = live.pop("_note", {})
    biggest = ("", "")
    for k, v in sorted(live.items(), key=lambda kv: -len(kv[1])):
        say("  %-16s %6d 字符  ≈%s token(cl100k 估算)｜%s"
            % (k, len(v), tks(v), notes.get(k, "?")))
        if len(v) > len(biggest[1]):
            biggest = (k, v)
    if not live:
        FAILS.append("甲段2 没重建出任何真件")
        say("  🛑 一条都没重建出来 ⇒ 这一格没有读数")

    say("\n== 甲段 3：无体积闸但可算的那两格（`build_daily_prompt` 对真件直接调用） ==")
    tpl = template_texts()
    if not tpl:
        FAILS.append("甲段3 没有读数")
    for k, (txt, empty) in sorted(tpl.items(), key=lambda kv: -len(kv[1][0])):
        say("  %-30s 模板件 %5d 字符 ≈%s token｜空子块：%s"
            % (k, len(txt), tks(txt), empty or "无"))
    if tpl:
        mx = max(tpl.values(), key=lambda kv: len(kv[0]))
        say("  ⇒ 这条臂今天 %d 字符，但源码里**没有体积闸**（slippage/strategy 整块拼进去）："
            "现在小是因为真件里那几个子块是空的（%s），不是因为形状天生小"
            % (len(mx[0]), mx[1] or "无"))

    top, wc = worst_case(rows)
    if top is None:
        say("  🛑 没有「入口可达且带体积闸」的调用点 ⇒ B2/B3 无句可填")
    else:
        say("  体积闸最大的可达调用点＝%s（%s:%d，闸 %s ＋ system %d）⇒ 填满＝%d 字符 ≈%s token"
            % (top["label"], top["rel"], top["line"],
               "+".join(map(str, top["caps"])), sum(top["sys"].values()),
               len(wc), tks(wc)))

    budget = SERVER_WINDOW - (LLM_MAX_TOKENS or 0)
    say("\n== 乙段：现量三条（原生 /api/chat，只 prefill 不解码） ==")
    if os.environ.get("FIT_DRY"):
        say("  FIT_DRY=1 ⇒ 乙段一条都不发（这一遍只验甲段的重建与点位表）")
        return 1 if FAILS else 0
    if not os.path.isfile(CONTROL_PROMPT):
        FAILS.append("正对照原文缺失")
        say("  🛑 正对照原文不在（%s）⇒ 乙段不起" % CONTROL_PROMPT)
        return 1
    ctrl = open(CONTROL_PROMPT, encoding="utf-8").read()
    say("[B0] 正对照＝10-03 那条真提示词（ollama 当晚报过 5502）")
    b0 = native_arm("B0", ctrl, 16384)
    if b0 is None or not (5502 * 0.95 <= b0 <= 5502 * 1.05):
        FAILS.append("正对照不落在 5502±5%")
        say("  🛑 尺子坏了（读到 %s），乙段其余读数一律不作数" % b0)
        return 1
    say("[B1] 今天实际最大的一条真件（%s）" % biggest[0])
    b1 = native_arm("B1", biggest[1], 16384)
    if top is None:
        b2 = b3 = None
    else:
        say("[B2] 体积闸填满的那一条（%s 的 %s）" % (top["label"],
            "+".join(map(str, top["caps"]))))
        b2 = native_arm("B2", wc, 16384)
        say("[B3] 同一句 B2 放回生产默认窗口 4096（验『2050＝4096−默认输出』那一格）")
        b3 = native_arm("B3", wc, SERVER_WINDOW)

    say("\n== 判读 ==")
    say("  B0=%s（尺子有效）" % b0)
    # 两条线要分开念：3072 是「4096 − max_tokens」这道**算式**，
    # B3 是服务端**实进**的那个数。10-04 两场（5502 与 4186 两种长度）都停在 2050
    # ⇒ 真限是常量，算式那条线从来没被执行过。
    ceiling = b3
    if ceiling is not None:
        say("  输入线两条：算式 4096−max_tokens＝%d｜现量（B3）＝%d"
            % (budget, ceiling))
    if b1 is None:
        FAILS.append("B1 无读数")
    else:
        line2 = (">=%d **越线**" % ceiling) if ceiling and b1 >= ceiling \
            else ("<%d 未越" % ceiling) if ceiling else "（B3 缺，只有一条线）"
        say("  B1＝今天主线最大真件 %s token｜算式线 %d（%s）｜现量线 %s ⇒ %s"
            % (b1, budget, "远未逼近" if b1 < budget else "已越", line2,
               "空转此刻不付代价" if (not ceiling or b1 < ceiling) else
               "空转今天就在吃字——最大真件已经贴到现量线上"))
    if b2 is None:
        FAILS.append("B2 无读数")
    else:
        verdict = ("主线连最坏都碰不到窗口 ⇒ 选项甲（保留空转）就是终态"
                   if b2 < budget else
                   "主线最坏 %s ≥ 算式线 %d ⇒ 空转有牙：体积闸一填就超窗，"
                   "而 /v1 超了是静默截、不报错" % (b2, budget))
        say("  B2＝体积闸填满 %s token ⇒ %s" % (b2, verdict))
        if ceiling:
            say("  B2 对现量线 %d：超出 %s token＝那句提示词的 %.0f%% 会被静默丢掉"
                % (ceiling, b2 - ceiling, 100.0 * (b2 - ceiling) / b2))
    if b3 is not None and b2 is not None:
        say("  B3＝同一句在 4096 窗口下只进 %s token（B2 是 %s）⇒ 丢掉 %s；"
            "本场 num_predict=1、请求里没留 2048 的输出位，仍然停在 %s"
            "⇒『4096 被默认输出预算占掉一半』这一格坐实（不是这次请求要的）"
            % (b3, b2, b2 - b3, b3))
    if b1 is not None and tks(biggest[1]):
        say("  换算标定：cl100k/ollama 在这句真件上＝%d/%d＝%.3f（10-03 那件是 0.925）"
            % (tks(biggest[1]), b1, tks(biggest[1]) / b1))

    say("\n" + ("全部通过" if not FAILS else "失败项: " + ", ".join(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
