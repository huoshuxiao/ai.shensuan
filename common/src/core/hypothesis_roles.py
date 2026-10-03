# -*- coding: utf-8 -*-
"""多角色前置假设闸：生成 → 批判 → 修正 → 定稿

四个角色各有一套独立 prompt：假设生成（只出经济学命题，不写代码）、
代码实现（命题 → DSL 表达式）、批判者（逐条判 pass/revise/reject）、
反思者（把本轮战果压成下一轮的反馈）。

为什么只做成「前置闸」：RD-Agent 的工作区在容器里是只读挂载，自定义角色
塞不进它的循环；而本仓的 IC 闸与判重环（multi_source_mining）已经在
`_evaluate` 之后，动它们等于动判据。所以这一层只负责**把不合格的假设
挡在 IC 闸之前**，产出的形态与 `LLMFactorAgent._generate` 完全一致
（[{"name","expr","reason"}]），下游一行不改。

定稿阶段**不调 LLM**：试编译、未来函数、判重三件事都有确定答案，交给模型
判只会多烧一次调用。反思者是唯一带状态的角色，它的输出替换掉原来那行
「上轮平均 IC=…」，跨轮记忆因此才有内容。

开关 `HYPOTHESIS_ROLES`（环境变量 ETF_HYPOTHESIS_ROLES / STOCK_HYPOTHESIS_ROLES）
默认关 ⇒ 走原 `_generate`，逐字节不变。
"""

import os
import json
import re
import time

from tenacity import retry, stop_after_attempt, wait_exponential

from config import (LLM_MODEL, LLM_API_KEY_ENV, HYPOTHESIS_PER_LOOP,
                    HYPOTHESIS_ROLES)
from factor_dsl import safe_eval
from factor_static_check import check_expr
from llm_client import make_openai_client


# ========== 四套角色 prompt ==========

PROMPT_HYPOTHESIS = """你是量化研究员，只提**经济学假设**，一行代码都不要写。

标的池：场内 ETF 的日线（close/open/high/low/volume/returns）。
可写的列只有这六列，没有基本面、没有成分股权重、没有资金流。

输出 JSON：
{"hypotheses":[{"id":1,"idea":"一句话经济含义","channel":"趋势|反转|波动|量能|期限结构",
"expect":"预期与未来收益的方向（正/负/非对称）"}]}

要求：
1. 恰好 <<N>> 条，每条的 idea 必须能被这六列里的东西检验，不能依赖拿不到的数据
2. 不许与「已试过的假设」重复（见用户消息），换角度而不是换措辞
3. 只用 t 日及之前的数据讲道理，不许出现"知道明天涨跌"式的循环论证
"""

PROMPT_CODER = """你是量化开发工程师，把经济学假设翻译成因子表达式。

可用算子（与执行环境逐字一致，除此之外一律非法）：
- 数据列: close, open, high, low, volume, returns
- 滚动: ma(x, n), std(x, n), max(x, n), min(x, n), rank(s, n),
  ts_max(s, n), ts_min(s, n), quantile(s, n, q) —— **ma/std/max/min 的 x 可以直接传列**（`ma(volume, 20)`、`std(low, 60)`、`max(high, 240)`、`min(close, 5)` 都合法），传 `df` 时按 close 算
- 序列: delay(s, n), delta(s, n), ts_sum(s, n), ts_mean(s, n), ts_std(s, n),
  abs(s), log(s), sign(s)
- 关系: corr(a, b, n), slope(s, n), rsquare(s, n), resi(s, n), idx_max(s, n), idx_min(s, n)
- 窗口 n 必须是**正整数**，建议 5~240；q ∈ (0,1)

输出 JSON：
{"factors":[{"name":"英文小写下划线短名","expr":"单行表达式","idea":"对应哪条假设"}]}

硬性约束（违反即被丢弃，不用解释）：
1. 表达式里不许出现 shift、不许出现负窗口（delay/delta 的 n 必须 > 0）
2. 不许写 df.、df[ 这类取数下标，直接用裸列名
3. 分母一律加 1e-9 防除零
"""

