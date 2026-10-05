# -*- coding: utf-8 -*-
"""查「V3.md 请求」是否有真实用户消息来源；顺带复核当前会话行 601/606。
只读：~/.qoder-cn/projects/*/*.jsonl（本机全部 Qoder 会话转写）。
判据分层：真实用户消息(type=user,role=user,非tool_result,非compaction) / compaction / 其他。
"""
import glob, json, os

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".qoder-cn", "projects",
                    "-home-sunwenkun-Developer-agent-workspace-ai-shensuan-git")
CUR = os.path.join(PROJ, "e8a76879-f353-4ce9-a700-3fe7b556f882.jsonl")
PH = ["V1.md", "V2.md", "V3.md", "用户包", "我记得你第一次建的", "对的找回来"]


def user_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text") or "" for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_tool_result(msg):
    c = msg.get("content")
    return isinstance(c, list) and bool(c) and all(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def scan_file(path):
    n, umsgs, uh, ch, other = 0, [], [], 0, 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f, 1):
            n = i
            o = None
            if '"user"' in ln:
                try:
                    o = json.loads(ln)
                except Exception:
                    o = None
            is_user = (isinstance(o, dict) and o.get("type") == "user"
                       and (o.get("message") or {}).get("role") == "user"
                       and not is_tool_result(o.get("message") or {}))
            txt = ts = ""
            is_compact = False
            if is_user:
                txt = user_text(o.get("message") or {})
                ts = str(o.get("timestamp"))[:19]
                is_compact = ("compactMetadata" in o) or txt.startswith("This session is being continued")
                if not is_compact:
                    umsgs.append((i, ts, txt.strip().replace("\n", " ")[:90]))
            hits = [p for p in PH if p in ln]
            if hits:
                if is_user and not is_compact:
                    uh.append((i, ts, hits, txt.strip().replace("\n", " ")[:220]))
                elif is_user and is_compact:
                    ch += 1
                else:
                    other += 1
    return n, umsgs, uh, ch, other


files = sorted(glob.glob(os.path.join(HOME, ".qoder-cn", "projects", "*", "*.jsonl")))
print("== 全部会话文件 ==")
for f in files:
    print("  %12d B  %s" % (os.path.getsize(f), os.path.basename(f)))

print("\n== 逐文件：用户消息量 / 短语命中分层 ==")
tot_uh = 0
for f in files:
    n, umsgs, uh, ch, other = scan_file(f)
    tspan = (umsgs[0][1] + " ~ " + umsgs[-1][1]) if umsgs else ""
    print("\n-- %s lines=%d user_msgs=%d span=%s" % (os.path.basename(f), n, len(umsgs), tspan))
    print("   短语命中: 真实用户消息=%d  compaction=%d  其他=%d" % (len(uh), ch, other))
    for h in uh[:10]:
        print("   !!! L%d %s %s :: %s" % (h[0], h[1], h[2], h[3]))
    for m in umsgs[-3:]:
        print("   last-user L%d %s :: %s" % m)
    tot_uh += len(uh)
print("\n== 真实用户消息短语命中合计: %d ==" % tot_uh)

lines = open(CUR, encoding="utf-8", errors="replace").read().splitlines()
print("\n== 当前会话 e8a76879: 共 %d 行 ==" % len(lines))
for i in (601, 606):
    try:
        o = json.loads(lines[i - 1])
    except Exception as e:
        print("  L%d parse-fail %s" % (i, e))
        continue
    t = o.get("type")
    msg = o.get("message") or {}
    c = msg.get("content")
    ts = str(o.get("timestamp"))[:19]
    print("  L%d ts=%s type=%s role=%s" % (i, ts, t, msg.get("role", "")))
    if isinstance(c, list):
        for j, b in enumerate(c):
            if not isinstance(b, dict):
                continue
            k = b.get("type")
            if k == "thinking":
                print("     block%d thinking len=%d head=%r" % (j, len(b.get("thinking") or ""), (b.get("thinking") or "")[:50]))
            elif k == "text":
                print("     block%d text len=%d head=%r" % (j, len(b.get("text") or ""), (b.get("text") or "")[:50]))
            else:
                print("     block%d %s name=%s" % (j, k, b.get("name", "")))
    if t == "attachment":
        print("     attachment: %s" % str(o.get("attachment"))[:160])
