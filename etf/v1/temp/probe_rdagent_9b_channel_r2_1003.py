# -*- coding: utf-8 -*-
"""B4 第二轮探针：把提示词换成 RD-Agent **真发过的那一句**，补第一轮欠的四格读数。

第一轮（`probe_rdagent_9b_channel_1003.py`，10-03 19:38 读数）证了两件事、欠了四件事：
  证：生产形态那一句单次 1653.7s、正文 0 字符（4096 窗口被思考吃光）；顶层 kwargs 关得掉思考
      （`think=False` 44.0s / `/v1`+`reasoning_effort="none"` 39.2s，两臂思考都归 0）。
  欠：① 快臂的正文 JSON 不可解析，但**没存全文与报错原文** ⇒「被截断」还是「少个括号」分不开；
      ② 提示词是我造的 ~600 token 短句 ⇒ **没撞上"真提示词本身就超过默认窗口"这一格**；
      ③ `think=False` + `num_ctx=16384` 的组合臂没打过；④ 每臂只打 1 次 ⇒ 没有稳定性读数。

**本轮的真提示词**（不是手写近似）：从本场生产会话留下的 `debug_tpl/*.pkl` 里取
`components.proposal.prompts:hypothesis_gen.system_prompt` 的 `rendered` 字段，**逐字节原样**发出去。
它实测 **20166 字符 / 5088 token（tiktoken cl100k）**，而原生口默认窗口只有 **4096**（`/api/ps` 的
`context_length`，09-28 与 10-03 两次都是这个数）⇒ **提示词自己就装不下，还没轮到思考**。
这大概就是 9b 那场「还在第一轮」刷 55 次、0 轮的病根，本轮把它量成读数。

五臂（B0/B4 各 1 次，B1/B2/B3 各 3 次看抖动）：
  B0  生产形态：不加任何参数，默认 4096 窗口
  B1  原生 + `think=False`（窗口仍 4096）
  B2  原生 + `think=False` + `num_ctx=16384`      ← 候选解
  B3  换口 `/v1` + `reasoning_effort="none"`（`/v1` 悄悄把窗口钉在 4096，本轮看真提示词下它怎么错）
  B4  原生 + `num_ctx=16384`，思考仍开（第一轮 A2 那条，换成真提示词再看一次代价）

每臂都落：墙钟、正文/思考字符、`finish_reason`、驻留窗口、**正文全文与 `json.loads` 报错原文**
（存 `temp/probe_r2_out/`），并每 30 秒刷一次进度（第一轮漏了 flush ⇒ 读数全在缓冲区里，本轮不留这个坑）。

用法（必须用 conda rdagent 环境，litellm 只装在那边）：
  env PYTHONNOUSERSITE=1 /home/sunwenkun/miniconda3/envs/rdagent/bin/python -u \
      etf/v1/temp/probe_rdagent_9b_channel_r2_1003.py \
      > etf/v1/temp/probe_rdagent_9b_channel_r2_1003.log 2>&1
"""
import hashlib
import json
import os
import pickle
import subprocess
import sys
import time

import litellm

MODEL = "qwen3.5:9b"
OLLAMA_NATIVE = "ollama_chat/" + MODEL
OLLAMA_V1_BASE = "http://localhost:11434/v1"
V1_ARM = "openai/" + MODEL

# 本文件在 <repo>/etf/v1/temp/ 下 ⇒ 往上**三级**才是仓库根（少一级就把 LOG_ROOT 指到不存在的
# <repo>/etf/etf/... 上，`find` 静默返回空、看起来像"生产没留提示词"）
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LOG_ROOT = os.path.join(REPO, "etf/v1/data/results/rdagent_output/log")
OUT_DIR = os.path.join(REPO, "etf/v1/temp/probe_r2_out")
PROMPT_CACHE = os.path.join(REPO, "etf/v1/temp/probe_real_prompt_hypothesis_gen_1003.txt")
URI = "components.proposal.prompts:hypothesis_gen.system_prompt"

# 用户侧那句是真模板里的一句话形状（RD-Agent 的 user_prompt 远短于 system），
# 本轮要量的是「窗口 + 思考 + JSON 可解析」，所以 system 用逐字节原件、user 用这条固定短句。
USER_LINE = ("Please provide a new hypothesis about ETF factors in the JSON format "
             "described above. Only output the JSON object.")


