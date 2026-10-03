# -*- coding: utf-8 -*-
"""第一步（记忆污染探针）：让 9b 只做一件有标准答案的事 —— 猜历史月份里谁涨最多。

为什么先做这一件：第二步是「60 场历史回放，让模型从当日真实观察名单里挑 5 只」。那场
比出来 LLM 臂**跑输**是强证据（背过也不会输），**跑赢**却归不了因 —— 因为它的训练语料
里就有 A 股这些年的走势。所以「赢没赢」这个问题本身要先加一道闸：它到底记不记得某个月
发生过什么。这一支探针量的就是这个。

设计（一场 5 只、答 2 只，每场问两遍）：
  甲·真标签   告诉它真实的那天日期 + 那天 5 只的盘面收盘价，问「往后 21 个交易日谁涨最多」，
              用**面板自己算出的真答案**判分。
  乙·换标签   同样 5 只、同样那批报价，只把日期**换成另一个真实存在过的日子**（隔一年），
              仍然按甲那个月的真结果判分。
  ⇒ 乙是甲的对照组：它把「记得那一个月」和「一直觉得这几只票好」分开。
     甲明显好过乙 = 它真在查日程表 = 期间记忆；甲≈乙 = 日期这个信息压根没进它的判断，
     它给的只是一种稳定的名字偏好（这仍然不是本事，但至少不是背答案）。

判分口径（先验尺子，再花 CPU）：
  真值 = 5 只里未来 21 个交易日「开盘接力收益」最高的两只，与第二步回放**同一把价、同一道
  假台阶护栏**（`ret_open0` 连乘，不是 close 直除，理由见 ashare_screen.build_matrices 那段）。
  随机基线 = 从 5 只里点 2 只，命中数分布 C(2,k)C(3,2-k)/C(5,2) = {0:0.3, 1:0.6, 2:0.1}，
  期望 0.8/场。**精确** p 值由各场自己的 m（模型实际点了几只在册票）卷积出来，不查表。

用法：
    /usr/bin/python3.10 stock/v1/temp/probe_llm_memory_0927.py --detect-check   # 只验尺子，不花 CPU
    /usr/bin/python3.10 stock/v1/temp/probe_llm_memory_0927.py                  # 验完尺子才调模型
产出全部落在 `stock/v1/temp/tmp_llm_evidence_0927/`，**不碰 `stock/v1/data/` 任何归档**。
"""
import os
import sys
import time
from math import comb

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(ROOT, "stock/v1/src"))
import _bootstrap  # noqa: F402,E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (ASHARE_PORT_MIN_AMOUNT, ASHARE_PORT_MIN_LISTED,  # noqa: E402
                    ASHARE_PORT_START, LLM_MODEL)
from strategy.ashare_screen import build_matrices, load_panel  # noqa: E402
import llm_evidence_common as C  # noqa: E402

OUT = os.path.join(ROOT, "stock/v1/temp/tmp_llm_evidence_0927")
os.makedirs(OUT, exist_ok=True)

EPOCHS = 10          # 每场两问 ⇒ 20 次调用；冷加载那一次约 330s，其余每次 33~45s
FWD = 21             # 「一个月」= 21 个交易日
N_PICK = 2           # 问它涨最多的两只
POOL_N = 5
MIN_GAP = 0.005      # 真答案第 2 名与第 3 名的差要大于它，否则「哪两只最多」是含糊的
SEED = 20260927


def null_pmf(m, pool_n=POOL_N, k_target=N_PICK):
    """随机点 m 只在册票的命中数**精确**分布（超几何，不是估的）。

    写出来而不是写死 {0:.3, 1:.6, 2:.1}：模型有时只点 1 只、有时点 3 只，基线必须跟着
    它实际点了几只走，否则「跟随机基线比过」这句话是假的。
    """
    tot = float(comb(pool_n, m))
    hits = []
    for k in range(0, min(m, k_target) + 1):
        other = m - k
        if other > pool_n - k_target:
            hits.append(0.0)
            continue
        hits.append(float(comb(k_target, k) * comb(pool_n - k_target, other)) / tot)
    return np.array(hits)


def pmf_mean(p):
    return float((p * np.arange(len(p))).sum())


def convolve_pmf(pmfs):
    """各场独立 ⇒ 逐场 pmf 卷积成总分分布；空列表 = 总分恒 0。"""
    out = np.array([1.0])
    for p in pmfs:
        out = np.convolve(out, p)
    return out