PROMPT_CRITIC = """你是苛刻的审稿人，逐条判定下面的因子表达式能不能进回测。

判据（按顺序取第一条命中的）：
- reject：经济学上站不住（与 idea 说的不是一回事）、或者恒为常数/全 NaN
- reject：需要 t 日收盘之后的信息才能算出来
- revise：想法成立但写法有问题（窗口取反、符号与 expect 相反、量纲不匹配、
  分母可能为 0），**给出改后的表达式**
- pass：idea、写法、方向三者自洽

输出 JSON：
{"verdicts":[{"name":"与输入同名","verdict":"pass|revise|reject",
"problem":"一句话理由","fix_expr":"仅 revise 时给，单行表达式；否则空串"}]}

要求：<<N>> 条输入必须给 <<N>> 条判定，name 逐字照抄输入，不许增删条目。
"""


def fill(tpl: str, n) -> str:
    """把 <<N>> 换成条数。**不用 str.format**：这几套 prompt 正文里有 JSON
    字面大括号，format 会把 `{"hypotheses": [...]}` 当成字段名解析并抛
    KeyError；而调用点都套着宽 except ⇒ 异常被吞成「静默回退模板/回退空表」。
    本仓历史上 4 处就是这么坏的（llm_factor_agent.py:99、llm_genetic_hybrid.py
    的 SEED/FEEDBACK、llm_research_planner.py 的 RESEARCH），10-01 全部改走这里，
    由 etf/v1/temp/check_prompt_fill_sites_1001.py 钉住。
    """
    return tpl.replace("<<N>>", str(int(n)))

PROMPT_REFLECT = """你是复盘者。基于本轮的判定结果与实测 IC，写下一轮的研究提示。

输出 JSON：
{"feedback":"不超过 200 字，给下一轮假设生成用"}

要求：
1. 说清「哪一类 channel 在系统性拿不到 IC」和「哪一类写法被反复判 reject」
2. 只说**这一轮真数据里看到的**，不许泛泛而谈量化常识
3. 不许出现具体的因子名字，要说规律
"""


# ========== 定稿：确定性校验（不调 LLM）==========

# 未来函数/语法/白名单那把尺子在 `factor_static_check`（10-01 提出去，
# 因为写库单点也要用它）；这里只留判重用的写法指纹。

def _normalize(expr: str) -> str:
    """判重用的写法指纹：去掉空白差异与 +1e-9 这类防除零尾巴"""
    e = re.sub(r"\s+", "", expr or "")
    return re.sub(r"(\+|\-|/)1e-9$", "", e)


def dedup_key(name: str, expr: str) -> str:
    return f"{(name or '').strip().lower()}|{_normalize(expr)}"


# ========== 闸本体 ==========

