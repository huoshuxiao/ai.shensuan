# -*- coding: utf-8 -*-
"""换 4b + 开思考的**行为冒烟**：五行对照，每行只动一个旋钮，只读生产、只写本目录。

用户的问题：把本地 LLM 换成 `qwen3.5:4b` 并**打开思考**，两条线一起换（改 `v1/.env` 并同步
`v1/data/results/rdagent_output/.env`）。这一步**不改任何配置文件**：
`common/src/config_base.py:71-74` 的 `load_dotenv` 是 `override=False`，进程环境变量优先于
`.env` ⇒ 开关可以在这一跑里临时拨，量完再决定要不要落盘（参数类决策先给实测代价）。

为什么"开思考"这件事必须先量：`STOCK_LLM_REASONING_EFFORT=none` 不是审美选择，是 09-27 用
一次真故障换来的闸门（无闸门 = 90s 读超时 × SDK 重试 = 271.5s 抛错、三次零正文；
`qwen3.5:9b` 不封顶时 >900s 不返回）。开思考 = 把那道闸门撤掉，所以要验的是撤掉之后
**这条 JSON 契约还在不在**。三个可能互相掩盖的因子必须拆开，否则读数没法归因：
  ① 思考本身（`reasoning_effort` 不传 = ollama 侧 qwen3.5 默认开）；
  ② `max_tokens=1024` 这道天花板（思考 token 也计入配额 ⇒ 可能被思考吃光而 `finish=length`、
     正文为空——09-27 记过「9b 带思考=正文为空，思考在独立字段」）；
  ③ `response_format={"type":"json_object"}`（本地推理框架在 JSON 约束下会强制语法解码器，
     它和思考可能打架）。
所以 R1/R2/R3 是 1024封顶 / 不封顶 / 不封顶且去掉 JSON 约束 的三级剥洋葱。

五行表（每行只比上一行多动一个东西）：
  R4  4b + 关思考 + 1024 + json  ← 换模型不换闸：这就是「4b 到底省下多少」的干净对照
  R1  4b + 开思考 + 1024 + json  ← 用户设想的那一档直接落地会怎样
  R2  4b + 开思考 + 无上限 + json ← 若 R1 是 `length` 截停，放开上限后能不能收敛
  R3  4b + 开思考 + 无上限 + 无 JSON 约束 ← 若 R2 仍废，是不是约束和思考打架
  R5  9b + 关思考 + 1024 + json  ← 现生产基准行（与 R4 同提示词 ⇒ 4b/9b 只差模型一个因子）

池子只从**归档 csv** 取（不装面板）：这一步问的是"能不能用"，不是"值不值 2.4 小时"。
顺带量到一件有用的事——60 场回放里一次调用中位 **141.9s**，而 09-27 独立探针是 33~38s，
差的那 3GB 面板就是嫌疑人；这一跑进程里没有面板，R5 那格的秒数就是「影子进程只读 csv」
的真实墙钟（那条链路能不能按 ~40s/场 预算，看这一格）。

尺子先验再花钱：`check_compliance`/`build_fixtures`/`detect_check` 直接从
`probe_llm_picks_0927.py` import（阈值来自生产 config、含「未知」行业豁免），
不抄第二份数字；自检 5/5 绿才调模型。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_4b_think_0928.py --detect-check   # 只验尺子
    /usr/bin/python3.10 stock/v1/temp/probe_llm_4b_think_0928.py                 # 五行实跑
产物：stock/v1/temp/tmp_llm_4b_think_0928/{smoke.csv, raw.jsonl, smoke.log}
"""
import json
import os
import sys
import time

import pandas as pd

# 三道闸门在本探针里**故意失效**：max_tokens=0、reasoning_effort="" ⇒
# `_apply_request_defaults` 的闭包不注入任何字段，请求体由下面每一行自己写。
# 必须在 import config 之前设（config_base 在导入时就把它读成模块常量）。
os.environ["STOCK_LLM_MAX_TOKENS"] = "0"
os.environ["STOCK_LLM_REASONING_EFFORT"] = ""

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "src"))
import _bootstrap  # noqa: F402,E402

