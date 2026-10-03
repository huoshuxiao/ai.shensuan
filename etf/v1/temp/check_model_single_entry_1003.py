# -*- coding: utf-8 -*-
"""甲（10-03）：official 支线的模型收进一个入口 + 体检表一行念出两个模型。

背景（用户问「跑一次为什么会起两个本地模型」）：主线侧模型在 config 的
`LLM_MODEL`，official 侧模型写在各线 `rdagent_output/.env` 的 `LITELLM_CHAT_MODEL`，
两份配置各说各话，而多源挖掘里两支是并发跑的 ⇒ 同一台机同时驻留两个聊天模型。
甲只统一**入口与读数**，不改选型（默认留空＝行为与改前一字不差）。
**10-03 晚用户裁「一」＝本线把默认写成 9b**（`etf/v1/.env` 的 `ETF_RDAGENT_LLM_MODEL`，
该文件不进版本库 ⇒ 这条默认只活在本机），所以本夹具的"默认档"一臂从「留空」换成「9b」，
而「留空＝不注入」那条机制改由显式清空键的子进程臂守着（机制没删，只是不再是本线默认）。

判据形状（默认档一臂 + 留空一臂 + 两条负对照 + 一条真入口）：
  D  本线默认档（10-03 用户裁「一」之后）＝ `.env` 里那行 9b：
     D1 键确实读到了值，D2 **值必须带 litellm 的 provider 前缀**（裸名那场实测
        第一次调用就 10 连败退出，10-03 10:58 白烧 4 个 trial ⇒ 这条是前缀的牙），
     D3 父进程真把 `LITELLM_CHAT_MODEL` 装进子进程环境，D4 来源如实写成「注入」。
  B  留空＝不注入（把键显式设成空串的子进程才测得出来，同进程改环境是假臂）：
     B1 子进程环境里**不许**出现 LITELLM_CHAT_MODEL 键，B2/B3 这时报的才是
     工作区 .env 里那个模型、来源如实写成「工作区」。
  N 负对照：留空 + 指向不存在的 .env ⇒ 必须报「没设」，不许折成空串糊过去。
     （这一条在 10-03 之前是进程内测的；默认档设值之后进程内**永远**是注入优先，
      再在进程内测它就变成恒假 ⇒ 挪进留空子进程臂。）
  A2 注入＝压过 .env：注一个与默认档**不同**的值，验它原样落到子进程环境。
     （这里的值故意用裸名——这一臂测的是"透传"不是"可用性"，真跑批的值见 .env 注释。）
  A4 真入口：`rdagent_preflight()` 打出来的那一行必须**同时**含两个模型名
     （能失败的地方在这一步：只改了函数没接线，前两臂全绿而日志里看不见）。
  A5 负对照：把旋钮注成一个假名走真入口，那一行必须跟着换成假名
     ——函数对、默认档念得出来，都证不了"旋钮真的接在体检表上"，只有这一条证。

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


def workspace_env_model(env_file=ENV_FILE):
    """独立 oracle：自己读工作区那份 .env 的 LITELLM_CHAT_MODEL，不走生产代码。

    写成解析而不是把 `ollama_chat/qwen2.5:7b` 钉死在源码里——那份 .env 由 RD-Agent
    工作区自己维护，钉死值＝迟早有一天红的是夹具、不是判据。
    """
    if not os.path.exists(env_file):
        return ""
    for line in open(env_file, encoding="utf-8"):
        line = line.strip()
        if line.startswith("LITELLM_CHAT_MODEL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


WORKSPACE_MODEL = workspace_env_model()


def arm_default():
    """D：本线默认档（10-03 裁「一」之后＝`.env` 那行 9b）"""
    sys.path.insert(0, os.path.join(ROOT, "etf/v1/src"))
    import _bootstrap  # noqa: F401  平铺导入的挂路
    import config
    import official_rdagent as o

    value = config.RDAGENT_LLM_MODEL
    say(value.endswith("qwen3.5:9b"),
        "D1 本线默认档读到值（10-03 裁「一」＝9b 成默认）", f"实得 {value!r}")
    say("/" in value,
        "D2 值带 litellm 的 provider 前缀（裸名＝官方支第一次调用就 10 连败退出）",
        f"实得 {value!r}")
    env = o._driver_env()
    say(env.get("LITELLM_CHAT_MODEL") == value,
        "D3 父进程真把它装进子进程环境（默认档不是只写在纸面上）",
        f"实得 {env.get('LITELLM_CHAT_MODEL')!r}")
    model, src = o.official_chat_model(ENV_FILE)
    say(src.startswith("注入 "), "D4 来源如实写成「注入」", f"实得 {src!r}")
    # 现状读数（不是断言）：主线与 official 是否同一个模型＝「一场起几个模型」的实证
    same = model.split("/")[-1] == config.LLM_MODEL
    print(f"     读数：主线={config.LLM_MODEL}｜official={model}｜"
          f"{'同一个 ⇒ 这场只起一个大模型' if same else '两个不同 ⇒ 这场会同时起两个'}")
    return model, config.LLM_MODEL


def arm_blank():
    """B + N：把键显式设成空串的子进程＝回到「留空＝不注入」的历史语义"""
    code = (
        "import sys;"
        f"sys.path.insert(0,'{ROOT}/etf/v1/src');"
        "import _bootstrap, official_rdagent as o;"
        "e=o._driver_env();"
        f"m,s=o.official_chat_model('{ENV_FILE}');"
        "n,_=o.official_chat_model('/nonexistent_dir_1003/.env');"
        "print(e.get('LITELLM_CHAT_MODEL','<缺>'));print(m);print(s);print(n)"
    )
    p = subprocess.run([sys.executable, "-c", code],
                       env={**os.environ, "ETF_RDAGENT_LLM_MODEL": ""},
                       capture_output=True, text=True, timeout=180)
    lines = p.stdout.strip().splitlines()
    say(p.returncode == 0 and len(lines) >= 4,
        "B0 留空臂的子进程正常退出", f"rc={p.returncode} {p.stderr[-200:]}")
    if len(lines) >= 4:
        say(lines[0] == "<缺>",
            "B1 留空时子进程环境里不许多出那个键（机制没删，只是不再是本线默认）",
            f"实得 {lines[0]!r}")
        say(lines[1] == WORKSPACE_MODEL,
            "B2 这时报的才是工作区那份 .env 里的模型（与独立 oracle 对表）",
            f"实得 {lines[1]!r} vs {WORKSPACE_MODEL!r}")
        say(lines[2].startswith("工作区 "),
            "B3 来源如实写成「工作区 .env」", f"实得 {lines[2]!r}")
        say("没设" in lines[3],
            "N 留空＋文件不存在 ⇒ 如实报「没设」（不折成空串糊过去）",
            f"实得 {lines[3]!r}")


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

    这一条才是「接线证明」：D/B 两臂只证明函数本身对，A4 只证明默认档念得出来；
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
    print(f"     工作区那份 .env 解析到的模型（独立 oracle）：{WORKSPACE_MODEL!r}")
    official_model, main_model = arm_default()
    arm_blank()
    arm_inject()
    arm_preflight(official_model, main_model)
    arm_preflight_inject()
    print(f"合计 通过 {ok}，不通过 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
