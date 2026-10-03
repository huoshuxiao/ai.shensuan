# -*- coding: utf-8 -*-
"""两步证据（记忆探针 + 60 场回放）共用的三件小事：简称表、下单层提示词、一次 LLM 调用。

为什么要抽出来：这两步要**并列读**（回放里 LLM 臂跑赢了，第一句要问的就是"它是不是背过"，
而答案来自探针）。两份提示词/两份简称口径 = 两次实验的输入不是同一份，并列就没有意义。

只读生产、不写 `stock/v1/data/` 任何归档。
"""
import json
import os
import time

import pandas as pd

from config import (ASHARE_INDUSTRY_CSV, ASHARE_ORDER_MAX_PER_BOARD,  # noqa: F401
                    ASHARE_ORDER_MAX_PER_INDUSTRY, LLM_MODEL)
from strategy.ashare_screen import INDUSTRY_UNKNOWN
from core.llm_client import make_openai_client

# 单次调用的墙钟上限。取 900s 的理由：本机纯 CPU 冷加载那一次实测 330.4s（含 6.07GB 权重
# 装载），热态 33~38s。900s = 冷加载的 2.7 倍，够装完模型又不让一场卡死拖垮整批 60 场。
CALL_TIMEOUT = 900.0

# 下单层的系统提示词：与 stock/v1/temp/probe_llm_picks_0927.py 同一段措辞（那道闸门
# 实测过 6/6 次被破，所以回放里**同时**跑「裸答案」和「过一遍 order_candidates」两条腿）。
SYSTEM_ORDER = (
    "你是 A 股日线量化辅助系统的下单层。只回 JSON，不要任何解释文字。\n"
    "任务：从给定的观察名单里挑 5 只明天开盘买入。\n"
    f"硬约束（必须满足）：同一板块最多 {ASHARE_ORDER_MAX_PER_BOARD} 只；"
    f"同一行业最多 {ASHARE_ORDER_MAX_PER_INDUSTRY} 只（行业写「{INDUSTRY_UNKNOWN}」的不受"
    "此限，因为北交所与科创板大部分没有行业映射）；只能从给定的名单里选，"
    "不许出现名单外的代码。\n"
    '输出格式：{"picks":[{"code":"SZ000001","reason":"不超过20字"}]}')


def load_names():
    """代码→简称（去掉新浪那份表里的全角空格，如「苏 泊 尔」→「苏泊尔」）。

    ⚠️ 这是**今天**抓的一份映射表往历史回溯用（与 dd_scale 那台秤同一边界）：2015 年在场、
    如今退市/改名的票在这里取不到 ⇒ 调用方按「没有简称就用代码」降级，不许假装那行在。
    """
    p = os.environ.get("STOCK_INDUSTRY_CSV") or ASHARE_INDUSTRY_CSV
    if not os.path.exists(p):
        return {}
    d = pd.read_csv(p, dtype=str).fillna("")
    if "简称" not in d.columns:
        return {}
    return {c: n.replace(" ", "") for c, n in zip(d["代码"], d["简称"]) if n}


def client():
    """正向链路同一个构造函数（三道 .env 闸门自动注入），但把 SDK 重试按死在 0。

    SDK 默认 `max_retries=2`：一次 /v1 卡死会静默重试 3 遍（09-27 记过这条），
    60 场批量里那就是几十小时的坑。这里宁可记一笔「这一场没答上」并续跑。
    """
    return make_openai_client(max_retries=0, timeout=CALL_TIMEOUT)


def ask_json(cl, system, user, label):
    """一次调用 → (dict 或 None)。None = 抛错或 JSON 不可解析，错误原文进读数。"""
    t0 = time.time()
    try:
        resp = cl.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.0,
            response_format={"type": "json_object"})
    except Exception as e:
        return {"档": label, "秒": round(time.time() - t0, 1),
                "错误": f"{type(e).__name__}: {str(e)[:200]}"}
    content = resp.choices[0].message.content or ""
    rec = {"档": label, "秒": round(time.time() - t0, 1), "模型": LLM_MODEL,
           "finish": resp.choices[0].finish_reason,
           "completion_tokens": getattr(resp.usage, "completion_tokens", None),
           "content": content}
    try:
        obj = json.loads(content)
    except Exception as e:
        rec["错误"] = f"JSON 不可解析 {type(e).__name__}: {str(e)[:120]}"
        return rec
    rec["picks"] = [str(p.get("code", "")).upper() for p in obj.get("picks", [])]
    rec["reasons"] = {str(p.get("code", "")).upper(): str(p.get("reason", ""))
                      for p in obj.get("picks", [])}
    return rec


class Cache:
    """按 key 落盘的 JSONL 缓存：命中就不调模型。

    为什么要它：60 场回放要烧 40 分钟 CPU，任何一次中断（内存/断电/我改错一行）都不该
    重烧一遍。**追加**写，所以中途崩了已跑的那批还在。
    """

    def __init__(self, path):
        self.path = path
        self.d = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    self.d[r["key"]] = r
        self.hits = 0

    def get(self, key):
        r = self.d.get(key)
        if r is not None:
            self.hits += 1
        return r

    def put(self, key, rec):
        rec = dict(rec)
        rec["key"] = key
        self.d[key] = rec
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