def score(answer_codes, epoch):
    """命中数 = |答案 ∩ 真前二|；先丢不在册的代码（它答的压根不是这 5 只，不计分也不进分母）"""
    in_set = []
    for c in answer_codes:
        if c in set(epoch["codes"]) and c not in in_set:
            in_set.append(c)
    take = in_set[:N_PICK]
    return sum(1 for c in take if c in epoch["truth_top"]), len(take)


# ===== 探测器自检：尺子脏 = 20 次调用（十几分钟 CPU）换来的四行结论全是废纸 =====
def detect_check(epochs):
    e = epochs[0]
    top2 = list(e["truth_top"])
    bot2 = list(e["codes"][np.argsort(e["fwd"])[:N_PICK]])
    cases = [
        ("真答案 2 只 ⇒ 必须读 2/2", top2, 2),
        ("反向答案（跌得最多的 2 只）⇒ 必须读 0", bot2, 0),
        ("一真一假 ⇒ 必须读 1", [top2[0], bot2[0]], 1),
        ("只点 1 只且点中 ⇒ 必须读 1", [top2[0]], 1),
        ("掺 1 个不在册代码 ⇒ 池外那只不得计分", [top2[0], "SZ999999"], 1),
        ("全不在册 ⇒ 必须读 0", ["SZ999998", "SZ999999"], 0),
    ]
    bad = []
    for name, ans, want in cases:
        got, m = score(ans, e)
        ok = got == want
        print(f"[尺子自检] {name}: 读数={got} (m={m}) 期望={want} {'✅' if ok else '❌'}",
              flush=True)
        if not ok:
            bad.append(name)
    checks = [("m=2 基线分布和=1", abs(null_pmf(2).sum() - 1.0) < 1e-12),
              ("m=2 基线均值=0.80（C(5,2) 精确值）", abs(pmf_mean(null_pmf(2)) - 0.8) < 1e-12),
              ("m=1 基线均值=0.40（跟着点数缩，不是写死的 0.8）",
               abs(pmf_mean(null_pmf(1)) - 0.4) < 1e-12),
              (f"每场真答案不含糊（第2/3名差 > {MIN_GAP:.1%}）",
               all(x["gap"] > MIN_GAP for x in epochs))]
    for name, ok in checks:
        print(f"[尺子自检] {name}: {'✅' if ok else '❌'}", flush=True)
        if not ok:
            bad.append(name)
    if bad:
        print(f"❌ 尺子脏（{len(bad)} 项不符）⇒ 不调模型：{bad}", flush=True)
        return 1
    print(f"✅ 尺子自检 {len(cases) + len(checks)}/{len(cases) + len(checks)} 通过"
          "（含 3 个「必须读低分」的负对照 ⇒ 这把尺子不恒真）", flush=True)
    return 0


