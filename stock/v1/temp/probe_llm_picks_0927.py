# -*- coding: utf-8 -*-
"""让 9b 真的出一次下单名单，量"接进判据"的四张账单——**只读生产、只写 stdout**。

为什么要先量再写代码：股票线的 5 只下单名单是**手工实盘的输入**，一旦让 LLM 参与，
它就从一个"每次都能复现的算式"变成"今天说 A 明天说 B 的意见"。这不是好坏问题，
是**能不能事后复盘**的问题：赚了两笔，是判据对还是模型蒙对？所以四件事必须先有数：

  ① 幻觉率：给的池子是当日真实那 50 只，它挑的 5 个代码全在池内吗（编代码=直接废）。
  ② 硬闸顺从度：现有下单层有「同板块 ≤ASHARE_ORDER_MAX_PER_BOARD、
     同行业 ≤ASHARE_ORDER_MAX_PER_INDUSTRY（行业未知豁免）」两道限幅。提示词里写了
     约束，它守不守？
  ③ 墙钟：这台机器实测约 3.1 字/秒（09-27 那笔 33 token/10.5s）。50 行进 5 条出，
     一次到底等多久、有没有撞上 max_tokens 的天花板（撞上=会被截断成半截名单）。
  ④ **可复现性**：同一条请求 `temperature=0` 连打两次，两次名单是否逐位相同。
     这条最要命——不相同就意味着归档表不可复现，接进判据前必须先解决。

调用走**正向链路同一个函数** `make_openai_client()`（三道 .env 闸门自动注入），
请求体照共享层 20 个入口的共同形态（`response_format=json_object`）。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_picks_0927.py --detect-check   # 只验尺子，不花 CPU
    /usr/bin/python3.10 stock/v1/temp/probe_llm_picks_0927.py                  # 验完尺子才调模型
"""
import json
import os
import sys
import time
from glob import glob

import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "stock", "v1", "src")))
import _bootstrap  # noqa: F402,E402
from config import (ASHARE_ORDER_MAX_PER_BOARD, ASHARE_ORDER_MAX_PER_INDUSTRY,  # noqa: E402
                    LLM_BASE_URL, LLM_MODEL)
# 阈值与「行业未知」豁免都从生产侧 import，不在探针里抄第二份数字：
# 抄一份 = 改了生产限幅后探针还按老尺子判"顺从"，那是最客气的一种假绿灯。
from strategy.ashare_screen import INDUSTRY_UNKNOWN  # noqa: E402
from core.llm_client import describe_endpoint, make_openai_client  # noqa: E402

SIG_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
    "stock", "v1", "data", "results", "daily_signal"))
FAKE_CODE = "SZ999999"
RAW_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "probe_llm_picks_0927_raw.json")
SYSTEM = (
    "你是 A 股日线量化辅助系统的下单层。只回 JSON，不要任何解释文字。\n"
    "任务：从给定的观察名单里挑 5 只明天开盘买入。\n"
    f"硬约束（必须满足）：同一板块最多 {ASHARE_ORDER_MAX_PER_BOARD} 只；"
    f"同一行业最多 {ASHARE_ORDER_MAX_PER_INDUSTRY} 只（行业写「{INDUSTRY_UNKNOWN}」的不受"
    "此限，因为北交所与科创板大部分没有行业映射）；只能从给定的名单里选，"
    "不许出现名单外的代码。\n"
    '输出格式：{"picks":[{"code":"SZ000001","reason":"不超过20字"}]}')


def check_compliance(codes, rows):
    """照抄生产下单层的两道限幅（strategy/ashare_screen.py:1074-1075, 1108-1111）：
        板块数[板块_i] < ASHARE_ORDER_MAX_PER_BOARD
        ∧ (行业_i = 未知 ∨ 行业数[行业_i] < ASHARE_ORDER_MAX_PER_INDUSTRY)
    「未知」豁免是刻意的：外部行业映射对北交所缺 85%、科创板缺 96%，把「未知」当一个
    行业去重等于变相拉黑那两段（用户口径：三段都有权限，约束只能是分散度）。
    返回 (池外代码, 板块超限, 行业重复)；三组全空 = 完全顺从。
    """
    in_pool = set(rows.index)
    outside = [c for c in codes if c not in in_pool]
    known = [c for c in codes if c in in_pool]
    boards = [rows.at[c, "板块"] for c in known]
    inds = [rows.at[c, "行业"] for c in known]
    board_over = sorted({b for b in boards
                         if boards.count(b) > ASHARE_ORDER_MAX_PER_BOARD})
    ind_dup = sorted({i for i in inds if i != INDUSTRY_UNKNOWN
                      and inds.count(i) > ASHARE_ORDER_MAX_PER_INDUSTRY})
    return outside, board_over, ind_dup