from config import LLM_BASE_URL  # noqa: E402
from core.llm_client import describe_endpoint, make_openai_client  # noqa: E402
from llm_evidence_common import SYSTEM_ORDER  # noqa: E402
# 尺子从既有探针复用（同一把，不另造）：那几个函数只吃 rows(code→板块/行业) 与生产 5 只。
sys.path.insert(0, os.path.join(_ROOT, "stock", "v1", "temp"))
from probe_llm_picks_0927 import build_fixtures, check_compliance  # noqa: E402

SIG_DIR = os.path.join(_ROOT, "stock", "v1", "data", "results", "daily_signal")
OUT = os.path.join(_ROOT, "stock", "v1", "temp", "tmp_llm_4b_think_0928")
RAW_LOG = os.path.join(OUT, "raw.jsonl")
# 单次墙钟上限：冷加载实测 330.4s（9b 6.6GB），4b 3.4GB 约其一半；
# 900s = 9b 冷加载的 2.7 倍，够装完模型。思考不收敛时**这一格记成"超时"读数**，不是失败。
CALL_TIMEOUT = 900.0
THINK_ON = None      # 不传 reasoning_effort ⇒ ollama 侧 qwen3.5 默认开思考
THINK_OFF = "none"   # 实测唯一有效的关思考写法（/v1 兼容口下 think:false 无效）
ROWS = [
    dict(tag="R4 4b·关思考·1024·json", model="qwen3.5:4b", effort=THINK_OFF,
         max_tokens=1024, json_mode=True),
    dict(tag="R1 4b·开思考·1024·json", model="qwen3.5:4b", effort=THINK_ON,
         max_tokens=1024, json_mode=True),
    dict(tag="R2 4b·开思考·无上限·json", model="qwen3.5:4b", effort=THINK_ON,
         max_tokens=None, json_mode=True),
    dict(tag="R3 4b·开思考·无上限·无约束", model="qwen3.5:4b", effort=THINK_ON,
         max_tokens=None, json_mode=False),
    dict(tag="R5 9b·关思考·1024·json", model="qwen3.5:9b", effort=THINK_OFF,
         max_tokens=1024, json_mode=True),
]


def load_pool():
    """最近一场生产观察名单（归档 csv）+ 该场生产下单 5 只 = 基线"""
    buys = sorted(f for f in os.listdir(SIG_DIR) if f.startswith("buy_") and f.endswith(".csv"))
    orders = sorted(f for f in os.listdir(SIG_DIR) if f.startswith("order_") and f.endswith(".csv"))
    if not buys or not orders:
        raise SystemExit("❌ 找不到日频归档 ⇒ 池子无从取材")
    tag_b, tag_o = buys[-1][4:12], orders[-1][6:14]
    if tag_b != tag_o:
        raise SystemExit(f"❌ 观察名单 {tag_b} 场、下单名单 {tag_o} 场不是同一场 ⇒ 基线不可用")
    df = pd.read_csv(os.path.join(SIG_DIR, buys[-1]))
    det = pd.read_csv(os.path.join(SIG_DIR, orders[-1]))["code"].tolist()
    return tag_b, df, det


def pool_text(df):
    """名次|代码|简称|收盘价|板块|行业 —— 列与回放那套同源，但价格取归档那份。

    ⚠️ 口径差一句：归档 `close` 是**复权锚在上市首日**的收盘价（面板 $close），
    60 场回放的池子写的是 `raw_price`（真实成交价）。这一跑问的是「思考开不开这条
    JSON 契约还在不在」，票价数值不影响契约，所以认这个差；但要拿这一跑的**秒数**
    去和回放比时，记住提示词只差几个数字位数、不影响 token 量的量级。
    """
    lines = ["格式：名次|代码|简称|收盘价|板块|行业"]
    for _, r in df.sort_values("rank").iterrows():
        nm = str(r.get("name", "")).replace(" ", "")
        cl = "" if pd.isna(r["close"]) else "{:.2f}".format(r["close"])
        lines.append(f"{int(r['rank'])}|{r['code']}|{nm}|{cl}|{r['板块']}|{r['行业']}")
    return "\n".join(lines)