def build_epochs(mtx, names, rng):
    """在真实历史上均匀取日子，每场抽 5 只「当天有报价、当天叫得出名字、未来 21 日不断牌」的票"""
    cl, ro = mtx["close"], mtx["ret_open0"]
    days = cl.index
    amount, listed = mtx["amount20"], mtx["listed_days"]
    lo0 = int(np.searchsorted(days, pd.Timestamp(ASHARE_PORT_START)))
    hi = len(days) - FWD - 1
    if hi - lo0 < EPOCHS * 20:
        raise SystemExit(f"面板交易日不够切 {EPOCHS} 场（可用 {hi - lo0}）")
    epochs, tried = [], 0
    for pos in np.unique(np.linspace(lo0, hi - 1, EPOCHS * 3).round().astype(int)):
        if len(epochs) >= EPOCHS:
            break
        tried += 1
        pos = int(pos)
        d, d_end = days[pos], days[pos + FWD]
        ok = (cl.loc[d].notna() & amount.loc[d].ge(ASHARE_PORT_MIN_AMOUNT)
              & listed.loc[d].ge(ASHARE_PORT_MIN_LISTED) & cl.loc[d_end].notna())
        cand = [c for c in cl.columns[ok.to_numpy()] if c in names]
        if len(cand) < POOL_N + 20:
            print(f"⚠️ {d.date()}: 有简称又可投的只有 {len(cand)} 只 ⇒ 换日子", flush=True)
            continue
        leg = ro.loc[days[pos + 1:pos + 1 + FWD], cand]
        live = [c for c in cand if bool(leg[c].notna().all())]
        for _try in range(20):
            codes = np.array([str(c) for c in rng.choice(live, POOL_N, replace=False)])
            fwd = np.array([float((1.0 + leg[c].to_numpy(dtype="float64")).prod() - 1.0)
                            for c in codes])
            order = np.argsort(-fwd)
            gap = float(fwd[order[N_PICK - 1]] - fwd[order[N_PICK]])
            if len(set(np.round(fwd, 6))) == POOL_N and gap > MIN_GAP:
                epochs.append({"n": len(epochs), "信号日": d, "假标签": days[
                                  pos + 252 if pos + 252 < len(days) else pos - 252],
                              "codes": codes, "fwd": fwd, "gap": gap,
                              "truth_top": [codes[i] for i in order[:N_PICK]],
                              "n_pool": len(live)})
                break
        else:
            print(f"⚠️ {d.date()}: 20 次重采都撞不出 {MIN_GAP:.1%} 以上的第2/3名差 ⇒ 换日子",
                  flush=True)
    if len(epochs) < EPOCHS:
        raise SystemExit(f"只凑出 {len(epochs)}/{EPOCHS} 场（试过 {tried} 个日子）⇒ 加大日子候选数")
    print(f"[取材] {EPOCHS} 场（试了 {tried} 个日子），真值=未来 {FWD} 个交易日开盘接力收益前 "
          f"{N_PICK} 名｜对照组日期与真标签全部不同"
          f"={all(e['假标签'] != e['信号日'] for e in epochs)}", flush=True)
    return epochs


def ask(cl, e, cond, rows_txt):
    """cond='real' 用真日期，'shift' 用另一个真日期；两问除日期那句外逐字相同"""
    label = (e["信号日"] if cond == "real" else e["假标签"]).strftime("%Y年%m月%d日")
    user = (f"下面 5 只 A 股在 {label} 的收盘价（人民币）：\n{rows_txt}\n\n"
            f"问题：从 {label} 起往后 {FWD} 个交易日，这 5 只里**涨幅最高**的 {N_PICK} 只是哪两只？"
            "只回 JSON："
            '{"picks":[{"code":"SZ000001","reason":"不超过15字"}]}，'
            f"恰好 {N_PICK} 条，按你认为涨幅从高到低排。")
    return C.ask_json(cl, "你是 A 股历史行情问答器。只能从给定名单里选代码。"
                          "只回 JSON，不要解释文字。", user, f"{e['n']}-{cond}")