def find_biggest_rendered():
    """扫生产留下的 debug_tpl，取本 uri 最长那条 rendered（真提示词，不手写）"""
    best = None
    for path in subprocess.run(["find", LOG_ROOT, "-path", "*/debug_tpl/*/*.pkl"],
                               capture_output=True, text=True).stdout.split():
        try:
            with open(path, "rb") as f:
                obj = pickle.load(f)
        except Exception:
            continue
        if not isinstance(obj, dict) or obj.get("uri") != URI:
            continue
        text = obj.get("rendered")
        if isinstance(text, str) and (best is None or len(text) > len(best[1])):
            best = (path, text)
    return best


def load_prompt():
    """优先用已缓存的逐字节副本（可复跑、不依赖别人搬家 pkl），没有才现扫生产目录"""
    if os.path.exists(PROMPT_CACHE):
        with open(PROMPT_CACHE, encoding="utf-8") as f:
            return f.read(), PROMPT_CACHE + "（缓存副本）"
    found = find_biggest_rendered()
    if not found:
        raise SystemExit("找不到生产 debug_tpl 里的 " + URI + "，本轮不降级用手写提示词")
    path, text = found
    with open(PROMPT_CACHE, "w", encoding="utf-8") as f:
        f.write(text)
    return text, path


def ollama_ps():
    try:
        out = subprocess.run(["curl", "-s", "--max-time", "8",
                              "http://localhost:11434/api/ps"],
                             capture_output=True, text=True).stdout
        models = json.loads(out or "{}").get("models", [])
        return [(m["name"], round(m["size"] / 2 ** 30, 2), m.get("context_length"))
                for m in models]
    except Exception as e:
        return [("读失败", type(e).__name__, None)]


def mem_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return round(int(line.split()[1]) / 2 ** 20, 2)
    return None


def consume(resp, deadline):
    """按 rdagent 的读法消费流；**硬墙钟自己掐**（第一轮实证 `request_timeout` 管不住流式总时长）"""
    content, reasoning, finish = "", "", None
    t0 = time.time()
    last_beat = t0
    for chunk in resp:
        if time.time() - t0 > deadline:
            return content, reasoning, finish or "deadline", True
        try:
            choice = chunk["choices"][0]
        except (KeyError, IndexError, TypeError):
            continue
        delta = choice.get("delta") or {}
        if choice.get("finish_reason"):
            finish = choice["finish_reason"]
        content += delta.get("content") or ""
        reasoning += (delta.get("reasoning_content") or delta.get("thinking") or "")
        if time.time() - last_beat > 30:
            last_beat = time.time()
            print(f"      …{round(time.time() - t0)}s 正文 {len(content)} / 思考 {len(reasoning)} 字",
                  flush=True)
    return content, reasoning, finish, False


def json_check(text):
    """返回 (可解析, 报错原文)；空串单独算一种状态，不折成 False 判红"""
    if not text.strip():
        return None, "正文为空（不是解析失败，是没内容）"
    try:
        json.loads(text)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def selfcheck_ruler():
    """先证明这把 JSON 尺子有牙：合法串必须 True、坏串必须 False（第一轮那种"分不清病因"不再重演）"""
    ok_good, _ = json_check('{"factors": [{"h": "x", "e": "ma(df,5)"}]}')
    ok_bad, err = json_check('{"factors": [{"h": "x", "e": "ma(df,5)"}')
    ok_empty, _ = json_check("   ")
    print(f"[尺子自检] 合法={ok_good}（须 True） 缺括号={ok_bad}（须 False） "
          f"空正文={ok_empty}（须 None）｜报错原文样例: {err[:60]}")
    if not (ok_good is True and ok_bad is False and ok_empty is None):
        raise SystemExit("尺子自检没过，读数会失真，不起场")


