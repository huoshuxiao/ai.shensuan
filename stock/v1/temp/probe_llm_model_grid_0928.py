# -*- coding: utf-8 -*-
"""换模型横向网格：同一份提示词、同一把尺子，一次量完一个候选模型的四种走法。

为什么要有这台网格而不是再抄一个探针：09-27/09-28 已经量过 `qwen3.5:9b`（生产现况）与
`qwen3.5:4b`（开思考判死，见 `probe_llm_4b_think_0928.py`）。再来一个候选（这次是
`qwen2.5-coder:7b`）若换一份代码、换一段提示词，读数就不可并列——用户逐字挑过"口径不统一"。
所以池子、系统提示词、合规尺子全部复用上一跑的那三件（`load_pool`/`pool_text`/
`SYSTEM_ORDER`/`check_compliance`），**只有模型名和请求体这几格在动**。

四格各自隔离一个因子（顺序=便宜的在前，冷加载只付一次）：
  G1 带 `reasoning_effort=none` + `max_tokens=1024` + JSON 约束
     ⇒ 这一格是「只改 `.env` 里模型名、三道闸门一行不动」的真实结果。
     非思考模型收到 `reasoning_effort` 是**报错**还是**忽略**，本机从没量过；
     若报错，那"换模型"就不是改一行配置的事（`llm_client` 会给每个请求注入这个字段）。
  G2 与 G1 完全同体、连打第二次 ⇒ 拆出冷加载/热态两段墙钟，并顺手验 `temperature=0`
     下两次名单是否逐位相同（不相同=不能进归档链路，09-27 那条判据）。
  G3 不带 `reasoning_effort`（其余同 G1）⇒ 若 G1 红了，这一格说明是不是那个字段惹的。
  G4 不带 `reasoning_effort` 也不带 `response_format` ⇒ 它在自然输出下给不给干净 JSON，
     量的是"提示词里那句只回 JSON"到底承不承得住（生产 20 个调用点都带约束，
     但 `LITELLM_ENABLE_RESPONSE_SCHEMA=false` 那条历史说明本地模型不一定支持严格模式）。

尺子先验再花钱：直接 import 上一跑的 `build_fixtures`/`check_compliance`，5 个夹具全绿才调模型。
只读生产归档（`daily_signal/buy_*.csv`）、不装面板、不写 `stock/v1/data/` 任何文件。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_model_grid_0928.py qwen2.5-coder:7b
    /usr/bin/python3.10 stock/v1/temp/probe_llm_model_grid_0928.py qwen2.5-coder:7b --detect-check
产物：stock/v1/temp/tmp_llm_model_grid_0928/<模型名>/grid_raw.jsonl、grid.log
"""
import json
import os
import sys
import time

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "src"))
# 三道闸门在网格里**故意失效**（`load_dotenv` 是 override=False ⇒ 进程环境变量优先），
# 请求体由下面四格自己写；必须赶在 import config 之前设。
os.environ["STOCK_LLM_MAX_TOKENS"] = "0"
os.environ["STOCK_LLM_REASONING_EFFORT"] = ""

import _bootstrap  # noqa: F402,E402  必须先挂 sys.path，否则 `config` 解析到无名命名空间包
from config import LLM_BASE_URL, LLM_MODEL  # noqa: E402
from core.llm_client import make_openai_client  # noqa: E402

sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "temp"))
from llm_evidence_common import SYSTEM_ORDER  # noqa: E402
from probe_llm_4b_think_0928 import load_pool, pool_text  # noqa: E402
from probe_llm_picks_0927 import build_fixtures, check_compliance  # noqa: E402

OUT_ROOT = os.path.join(_ROOT, "stock", "v1", "temp", "tmp_llm_model_grid_0928")
CALL_TIMEOUT = 900.0
ROWS = [
    ("G1 带effort=none·1024·json", dict(effort="none", max_tokens=1024, json_mode=True)),
    ("G2 同体连打第二次", dict(effort="none", max_tokens=1024, json_mode=True)),
    ("G3 不带effort·1024·json", dict(effort=None, max_tokens=1024, json_mode=True)),
    ("G4 不带effort·1024·无约束", dict(effort=None, max_tokens=1024, json_mode=False)),
]