def one_call(cl, spec, user_prompt, rows, det):
    """一次调用 → 一行读数。抛错也记读数（超时/500 本身就是这一档的账单）。"""
    kw = dict(model=spec["model"],
              messages=[{"role": "system", "content": SYSTEM_ORDER},
                        {"role": "user", "content": user_prompt}],
              temperature=0.0)
    if spec["effort"]:
        kw["reasoning_effort"] = spec["effort"]
    if spec["max_tokens"]:
        kw["max_tokens"] = spec["max_tokens"]
    if spec["json_mode"]:
        kw["response_format"] = {"type": "json_object"}
    t0 = time.time()
    rec = {"档": spec["tag"], "模型": spec["model"],
           "思考": "开" if spec["effort"] is None else "关",
           "封顶": spec["max_tokens"] or "无",
           "JSON约束": "有" if spec["json_mode"] else "无",
           "提示词字数": len(SYSTEM_ORDER) + len(user_prompt)}
    try:
        resp = cl.chat.completions.create(**kw)
    except Exception as e:
        rec["秒"] = round(time.time() - t0, 1)
        rec["错误"] = f"{type(e).__name__}: {str(e)[:200]}"
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        _append_raw(dict(rec))
        return rec
    msg = resp.choices[0].message
    dump = msg.model_dump()
    # 思考落在哪：兼容口下它可能进独立字段（09-27 记过 9b「正文为空、思考在独立字段」），
    # 字段名不统一 ⇒ 把除 role/content 外所有非空的附加字段量一遍（长度，不是原文），别猜。
    extras = {}
    for k, v in dump.items():
        if k in ("role", "content") or not v:
            continue
        extras[k] = len(str(v))
    usage = resp.usage
    ctok = getattr(usage, "completion_tokens", None)
    d = getattr(usage, "completion_tokens_details", None)
    if d is not None:
        rtok = d.get("reasoning_tokens") if isinstance(d, dict) else \
            getattr(d, "reasoning_tokens", None)
    else:
        rtok = None
    content = msg.content or ""
    rec.update({"秒": round(time.time() - t0, 1),
                "finish": resp.choices[0].finish_reason,
                "出词": ctok, "思考词": rtok,
                "正文字符": len(content),
                "附加字段": extras,
                "原文": content})
    try:
        obj = json.loads(content)
        picks = obj.get("picks", [])
        codes = [str(p.get("code", "")).upper() for p in picks]
        rec["可解析"] = True
    except Exception as e:
        rec["可解析"] = f"否({type(e).__name__})"
        print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                         ensure_ascii=False), flush=True)
        _append_raw(rec)
        return rec
    outside, board_over, ind_dup = check_compliance(codes, rows)
    rec.update({"条数": len(codes), "池外": len(outside), "板块超限": len(board_over),
                "行业重复": len(ind_dup),
                "与生产重合": len(set(codes) & set(det)),
                "picks": codes})
    print(json.dumps({k: v for k, v in rec.items() if k != "原文"},
                     ensure_ascii=False), flush=True)
    _append_raw(rec)
    return rec


def _append_raw(rec):
    os.makedirs(OUT, exist_ok=True)
    with open(RAW_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in rec.items() if k != "提示词字数"},
                           ensure_ascii=False) + "\n")