# ===== 探测器自检：修尺子优先于花钱 =====
# 三根尺子各配一个负对照（只弄坏它那一格），外加两条"必须全绿"的正对照：
#   基线 = 生产下单层当天真实那 5 只（它红了说明尺子把合规判成违规，四张账单全是废纸）；
#   正对照 D = 两只「未知」行业（它红了说明豁免被误判，反之若「行业重复」从未触发过，
#              就分不清是修好了还是探测器压根坏了）。
# 夹具从**当轮池子现造**，不写死 09-24 的代码——池子每天在换，写死会让自检在下一场变假红。

def _fits(codes, rows, cand, allow_board=None, allow_ind=None):
    """加一只候选后是否仍然合规。allow_board/allow_ind 指名「唯一被允许超限的那一块」，
    只用来造负对照；其余任何一格超限都算不合适。"""
    out, bo, idup = check_compliance(list(codes) + [cand], rows)
    if out:
        return False
    if set(bo) - ({allow_board} if allow_board else set()):
        return False
    if set(idup) - ({allow_ind} if allow_ind else set()):
        return False
    return True


def _fill(codes, rows, pool, need, allow_board=None, allow_ind=None):
    """按代码序补齐到 need 只（除指定那一格外全程合规）；补不满返回 None。"""
    out = list(codes)
    for c in pool:
        if len(out) >= need:
            break
        if c in out:
            continue
        if _fits(out, rows, c, allow_board, allow_ind):
            out.append(c)
    if len(out) != need:
        return None
    return out


def build_fixtures(truth, rows, det):
    fx = [("基线=生产真实 5 只（必须全绿）", list(det), ([], [], []))]
    pool = truth["code"].tolist()

    # 负对照A：基线前 4 只 + 1 个池外假代码 ⇒ 只许「池外」红。
    fx.append(("负对照A 掺 1 个池外代码", list(det[:4]) + [FAKE_CODE],
               ([FAKE_CODE], [], [])))

    # 负对照B：同板块凑 (限幅+1) 只，行业彼此不撞 ⇒ 只许「板块」红。
    b_trip = None
    lim_b = ASHARE_ORDER_MAX_PER_BOARD + 1
    for b in sorted({rows.at[c, "板块"] for c in pool}):
        members = [c for c in pool if rows.at[c, "板块"] == b]
        if len(members) < lim_b:
            continue
        picked = []
        for c in members:  # 收同板块，但只放开这一块板块超限（行业重复仍然不要）
            if _fits(picked, rows, c, allow_board=b):
                picked.append(c)
            if len(picked) == lim_b:
                break
        if len(picked) < lim_b:
            continue
        five = _fill(picked, rows, pool, 5, allow_board=b)
        if five is not None:
            b_trip = (f"负对照B 同板块 {lim_b} 只（{b}）", five, ([], [b], []))
            break
    fx.append(b_trip or ("负对照B：池子里造不出「只超板块」的样本", None, None))

    # 负对照C：同一非「未知」行业凑 2 只，板块不堆 ⇒ 只许「行业」红。
    c_trip = None
    lim_i = ASHARE_ORDER_MAX_PER_INDUSTRY + 1
    named = {g: [c for c in pool if rows.at[c, "行业"] == g]
             for g in sorted({rows.at[c, "行业"] for c in pool} - {INDUSTRY_UNKNOWN})}
    for g, members in named.items():
        if len(members) < lim_i:
            continue
        five = _fill(members[:lim_i], rows, pool, 5, allow_ind=g)
        if five is not None:
            c_trip = (f"负对照C 同行业 {lim_i} 只（{g}）", five, ([], [], [g]))
            break
    fx.append(c_trip or ("负对照C：池子里造不出「只超行业」的样本", None, None))

    # 正对照D：两只「未知」行业 + 补足 3 只 ⇒ 三格都不许红（守的就是豁免本身）。
    unk = [c for c in pool if rows.at[c, "行业"] == INDUSTRY_UNKNOWN]
    d_ok = None
    if len(unk) >= 2:
        five = _fill(unk[:2], rows, pool, 5)
        if five is not None:
            d_ok = (f"正对照D 「{INDUSTRY_UNKNOWN}」行业 2 只应被豁免", five, ([], [], []))
    fx.append(d_ok or (f"正对照D：池子里造不出「未知×2 且其余全绿」的样本", None, None))
    return fx