def one_call(cl, model, tag, kw_on, user_prompt, rows, det):
    kw = dict(model=model,
              messages=[{"role": "system", "content": SYSTEM_ORDER},
                        {"role": "user", "content": user_prompt}],
              temperature=0.0)
    if kw_on["effort"]:
        kw["reasoning_effort"] = kw_on["effort"]
    if kw_on["max_tokens"]:
        kw["max_tokens"] = kw_on["max_tokens"]
    if kw_on["json_mode"]:
        kw["response_format"] = {"type": "json_object"}
    rec = {"档": tag, "模型": model, "effort": kw_on["effort"] or "不传",
           "封顶": kw_on["max_tokens"] or "无",
           "JSON约束": "有" if kw_on["json_mode"] else "无"}
    t0 = time.time()
    try:
        resp = cl.chat.completions.create(**kw)
    except Exception as e:
        rec.update({"秒": round(time.time() - t0, 1),
                    "错误": f"{type(e).__name__}: {str(e)[:220]}"})
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        return rec
    msg = resp.choices[0].message
    dump = msg.model_dump()
    extras = {k: len(str(v)) for k, v in dump.items()
              if k not in ("role", "content") and v}
    content = msg.content or ""
    rec.update({"秒": round(time.time() - t0, 1),
                "finish": resp.choices[0].finish_reason,
                "出词": getattr(resp.usage, "completion_tokens", None),
                "正文字符": len(content), "附加字段": extras, "原文": content})
    try:
        picks = json.loads(content).get("picks", [])
        codes = [str(p.get("code", "")).upper() for p in picks]
    except Exception as e:
        rec["可解析"] = f"否({type(e).__name__})"
        print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                         ensure_ascii=False), flush=True)
        return rec
    outside, bo, idup = check_compliance(codes, rows)
    rec.update({"可解析": True, "条数": len(codes), "池外": outside,
                "板块超限": bo, "行业重复": idup,
                "与生产重合": len(set(codes) & set(det)), "picks": codes})
    print(json.dumps({k: v for k, v in rec.items() if k != "原文"}, ensure_ascii=False),
          flush=True)
    return rec


def main():
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or [LLM_MODEL]
    out = []
    for model in models:
        tag_b, df, det = load_pool()
        rows = df.set_index("code")
        user_prompt = (f"场次 {tag_b}（收盘价截面，T+1 开盘买）。观察名单：\n"
                       f"{pool_text(df)}\n\n请挑 5 只，按你想要的优先级从高到低排列。")
        out_dir = os.path.join(OUT_ROOT, model.replace(":", "_"))
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n########## 模型 {model}｜场次 {tag_b}｜池子 {len(df)} 只｜"
              f"生产 5 只={det}", flush=True)
        print(f"端点 {LLM_BASE_URL}｜.env 里那份生产模型={LLM_MODEL}"
              "（本网格用显式 model= 覆盖，.env 一字未改）", flush=True)

        fixtures = build_fixtures(df, rows, det)
        bad = []
        for name, codes, want in fixtures:
            if codes is None:
                print(f"[尺子自检] {name} ❌ ⇒ 夹具造不出来，不调模型", flush=True)
                return 1
            got = [list(x) for x in check_compliance(codes, rows)]
            ok = got == [list(w) for w in want]
            print(f"[尺子自检] {name}: {'✅' if ok else '❌ 读数=' + str(got)}", flush=True)
            if not ok:
                bad.append(name)
        if bad:
            print(f"❌ 尺子脏（{len(bad)}/{len(fixtures)}）⇒ 不调模型", flush=True)
            return 1
        if "--detect-check" in sys.argv:
            print("（--detect-check：只验尺子，没调模型）", flush=True)
            return 0

        cl = make_openai_client(max_retries=0, timeout=CALL_TIMEOUT, api_key="ollama")
        recs = []
        for tag, kw_on in ROWS:
            print(f"\n===== {model} · {tag} =====", flush=True)
            recs.append(one_call(cl, model, tag, kw_on, user_prompt, rows, det))
        with open(os.path.join(out_dir, "grid_raw.jsonl"), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        out += recs

        print(f"\n===== {model} 四格账单 =====", flush=True)
        for r in recs:
            if "错误" in r:
                print(f"{r['档']}: ❌ {r['秒']}s {r['错误'][:150]}", flush=True)
                continue
            tok = r["出词"] or 0
            print(f"{r['档']}: {r['秒']}s｜finish={r['finish']}｜出词 {tok}"
                  f"（{tok / r['秒'] if r['秒'] else float('nan'):.1f} tok/s）｜"
                  f"正文 {r['正文字符']} 字符｜可解析={r['可解析']}", flush=True)
            if "条数" in r:
                print(f"    条数 {r['条数']}/5｜池外 {r['池外'] or '无'}｜"
                      f"破板块 {r['板块超限'] or '无'}｜破行业 {r['行业重复'] or '无'}｜"
                      f"与生产重合 {r['与生产重合']}/5", flush=True)
        g1, g2 = recs[0], recs[1]
        if "picks" in g1 and "picks" in g2:
            print("    G1 vs G2 逐位相同 = " + str(g1["picks"] == g2["picks"])
                  + "（同进程连打只是自证；跨进程可复现要另开一次进程再比）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
