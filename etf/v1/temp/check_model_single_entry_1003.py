# -*- coding: utf-8 -*-
"""甲（10-03）：official 支线的模型收进一个入口 + 体检表一行念出两个模型。

背景（用户问「跑一次为什么会起两个本地模型」）：主线侧模型在 config 的
`LLM_MODEL`，official 侧模型写在各线 `rdagent_output/.env` 的 `LITELLM_CHAT_MODEL`，
两份配置各说各话，而多源挖掘里两支是并发跑的 ⇒ 同一台机同时驻留两个聊天模型。
甲只统一**入口与读数**，不改选型（默认留空＝行为与改前一字不差）。

判据形状（三条臂 + 一条真入口，每条都可能失败）：
  A1 留空＝不注入：`_driver_env()` 里**不许**出现 LITELLM_CHAT_MODEL 键，
     且 `official_chat_model()` 报的是工作区 .env 里那个值（不是空、不是默认）。
  A2 注入＝压过 .env：起一个带 `ETF_RDAGENT_LLM_MODEL` 的**子进程**才测得出来
     （`_ENV_DRIVER` 在 import 时求值，同进程里改环境变量是假臂）。
  A3 负对照：两份都没设时必须报「没设」而不是回空串糊过去。
  A4 真入口：`rdagent_preflight()` 打出来的那一行必须**同时**含两个模型名
     （能失败的地方在这一步：只改了函数没接线，A1-A3 全绿而日志里看不见）。
  A5 负对照：把旋钮注成一个假名走真入口，那一行必须跟着换成假名
     ——A1-A3 证函数、A4 证默认档念得出来，只有 A5 证「旋钮真的接在体检表上」。

用法：/usr/bin/python3.10 etf/v1/temp/check_model_single_entry_1003.py
"""
import os
import subprocess
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
ENV_FILE = os.path.join(ROOT, "etf/v1/data/results/rdagent_output/.env")

ok = 0
fail = 0


def say(cond, label, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {label}")
    else:
        fail += 1
        print(f"  ✗ {label}  {detail}")
    return cond


def arm_empty():
    """A1 + A3：在「留空」的世界里跑（进程内不设注入值）"""
    sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
    import _bootstrap  # noqa: F401  平铺导入的挂路
    import config
    import official_rdagent as o

    say(config.RDAGENT_LLM_MODEL == "",
        "A1a 本线默认档留空（不注入＝选型一字未改）",
        f"实得 {config.RDAGENT_LLM_MODEL!r}")
    env = o._driver_env()
    say("LITELLM_CHAT_MODEL" not in env,
        "A1b 留空时子进程环境里不许多出那个键（否则默认档就被改了）",
        f"实得 {env.get('LITELLM_CHAT_MODEL')!r}")
    model, src = o.official_chat_model(ENV_FILE)
    say(model.startswith("ollama_chat/"),
        "A1c 报的是工作区 .env 里那个模型", f"实得 {model!r} / {src!r}")
    say(src.startswith("工作区 "),
        "A1d 来源如实写成「工作区 .env」", f"实得 {src!r}")
    # A3 负对照：文件不存在时必须出声，不许折成空串/默认值
    model2, src2 = o.official_chat_model(os.path.join(ROOT, "nope/.env"))
    say("没设" in model2,
        "A3 两份都没设 ⇒ 如实报「没设」（不是静默回空串）", f"实得 {model2!r}")
    return model, config.LLM_MODEL


def arm_inject():
    """A2：真子进程带 env 前缀键，验它压过工作区 .env"""
    code = (
        "import sys;"
        f"sys.path.insert(0,'{ROOT}/etf/v1/src');"
        "import _bootstrap, official_rdagent as o;"
        "e=o._driver_env();"
        "m,s=o.official_chat_model("
        f"'{ENV_FILE}');"
        "print(e.get('LITELLM_CHAT_MODEL','<缺>'));print(s)"
    )
    p = subprocess.run([sys.executable, "-c", code],
                       env={**os.environ, "ETF_RDAGENT_LLM_MODEL": "qwen3.5:9b"},
                       capture_output=True, text=True, timeout=180)
    lines = p.stdout.strip().splitlines()
    say(p.returncode == 0 and len(lines) >= 2,
        "A2a 注入臂的子进程正常退出", f"rc={p.returncode} {p.stderr[-200:]}")
    if len(lines) >= 2:
        say(lines[0] == "qwen3.5:9b",
            "A2b 注入值真进了子进程环境（压过 .env 那一份）",
            f"实得 {lines[0]!r}")
        say(lines[1].startswith("注入 "),
            "A2c 来源如实写成「注入」而不是「工作区」", f"实得 {lines[1]!r}")


def arm_preflight(official_model, main_model):
    """A4：真入口那一行是否把两个模型一起念出来（慢在 docker 探测）"""
    sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
    import _bootstrap  # noqa: F401
    import official_rdagent as o
    checks = o.rdagent_preflight()
    row = [c for c in checks if c[0] == "LLM 端点"]
    if not say(len(row) == 1, "A4a 体检表里有「LLM 端点」那一行",
               f"实得 {len(row)} 行"):
        return
    detail = row[0][2]
    print(f"     体检表原文: {detail}")
    say(official_model.split("/")[-1] in detail,
        "A4b official 侧模型名出现在那一行里", f"缺 {official_model!r}")
    say(main_model in detail,
        "A4c 主线侧模型名也在同一行 ⇒ 一行念完两个模型", f"缺 {main_model!r}")
    say(row[0][1] is True, "A4d 加读数没有把这一腿判成 ❌（不设新闸、不改判决）",
        f"实得 ok={row[0][1]}")
    # 现状读数（不是断言）：两个模型名是否真的不同 = 「起两个」这件事的实证
    same = official_model.split("/")[-1] == main_model
    print(f"     读数：主线={main_model}｜official={official_model}"
          f"｜{'同一个' if same else '两个不同 ⇒ 这场会同时起两个本地模型'}")


def arm_preflight_inject():
    """A5 负对照（走真入口）：把旋钮注成一个假名，体检表那一行必须跟着变。

    这一条才是「接线证明」：A1-A3 只证明函数本身对，A4 只证明默认档念得出来；
    若只改了函数没接进 `rdagent_preflight`，前四条全绿而这里必红。
    """
    marker = "MARKER-1003-9b"
    code = (
        "import sys;"
        f"sys.path.insert(0,'{ROOT}/etf/v1/src');"
        "import _bootstrap, official_rdagent as o;"
        "row=[c for c in o.rdagent_preflight() if c[0]=='LLM 端点'][0];"
        "print(row[2])"
    )
    p = subprocess.run([sys.executable, "-c", code],
                       env={**os.environ, "ETF_RDAGENT_LLM_MODEL": marker},
                       capture_output=True, text=True, timeout=300)
    line = p.stdout.strip()
    print(f"     注入假名后的体检表原文: {line}")
    say(marker in line,
        "A5 注进真入口 ⇒ 体检表那一行跟着换成注入值（没接线就抓不到）",
        f"rc={p.returncode} 行内无 {marker!r}")


def main():
    print("== 甲：模型入口统一 + 体检表一行念两个模型（10-03）==")
    official_model, main_model = arm_empty()
    arm_inject()
    arm_preflight(official_model, main_model)
    arm_preflight_inject()
    print(f"合计 通过 {ok}，不通过 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