def arm(tag, model, deadline, reps, dump_stem, **extra):
    rows = []
    for rep in range(1, reps + 1):
        print(f"\n=== {tag}  rep {rep}/{reps}  cap={deadline}s  extra={extra}", flush=True)
        print(f"    [起场] MemAvailable={mem_gb()}GB  驻留={ollama_ps()}", flush=True)
        t0 = time.time()
        try:
            resp = litellm.completion(model=model, messages=MESSAGES, stream=True,
                                      request_timeout=deadline, **extra)
            content, reasoning, finish, hit = consume(resp, deadline)
        except Exception as e:
            sec = time.time() - t0
            print(f"    ✗ {tag} rep{rep} {type(e).__name__} @ {sec:.1f}s :: {str(e)[:200]}",
                  flush=True)
            rows.append({"arm": tag, "rep": rep, "sec": round(sec, 1),
                         "error": type(e).__name__})
            continue
        sec = time.time() - t0
        ok, err = json_check(content)
        stem = os.path.join(OUT_DIR, f"{dump_stem}_r{rep}")
        with open(stem + "_content.txt", "w", encoding="utf-8") as f:
            f.write(content)
        with open(stem + "_thinking.txt", "w", encoding="utf-8") as f:
            f.write(reasoning[:20000])
        with open(stem + "_meta.json", "w", encoding="utf-8") as f:
            json.dump({"arm": tag, "rep": rep, "sec": round(sec, 1), "finish": finish,
                       "deadline_hit": hit, "json_ok": ok, "json_err": err,
                       "content": len(content), "thinking": len(reasoning),
                       "resident": ollama_ps()}, f, ensure_ascii=False, indent=1)
        print(f"    {tag} rep{rep}: 墙钟={sec:.1f}s 正文={len(content)} 思考={len(reasoning)} "
              f"finish={finish}{'（自己掐的硬截止）' if hit else ''} JSON={ok}", flush=True)
        if err:
            print(f"      报错原文: {err[:200]}", flush=True)
        print(f"      全文已存 {stem}_content.txt", flush=True)
        print(f"    [终场] MemAvailable={mem_gb()}GB  驻留={ollama_ps()}", flush=True)
        rows.append({"arm": tag, "rep": rep, "sec": round(sec, 1), "content": len(content),
                     "thinking": len(reasoning), "finish": finish, "deadline_hit": hit,
                     "json_ok": ok, "json_err": err[:160]})
    return rows


def main():
    global MESSAGES
    ver = subprocess.run(["ollama", "--version"], capture_output=True,
                         text=True).stdout.strip()
    print("== B4 第二轮：真提示词（生产逐字节原件）五臂实测 ==", flush=True)
    print(f"    ollama: {ver}", flush=True)
    prompt, src = load_prompt()
    try:
        import tiktoken
        ntok = len(tiktoken.get_encoding("cl100k_base").encode(prompt))
    except Exception as e:
        ntok = f"算不出（{type(e).__name__}），字符/4≈{len(prompt) // 4}"
    print(f"    提示词来源: {src}", flush=True)
    print(f"    提示词: {len(prompt)} 字符 / {ntok} token / sha256={hashlib.sha256(prompt.encode()).hexdigest()[:16]}",
          flush=True)
    MESSAGES = [{"role": "system", "content": prompt},
                {"role": "user", "content": USER_LINE}]
    selfcheck_ruler()
    os.makedirs(OUT_DIR, exist_ok=True)
    results = []
    results += arm("B0 生产形态(默认窗口)", OLLAMA_NATIVE, 600, 1, "B0")
    results += arm("B1 think=False(默认窗口)", OLLAMA_NATIVE, 300, 3, "B1", think=False)
    results += arm("B2 think=False+num_ctx=16384", OLLAMA_NATIVE, 300, 3, "B2",
                   think=False, num_ctx=16384)
    results += arm("B3 /v1+effort=none", V1_ARM, 300, 3, "B3",
                   api_base=OLLAMA_V1_BASE, api_key="ollama", reasoning_effort="none")
    results += arm("B4 num_ctx=16384(思考仍开)", OLLAMA_NATIVE, 1200, 1, "B4", num_ctx=16384)
    print("\n== 汇总（每臂每次）==", flush=True)
    for r in results:
        print("   ", r, flush=True)
    print("\n判读口径：B2 三次里 ≥2 次 json_ok=True 且墙钟 <120s ⇒ 那条通道值得接进驱动；"
          "B3 若明显差于 B1 ⇒ `/v1` 那个 4096 静默钉死在真提示词下就是否决项；"
          "B0 的截断形状用来判「今天生产发出去的到底长什么样」。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
