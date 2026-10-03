# -*- coding: utf-8 -*-
"""乙（09-30）：换轴这个决定，只用「过去」挑、拿「没见过的年份」计分能剩下多少

为什么要有这一步（甲只给了样本内横比，五臂横比本身就是五次比较）：
  09-30 甲那五臂 + 09-28 那两条旧轴臂都跑在**同一段样本**上（面板 2852 个 live 交易日、
  末日 2026-09-24、570 场），所以六条臂的逐年超额可以**直接并排**，不必再跑面板。
  样本内看冠军 = 「事后翻账本挑当年最灵的」；这条路真正的风险是冠军诅咒：
  五条臂里挑一条，就算全是噪音也必有一条排第一。所以要用只看得见的过去做决定：

    扩张窗 walk-forward：对每个测试年 T，只用 T 之前的年份算平均超额、挑最高的那条臂，
    然后**只拿 T 这一年**给它计分。挑臂的动作从不偷看 T 及以后。

三道自检（任何一道红 exit=1，读数不引）：
  W1 身份闸：两份产物里「现轴·配额·5 席」这一列必须逐年逐位相等（|Δ|<1e-12）。
     这一条过了才允许把 09-28 那两条旧轴臂并进甲的五臂——它们是同一批 570 场；
     过不了说明两次样本/口径不齐，合并表当场作废。
  W2 年份闸：测试年必须恰好 2019~2026 八年、且 2015 起逐年无缺（缺一年=并表丢了行）。
  W3 负对照（WF_NEGCTL=1 把挑臂器钉成「永远挑现轴」）：walk-forward 的样本外合计
     必须与「现轴从头买到尾」的合计**逐位相等**；不等说明挑臂器根本没接上计分。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
A_Y = os.path.join(ROOT, "stock/v1/temp/tmp_price_axis_ctrl_0930/price_axis_0930_yearly.csv")
B_Y = os.path.join(ROOT, "stock/v1/temp/tmp_axis_ctrl_0928/axis_ctrl_0928_yearly.csv")
OUT_DIR = os.path.join(ROOT, "stock/v1/temp/tmp_walk_forward_0930")
CELL = "｜E-a 20/10/10/10 回填｜5席"      # 掏钱那一格：配额名单 + 5 席下单
# 两份产物给同一根轴起了不同名字，并表前先归一（甲把现轴叫「臂0」、09-28 叫表达式）
CUR_KEY = "现轴（生产）"
CUR_A_LBL, CUR_B_LBL = "臂0 现轴 SMA(vol,20)", "现轴 ts_mean(volume,20)"
OLD_B_LBL = "旧轴 ts_std(volume,20)·只换排序键"
NEGCTL = os.environ.get("WF_NEGCTL") == "1"
FIRST_TEST = 2019        # 攒够 4 个训练年（2015~2018）才开始计分
TRADING_HINT = "（2026 只有到 09-24 的 8 场，不是整年）"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for p in (A_Y, B_Y):
        if not os.path.exists(p):
            raise SystemExit(f"❌ 缺输入 {p}⇒ 甲那场还没落产物，乙不能先跑")
    A, B = pd.read_csv(A_Y), pd.read_csv(B_Y)
    A = A.drop(columns=[c for c in A.columns if c == "Unnamed: 0"]).set_index("year")
    B = B.drop(columns=[c for c in B.columns if c == "Unnamed: 0"]).set_index("year")

    # ---------- 只取「掏钱那一格」那一列：每臂在产物里有 4 个格子，混了就错拿 10 席 ----------
    def money_cols(df, src):
        out = {}
        for c in df.columns:
            if c.endswith(CELL):
                out[c.split("｜")[0]] = c
        if len(out) != len(set(out.values())):
            raise SystemExit(f"❌ {src} 里两格同名（臂标签撞车）⇒ 并表会拿错列")
        return out

    mc_a, mc_b = money_cols(A, "甲（0930）"), money_cols(B, "09-28")
    arms = {}
    for lbl, c in mc_a.items():
        arms[CUR_KEY if lbl == CUR_A_LBL else lbl] = A[c]
    if CUR_KEY not in arms:
        raise SystemExit(f"❌ 甲的表里没有「{CUR_A_LBL}」这一臂 ⇒ 参照行没了，乙不能跑")
    # 09-28 只并「旧轴·只换排序键」这一条（「闸门也跟轴」与它逐值同，并进来是重复计数）
    if OLD_B_LBL not in mc_b:
        raise SystemExit(f"❌ 09-28 表里找不到「{OLD_B_LBL}」的 5 席那一列")
    arms[OLD_B_LBL] = B[mc_b[OLD_B_LBL]]

    # ---------- W1 身份闸：现轴那一列必须两份逐位相等 ----------
    cur_a, cur_b = A[mc_a[CUR_A_LBL]], B[mc_b[CUR_B_LBL]]
    both = pd.concat([cur_a, cur_b], axis=1, join="inner")
    max_d = float((both.iloc[:, 0] - both.iloc[:, 1]).abs().max())
    w1 = max_d < 1e-12 and len(both) == len(cur_a)
    print(f"W1 身份闸：现轴列两份共 {len(cur_a)}/{len(cur_b)} 年、交集 {len(both)} 年，"
          f"max|Δ|={max_d:.3e} ⇒ {'✅ 同一批 570 场，可以并表' if w1 else '❌ 样本/口径不齐，作废'}")

    tab = pd.DataFrame(arms).sort_index()
    yrs = list(tab.index)

    # ---------- W2 年份闸 ----------
    tests = [y for y in yrs if y >= FIRST_TEST]
    w2 = tests == list(range(FIRST_TEST, FIRST_TEST + len(tests))) and len(tests) == 8 \
        and yrs[:FIRST_TEST - 2015] == list(range(2015, FIRST_TEST))
    print(f"W2 年份闸：全样本 {yrs[0]}~{yrs[-1]} 共 {len(yrs)} 年、"
          f"测试年 {tests[0]}~{tests[-1]} 共 {len(tests)} 年 ⇒ "
          f"{'✅ 逐年无缺' if w2 else '❌ 有缺年，并表丢了行'}")

    # ---------- 逐年横比（先把六臂摆出来，让人看见冠军是谁、赢在哪几年） ----------
    pd.set_option("display.width", 300)
    print("\n===== 六臂·配额名单 5 席·逐年超额（小数=当年相对过闸池等权多赚的比例）=====")
    print(tab.to_string(float_format=lambda v: f"{v:+.3f}"))
    full_rank = tab.mean().sort_values(ascending=False)
    print("\n全样本平均（= 事后翻账本，冠军诅咒就藏在这一行）：")
    for k, v in full_rank.items():
        print(f"    {v:+.4f}  {k}")

    # ---------- walk-forward：只用 T 之前挑臂，只拿 T 计分 ----------
    rows, chosen = [], []
    for T in tests:
        tr = [y for y in yrs if y < T]
        sc = tab.loc[tr].mean()
        pick = CUR_KEY if NEGCTL else sc.idxmax()
        chosen.append(pick)
        rows.append({"测试年": T, "训练年数": len(tr), "挑中": pick,
                     "挑中臂当年": float(tab.loc[T, pick]),
                     "现轴当年": float(tab.loc[T, CUR_KEY]),
                     "差": float(tab.loc[T, pick] - tab.loc[T, CUR_KEY]),
                     **{f"训练期均值·{k.split('｜')[0][:12]}": float(sc[k]) for k in tab.columns}})
    wf = pd.DataFrame(rows)
    print("\n===== 扩张窗 walk-forward（每行都用「当年之前」的年份挑臂，当年才算分）=====")
    print(wf[["测试年", "训练年数", "挑中", "挑中臂当年", "现轴当年", "差"]].to_string(
        index=False, float_format=lambda v: f"{v:+.4f}"))

    s_wf = float(wf["挑中臂当年"].sum())
    s_cur = float(wf["现轴当年"].sum())
    champ = full_rank.index[0]
    s_champ = float(tab[champ].loc[tests].sum())
    n_pick_cur = int((pd.Series(chosen) == CUR_KEY).sum())
    print(f"\n样本外合计（{tests[0]}~{tests[-1]}，{len(tests)} 年，2026 为残年）：")
    print(f"    walk-forward 挑臂（只看过去）  {s_wf:+.4f}")
    print(f"    现轴从头买到尾（生产现状）      {s_cur:+.4f}")
    print(f"    全样本冠军「{champ}」（事后诸葛）  {s_champ:+.4f}")
    print(f"    ⇒ 只看过去比现状多 {s_wf - s_cur:+.4f}；"
          f"事后诸葛比只看过去多 {s_champ - s_wf:+.4f}（这一格就是冠军诅咒的大小）")
    print(f"    八个测试年里挑臂器有 {n_pick_cur} 年选回现轴、{len(tests) - n_pick_cur} 年换臂")
    print(f"    被选中过的臂：{sorted(set(chosen))}")

    # ---------- W3 负对照：钉成永远挑现轴 ⇒ 合计必须与「现轴从头买」逐位相等 ----------
    w3 = abs(s_wf - s_cur) < 1e-12 if NEGCTL else abs(s_wf - s_cur) > 1e-12
    print(f"\nW3 {'负对照' if NEGCTL else '正对照'}：wf 合计 {s_wf:.12f} vs 现轴合计 {s_cur:.12f}"
          f"｜差 {s_wf - s_cur:+.3e} ⇒ {'✅' if w3 else '❌'}"
          f"（{'钉成挑现轴后两者必须逐位相等' if NEGCTL else '正常场这两条本就不该相等'}）")

    wf.to_csv(os.path.join(OUT_DIR, "walk_forward_axis_0930.csv"), index=False)
    tab.to_csv(os.path.join(OUT_DIR, "six_arms_yearly_0930.csv"), index_label="year")
    print(f"\n[输出] {OUT_DIR}/walk_forward_axis_0930.csv、six_arms_yearly_0930.csv")

    bad = [n for n, ok in (("W1 身份闸", w1), ("W2 年份闸", w2), ("W3 对照", w3)) if not ok]
    if bad:
        print(f"[判据] {'、'.join(bad)} 红 ⇒ 退出码 1，上面的数一个都别引")
        sys.exit(1)
    print("[判据] W1/W2/W3 全绿")


if __name__ == "__main__":
    main()