def detect_check(truth, rows, det):
    fixtures = build_fixtures(truth, rows, det)
    bad = []
    for name, codes, want in fixtures:
        if codes is None:
            print(f"[探测器自检] {name} ❌ ⇒ 尺子缺一个对照，不去花 CPU", flush=True)
            return 1
        got = [list(x) for x in check_compliance(codes, rows)]
        exp = [list(w) for w in want]
        ok = got == exp
        print(f"[探测器自检] {name}: 读数={got} 期望={exp} {'✅' if ok else '❌'}",
              flush=True)
        if not ok:
            bad.append(name)
    if bad:
        print(f"❌ 尺子是脏的（{len(bad)}/{len(fixtures)} 个夹具不符）⇒ 不去花 CPU：{bad}",
              flush=True)
        return 1
    print(f"✅ 探测器自检 {len(fixtures)}/{len(fixtures)} 通过：两条正对照全绿、"
          "三根尺子各由自己的负对照点亮", flush=True)
    return 0


def build_pool(csv_path):
    """把当日观察名单压成提示词里的池子（一行一只，只给判据里真有的列）"""
    df = pd.read_csv(csv_path)
    lines = [f"共 {len(df)} 只，格式：code|板块|行业|安静度分位(越低越安静)|当日涨幅|均额亿"]
    for _, r in df.iterrows():
        lines.append(f"{r['code']}|{r['板块']}|{r['行业']}|{r['安静度分位']:.4f}|"
                     f"{r['当日涨幅']:.4f}|{r['amount20_yi']:.3f}")
    return "\n".join(lines), df


def _append_raw(rec):
    """把模型原话追加进 raw 档：`--against-library` 要靠它跟历史答案对表（跨进程复现）。"""
    hist = []
    if os.path.exists(RAW_LOG):
        try:
            with open(RAW_LOG, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = []
    hist.append(rec)
    with open(RAW_LOG, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)


def one_call(client, user_prompt, label, rows, det=None):
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": user_prompt}],
            temperature=0.0,
            response_format={"type": "json_object"})
    except Exception as e:
        print(f"{label}: 抛错 {type(e).__name__}: {str(e)[:180]} "
              f"({round(time.time() - t0, 1)}s)", flush=True)
        return None
    dt = round(time.time() - t0, 1)
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "completion_tokens", None) or \
        getattr(resp.usage, "completion_tokens", None)
    out = {"档": label, "秒": dt, "completion_tokens": usage,
           "finish": resp.choices[0].finish_reason, "字符": len(content)}
    try:
        picks = json.loads(content).get("picks", [])
    except Exception as e:
        out["可解析"] = f"否({type(e).__name__})"
        print(f"{label}: " + json.dumps(out, ensure_ascii=False), flush=True)
        return None
    codes = [p.get("code", "").upper() for p in picks]
    outside, board_over, ind_dup = check_compliance(codes, rows)
    reasons = {c: str(p.get("reason", "")) for c, p in zip(codes, picks)}
    out.update({"可解析": True, "条数": len(picks), "全在池内": not outside,
                "池外代码": outside, "板块超限": board_over, "行业重复": ind_dup,
                "picks": codes, "reasons": reasons})
    print(f"{label}: " + json.dumps(out, ensure_ascii=False), flush=True)
    _append_raw({"档": label, "秒": dt, "completion_tokens": usage, "finish": out["finish"],
                 "model": LLM_MODEL, "content": content, "picks": codes,
                 "det": list(det) if det else []})
    return out