def main():
    t0 = time.time()
    print(f"[口径] {EPOCHS} 场 × 2 问｜每场 {POOL_N} 只点 {N_PICK} 只｜随机基线均值 "
          f"{pmf_mean(null_pmf(2)):.2f}/场｜seed={SEED}", flush=True)
    wide, _ = load_panel()
    mtx = build_matrices(wide)
    del wide
    names = C.load_names()
    print(f"[数据] {time.time() - t0:.0f}s｜简称表 {len(names)} 条", flush=True)
    epochs = build_epochs(mtx, names, np.random.default_rng(SEED))
    if detect_check(epochs) != 0:
        return 1
    if "--detect-check" in sys.argv:
        print("（--detect-check：只验尺子，没调模型）", flush=True)
        return 0

    tag_m = LLM_MODEL.replace(":", "_")
    out_m = os.path.join(OUT, tag_m)
    os.makedirs(out_m, exist_ok=True)
    # 缓存与产物都按模型名分档：换模型后这一支要**重新问一遍**，
    # 甲乙对照的答案若来自两个模型，那张对照表就没有意义
    cache = C.Cache(os.path.join(out_m, "llm_cache_probe.jsonl"))
    cl = C.client()
    t_llm = time.time()
    for e in epochs:
        rows_txt = "\n".join(
            f"{c}｜{names[c]}｜收盘 {float(mtx['raw_price'].loc[e['信号日'], c]):.2f}"
            for c in e["codes"])
        for cond in ("real", "shift"):
            key = f"{e['n']}-{cond}"
            rec = cache.get(key)
            if rec is None:
                rec = ask(cl, e, cond, rows_txt)
                cache.put(key, rec)
            ans = rec.get("picks")
            if ans is None:
                print(f"  场{e['n']}·{cond}: ⚠️ {rec.get('错误')}（{rec['秒']}s）", flush=True)
                continue
            h, m = score(ans, e)
            e[f"hits_{cond}"], e[f"m_{cond}"], e[f"ans_{cond}"] = h, m, ans
            print(f"  场{e['n']}·{cond}: 命中 {h}/{N_PICK} 答={ans} 真={e['truth_top']} "
                  f"（{rec['秒']}s, {rec.get('completion_tokens')}tk, "
                  f"finish={rec.get('finish')}）", flush=True)
    print(f"[模型] {time.time() - t_llm:.0f}s｜缓存命中 {cache.hits} 次（续跑不重烧）", flush=True)

    got = [e for e in epochs if f"hits_real" in e and f"hits_shift" in e]
    n = len(got)
    if n < max(3, EPOCHS - 3):
        print(f"❌ 只有 {n}/{EPOCHS} 场两边都拿到答案 ⇒ 配对检验做不了；"
              "重跑一次补缓存（已答过的走缓存不再花 CPU）", flush=True)
        return 1

    df = pd.DataFrame([{
        "场": e["n"], "真标签": e["信号日"].date(), "假标签(对照)": e["假标签"].date(),
        "池内可投": e["n_pool"], "第2/3名差": e["gap"],
        "真答案": "|".join(e["truth_top"]),
        "甲_答": "|".join(e["ans_real"]), "甲_命中": e["hits_real"], "甲_点数": e["m_real"],
        "乙_答": "|".join(e["ans_shift"]), "乙_命中": e["hits_shift"], "乙_点数": e["m_shift"],
        "甲乙同答": set(e["ans_real"]) == set(e["ans_shift"]),
        "5只未来21日收益": "|".join(f"{x:.3f}" for x in e["fwd"]),
    } for e in got])
    df.to_csv(os.path.join(out_m, "probe_memory.csv"), index=False)

    obs_real, obs_shift = int(df["甲_命中"].sum()), int(df["乙_命中"].sum())
    p_real = float(convolve_pmf([null_pmf(e["m_real"]) for e in got])[obs_real:].sum())
    p_shift = float(convolve_pmf([null_pmf(e["m_shift"]) for e in got])[obs_shift:].sum())
    exp_real = sum(pmf_mean(null_pmf(e["m_real"])) for e in got)

    print("\n===== 记忆探针账单（n=%d 场）=====" % n, flush=True)
    print(f"甲·真标签：总命中 {obs_real}/{N_PICK * n}，均值 {obs_real / n:.2f}/场"
          f"｜随机基线期望 {exp_real:.2f}｜精确单侧 p={p_real:.4f}", flush=True)
    print(f"乙·换标签：总命中 {obs_shift}/{N_PICK * n}，均值 {obs_shift / n:.2f}/场"
          f"（按甲那个月的真结果判分）｜p={p_shift:.4f}", flush=True)
    print(f"甲乙给出同一组两只的比例 = {df['甲乙同答'].mean():.0%}", flush=True)
    print(f"逐场命中 甲 {list(df['甲_命中'])} / 乙 {list(df['乙_命中'])}", flush=True)
    print("\n判读（**跑之前**定死的三条分支，不事后挑）：", flush=True)
    if p_real < 0.05 and obs_real > obs_shift:
        print("⇒ 甲显著好过随机、且好过乙 ⇒ **它记得那一个月**。第二步里 LLM 臂任何「跑赢」"
              "只能当上界读，不许写成本事。", flush=True)
    elif p_real < 0.05:
        print("⇒ 甲显著好过随机但**不比乙好** ⇒ 它认的是这几只票本身（名字级先验），不是那个月。"
              "回放里它仍占语料的便宜，但便宜来自「长期偏好某类股票」——这一条要去第二步的"
              "换血率/重合名单上单独看。", flush=True)
    else:
        print("⇒ 甲与随机无异 ⇒ 没有证据说它背得出现代 A 股月度输赢。第二步的回放对比有资格读成"
              "「信号层的本事」；⚠️ 这是**未检出**、不是「无污染」：10 场 × 每场 2 只的分辨力有限，"
              "而且只考了「猜涨幅前二」这一种题型。", flush=True)
    print(f"（产物 {os.path.join(out_m, 'probe_memory.csv')}；{2 * n} 条原始回答在 "
          "llm_cache_probe.jsonl）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