class HypothesisGate:
    """四角色流水线。调用次数 = 每轮 4~5 次（修正那趟只在批判者判出 revise 时才发）。

    stats 是给冒烟账单用的计数器，不参与任何判定。
    """

    def __init__(self, pool, llm):
        self.pool = pool
        # llm 是 LLMClient 实例而不是布尔值：multi_source_mining 的 simple 源
        # 会在构造**之后**把 agent.llm.enabled 改成 False 来强制走模板，
        # 这里快照成布尔就会让"simple"这一源照样烧一次 LLM
        self._llm = llm
        self.stats = {"calls": 0, "stage_sec": {}, "hypotheses": 0,
                      "coded": 0, "pass": 0, "revise": 0, "reject": 0,
                      "dropped_static": 0, "dropped_compile": 0,
                      "dropped_dup": 0, "final": 0, "json_fail": 0}
        self._seen = set()
        # 写法指纹单独一格：历史里名字可能不同但表达式一模一样
        self._seen_expr = set()

    @property
    def enabled(self) -> bool:
        return bool(HYPOTHESIS_ROLES) and self._llm.enabled

    # ---- LLM 出口 ----
    @retry(stop=stop_after_attempt(2),
           wait=wait_exponential(multiplier=1, min=2, max=10))
    def _chat(self, system, user):
        self.stats["calls"] += 1
        client = make_openai_client(
            api_key=os.environ.get(LLM_API_KEY_ENV, ""))
        resp = client.chat.completions.create(
            model=LLM_MODEL, temperature=0.7,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return resp.choices[0].message.content

    def _json(self, content, key, tag):
        """一次调用坏 JSON 只作废这一趟，不作废整轮：交回空列表并计数"""
        try:
            return json.loads(content).get(key, [])
        except Exception as e:
            self.stats["json_fail"] += 1
            print(f"  ⚠️ {tag} 返回的 JSON 不可解析({type(e).__name__})，"
                  f"本趟作废（不是静默跳过）")
            return []

    def _stage(self, tag, fn):
        t0 = time.time()
        out = fn()
        self.stats["stage_sec"][tag] = round(time.time() - t0, 1)
        return out

    # ---- 四角色 ----
    def _hypothesize(self, feedback, tried):
        def go():
            user = (f"已试过的假设：{json.dumps(tried[-15:], ensure_ascii=False)}"
                    f"\n上一轮复盘：{feedback or '无'}\n"
                    f"请提出 {HYPOTHESIS_PER_LOOP} 条新假设。")
            return self._json(self._chat(fill(PROMPT_HYPOTHESIS,
                                              HYPOTHESIS_PER_LOOP), user),
                              "hypotheses", "假设生成")
        return self._stage("假设生成", go)

    def _code(self, hypotheses):
        def go():
            user = "待实现的假设：" + json.dumps(hypotheses, ensure_ascii=False)
            return self._json(self._chat(PROMPT_CODER, user),
                              "factors", "代码实现")
        return self._stage("代码实现", go)

    def _criticize(self, factors):
        def go():
            slim = [{"name": f.get("name"), "expr": f.get("expr"),
                     "idea": f.get("idea", "")} for f in factors]
            user = ("待审因子：" + json.dumps(slim, ensure_ascii=False)
                    + f"\n共 {len(slim)} 条，请逐条给判定。")
            return self._json(self._chat(fill(PROMPT_CRITIC, len(slim)),
                                         user),
                              "verdicts", "批判者")
        return self._stage("批判者", go)

    def _revise(self, jobs):
        """jobs: 批判者判 revise 的条目。全 pass 时这一趟不发，省一次调用"""
        def go():
            user = ("这些写法被审稿人要求返工，请按 problem 重写表达式：\n"
                    + json.dumps(jobs, ensure_ascii=False)
                    + "\n只改写法，经济含义保持不变。")
            return self._json(self._chat(PROMPT_CODER, user),
                              "factors", "修正")
        return self._stage("修正", go)

    def reflect(self, results):
        """results: _evaluate 的返回值列表（带 mean_ic/pass）"""
        if not self.enabled or not results:
            return ""

        def go():
            slim = [{"name": r.get("name"), "mean_ic": round(r.get("mean_ic", 0), 5),
                     "pass": bool(r.get("pass"))} for r in results]
            user = ("本轮实测结果：" + json.dumps(slim, ensure_ascii=False)
                    + "\n请写下一轮的研究提示。")
            return self._json(self._chat(PROMPT_REFLECT, user),
                              "feedback", "反思者")
        out = self._stage("反思者", go)
        return (out or "").strip()[:400]

    # ---- 定稿：确定性三关 ----
    def _sample_df(self):
        for df in self.pool.values():
            if len(df) >= 30:
                return df.iloc[:min(len(df), 250)]
        return None

    def _finalize(self, cand):
        """试编译 + 静态体检 + 判重，全过才进 IC 闸"""
        sample = self._sample_df()
        out = []
        for f in cand:
            name, expr = (f.get("name") or "").strip(), (f.get("expr") or "").strip()
            if not name or not expr:
                self.stats["dropped_static"] += 1
                continue
            why = check_expr(expr)
            if why:
                self.stats["dropped_static"] += 1
                print(f"  🚫 {name} 静态体检不合格: {why}")
                continue
            if sample is not None:
                try:
                    s = safe_eval(expr, sample)
                    if s.isna().all():
                        self.stats["dropped_compile"] += 1
                        print(f"  🚫 {name} 真数据上全 NaN")
                        continue
                except Exception as e:
                    self.stats["dropped_compile"] += 1
                    print(f"  🚫 {name} 试编译失败: "
                          f"{type(e).__name__}: {e}")
                    continue
            k = dedup_key(name, expr)
            if k in self._seen or _normalize(expr) in self._seen_expr:
                self.stats["dropped_dup"] += 1
                print(f"  ♻️ {name} 与已试过的写法重复")
                continue
            self._seen.add(k)
            out.append({"name": name, "expr": expr,
                        "reason": f.get("idea") or f.get("reason") or ""})
        self.stats["final"] = len(out)
        return out

    # ---- 主流程 ----
    def generate(self, feedback="", tried=None):
        """返回 [{"name","expr","reason"}]，与原 _generate 同形"""
        tried = [t for t in (tried or []) if t]
        # 跨轮记忆：本轮定稿前先吃掉历史写法，否则第 2 轮会重新发明第 1 轮
        for t in tried:
            self._seen_expr.add(_normalize(t))
        hyp = self._hypothesize(feedback, tried)
        self.stats["hypotheses"] = len(hyp)
        if not hyp:
            return self._finalize([])
        coded = self._code(hyp)
        self.stats["coded"] = len(coded)
        if not coded:
            return self._finalize([])
        verdicts = {v.get("name"): v for v in self._criticize(coded)}
        accepted, jobs = [], []
        for f in coded:
            v = verdicts.get(f.get("name"))
            # 批判者漏判的条目按 reject 计：宁缺毋滥，闸门不能因为
            # 审稿人没签字就默认放行
            if v is None:
                self.stats["reject"] += 1
                print(f"  ⚠️ 批判者未对 {f.get('name')} 出具判定，按拒收处理")
                continue
            vd = (v.get("verdict") or "").strip().lower()
            if vd == "pass":
                self.stats["pass"] += 1
                accepted.append(f)
            elif vd == "revise":
                self.stats["revise"] += 1
                jobs.append({"name": f.get("name"), "expr": f.get("expr"),
                             "problem": v.get("problem", ""),
                             "fix_expr": v.get("fix_expr", "")})
            else:
                self.stats["reject"] += 1
                print(f"  ❌ 批判者拒收 {f.get('name')}: "
                      f"{v.get('problem', '')}")
        if jobs:
            fixed = {x.get("name"): x for x in self._revise(jobs)}
            for j in jobs:
                # 采纳顺序：修正后的新写法 > 审稿人自带的 fix_expr > 放弃
                got = fixed.get(j["name"])
                expr = (got or {}).get("expr") or j["fix_expr"]
                if expr:
                    accepted.append({"name": j["name"], "expr": expr,
                                     "idea": got.get("idea") if got else ""})
                else:
                    self.stats["dropped_static"] += 1
                    print(f"  ❌ {j['name']} 返工未产出可用写法")
        return self._finalize(accepted)

    def bill(self) -> str:
        s = self.stats
        stages = " ".join(f"{k}={v}s" for k, v in s["stage_sec"].items())
        return (f"LLM 调用 {s['calls']} 次｜假设 {s['hypotheses']} → 代码 "
                f"{s['coded']} → pass {s['pass']}/revise {s['revise']}/"
                f"reject {s['reject']} → 定稿 {s['final']}"
                f"（静态拒 {s['dropped_static']}·编译拒 {s['dropped_compile']}"
                f"·重复 {s['dropped_dup']}·坏 JSON {s['json_fail']}）｜{stages}")