def main():
    buys = sorted(glob(os.path.join(SIG_DIR, "buy_*.csv")))
    orders = sorted(glob(os.path.join(SIG_DIR, "order_*.csv")))
    if not buys or not orders:
        print("❌ 找不到日频产物 ⇒ 池子无从取材", flush=True)
        return 1
    csv_path = buys[-1]
    tag = os.path.basename(csv_path)[4:12]
    if os.path.basename(orders[-1])[6:14] != tag:
        print(f"❌ 观察名单是 {tag} 场、下单名单是 {os.path.basename(orders[-1])}，"
              "不是同一场 ⇒ 基线不可用", flush=True)
        return 1
    det = pd.read_csv(orders[-1])["code"].tolist()
    pool_text, truth = build_pool(csv_path)
    rows = truth.set_index("code")

    # 先验尺子再花钱：探测器脏 = 四张账单全是废纸，而这一跑 LLM 要等十几分钟。
    if detect_check(truth, rows, det) != 0:
        return 1
    if "--detect-check" in sys.argv:
        print("（--detect-check：只验尺子，没调模型）", flush=True)
        return 0

    user_prompt = (f"场次 {tag}（收盘价截面，T+1 开盘买）。观察名单：\n{pool_text}\n\n"
                   f"请挑 5 只，按你想要的优先级从高到低排列。")
    print(f"\n端点 {LLM_BASE_URL} | {describe_endpoint()}", flush=True)
    print(f"池子={csv_path} 共 {len(truth)} 只 | 确定性那场下单 5 只={det}", flush=True)
    print(f"提示词字数={len(user_prompt)}（中文约 1 字≈1 token 以内，这是输入 token 的保守上界）",
          flush=True)

    client = make_openai_client(api_key=os.environ.get("OPENAI_API_KEY") or "local-llm")
    a = one_call(client, user_prompt, f"① 第 {tag} 场·第 1 打", rows, det)
    b = one_call(client, user_prompt, "② 同一条请求·第 2 打", rows, det)

    print("\n===== 四张账单 =====", flush=True)
    if a is None or b is None:
        print("❌ 有一打没拿到可解析结果 ⇒ 上面那条错误就是账单", flush=True)
        return 1
    if len(a["picks"]) != 5 or len(b["picks"]) != 5:
        print(f"⚠️ 条数不是 5（{len(a['picks'])}/{len(b['picks'])}）⇒ 下面三张账单按实际条数读",
              flush=True)
    print(f"① 幻觉：第1打池外代码 {a['池外代码'] or '无'}；第2打 {b['池外代码'] or '无'}", flush=True)
    print(f"② 硬闸：第1打 板块超限 {a['板块超限'] or '无'} / 行业重复 {a['行业重复'] or '无'}；"
          f"第2打 板块超限 {b['板块超限'] or '无'} / 行业重复 {b['行业重复'] or '无'}", flush=True)
    print(f"③ 墙钟：第1打 {a['秒']}s（{a['completion_tokens']} token，含首次装载）；"
          f"第2打 {b['秒']}s（{b['completion_tokens']} token，热态）"
          f"｜finish={a['finish']}/{b['finish']}（length=被 max_tokens 截断）", flush=True)
    same = a["picks"] == b["picks"]
    print(f"④ 可复现：两次逐位相同 = {same}", flush=True)
    if not same:
        print(f"   第1打 {a['picks']}\n   第2打 {b['picks']}", flush=True)
    for nm, r in (("第1打", a), ("第2打", b)):
        ov = len(set(r["picks"]) & set(det))
        print(f"   {nm} 与确定性名单重合 {ov}/5 ⇒ 换血 {5 - ov}/5", flush=True)
        # 名次一起打出来：确定性那 5 只是「按名次自上而下凑满即止」的前缀解
        # （09-24 那场是第 1/2/3/21/22 名，扫到 22 就停），
        # 所以模型给的名次只要跳出这个范围，就说明它在读后半张表——那是生产没做的功课。
        rk = [int(rows.at[c, "rank"]) if c in rows.index else -1 for c in r["picks"]]
        print(f"   {nm} 名次分布 {rk}；理由：" + "；".join(
            f"{c}={r['reasons'].get(c, '')}" for c in r["picks"]), flush=True)
    return 0


def cross_check():
    """零成本读数：把 raw 档里所有历史答案摆在一起比。**跨进程**还能逐位相同，
    才是"可以进归档链路、事后说得清是谁的功劳"的判据；同一进程内比两次只是自证。
    """
    if not os.path.exists(RAW_LOG):
        print(f"❌ 没有 {RAW_LOG} ⇒ 先跑一次", flush=True)
        return 1
    with open(RAW_LOG, encoding="utf-8") as f:
        hist = json.load(f)
    groups = {}
    for rec in hist:
        groups.setdefault(tuple(rec["picks"]), []).append(
            (rec["档"], rec.get("seconds", rec.get("秒"))))
    print(f"raw 档共 {len(hist)} 笔、{len(groups)} 种不同答案：", flush=True)
    for picks, who in groups.items():
        print(f"  {list(picks)} ← {len(who)} 笔（{'; '.join(w[0] for w in who)}）", flush=True)
    print(f"跨进程可复现 = {len(groups) == 1}"
          f"（笔数 {len(hist)}，需 ≥2 笔且分属 ≥2 个进程才成立）", flush=True)
    return 0 if len(groups) == 1 and len(hist) >= 2 else 1


if __name__ == "__main__":
    if "--cross-check" in sys.argv:   # 不调模型，只比历史答案
        sys.exit(cross_check())
    sys.exit(main())