def main():
    tag, df, det = load_pool()
    rows = df.set_index("code")
    print(f"场次 {tag}｜池子 {len(df)} 只｜生产下单 5 只={det}", flush=True)
    print(f"端点 {LLM_BASE_URL} | {describe_endpoint()}", flush=True)
    print("本探针刻意让三道闸门失效（进程环境变量优先于 .env）："
          "STOCK_LLM_MAX_TOKENS=0 / STOCK_LLM_REASONING_EFFORT='' ⇒ 请求体每行自己写\n",
          flush=True)

    fixtures = build_fixtures(df, rows, det)
    bad = []
    for name, codes, want in fixtures:
        if codes is None:
            print(f"[尺子自检] {name} ❌ ⇒ 夹具造不出来，不去花钱", flush=True)
            return 1
        got = [list(x) for x in check_compliance(codes, rows)]
        ok = got == [list(w) for w in want]
        print(f"[尺子自检] {name}: 读数={got} 期望={[list(w) for w in want]} "
              f"{'✅' if ok else '❌'}", flush=True)
        if not ok:
            bad.append(name)
    if bad:
        print(f"❌ 尺子是脏的（{len(bad)}/{len(fixtures)} 不符）⇒ 不调模型", flush=True)
        return 1
    if "--detect-check" in sys.argv:
        print(f"✅ 尺子 {len(fixtures)}/{len(fixtures)} 通过（--detect-check：没调模型）", flush=True)
        return 0

    user_prompt = (f"场次 {tag}（收盘价截面，T+1 开盘买）。观察名单：\n{pool_text(df)}\n\n"
                   "请挑 5 只，按你想要的优先级从高到低排列。")
    cl = make_openai_client(max_retries=0, timeout=CALL_TIMEOUT, api_key="ollama")
    out = []
    for spec in ROWS:
        print(f"\n===== {spec['tag']} =====", flush=True)
        out.append(one_call(cl, spec, user_prompt, rows, det))
    pd.DataFrame(out).to_csv(os.path.join(OUT, "smoke.csv"),
                             index=False, encoding="utf-8-sig")

    print("\n===== 换 4b + 开思考 的行为账单 =====", flush=True)
    for r in out:
        if "错误" in r:
            print(f"{r['档']}: ❌ {r['秒']}s {r['错误'][:120]}", flush=True)
            continue
        tok = r["出词"] or 0
        print(f"{r['档']}: {r['秒']}s｜finish={r['finish']}｜出词 {tok}"
              f"（思考 {r['思考词']}）｜正文 {r['正文字符']} 字符｜"
              f"附加字段 {r['附加字段']}｜可解析={r['可解析']}", flush=True)
        # 「条数/池外/…」只在可解析那几行存在 ⇒ 缺键的行只报速度，别拿 KeyError 冒充结论
        if tok and r["秒"] > 0 and "条数" in r:
            print(f"    速度 {tok / r['秒']:.1f} tok/s｜条数 {r['条数']}/5｜池外 {r['池外']}"
                  f"｜破板块 {r['板块超限']}｜破行业 {r['行业重复']}"
                  f"｜与生产重合 {r['与生产重合']}/5", flush=True)
    print("\n判读（跑之前定死）：", flush=True)
    print("  ① R1/R2/R3 只要有一格「可解析」为真且池外=0、正文非空 ⇒ 思考模式在这条链路上"
          "技术可行；三格全废 ⇒ 开思考=拿不到名单，讨论到此为止。", flush=True)
    print("  ② R1 废而 R2 成 ⇒ 病根是 1024 天花板（思考 token 占配额），要动的是 MAX_TOKENS；"
          "R2 废而 R3 成 ⇒ 病根是 JSON 约束与思考打架，要动的是 response_format。", flush=True)
    print("  ③ 4b 值不值：R4 vs R5 同提示词，只差模型 ⇒ 秒数之比就是省下的墙钟；"
          "名单质量之比看这两行的「与生产重合」。", flush=True)
    print(f"  ④ 影子进程预算：R5 那一格的秒数就是「只读归档 csv、不装面板」的真实单场成本"
          f"（60 场回放里是 141.9s/场，那里面有 3GB 面板）。", flush=True)
    print(f"（产物：smoke.csv / raw.jsonl @ {OUT}）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
