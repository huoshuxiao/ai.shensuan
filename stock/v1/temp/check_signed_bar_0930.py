# -*- coding: utf-8 -*-
"""09-30 裁定「统一为 `< 0.99`」的夹具：判重那一道闸改吃**带符号**值，牙必须当场钉住。

改的是 `run_ashare_redundancy_check.verdict()`（硬闸从 |corr| 换成带符号 `IC_max`，
政策闸 NEAR_DUP 仍吃 |corr|）。**放宽类判据的规矩**：放行几格、判红几格都要有格数钉着，
而且"该红的还得红"必须同批交付——否则"闸改松了"与"闸坏了"在日志里长得一样。

三块：
  A) `verdict()` 六格真值表（3 格必须照旧挡、1 格是这一刀唯一放开的镜像那一型、2 格是
     政策闸与提名带的边界）＋ 三条「旋钮是活的」对照（拨 NEAR_DUP、拨 ASHARE_RED_BAR、
     复位后必须回到原判词）。
  B) `summarize()` 端到端：喂合成 P/S 矩阵（含一根逐字镜像、一根半镜像），核对
     `pearson_signed_max` / `pearson_max` / `pearson_signed` / `nearest_lib` vs
     `signed_nearest_lib` 各列**真的**是它名字说的那个量（最容易写的错是两列同源、
     或政策闸偷偷吃了带符号值）。
  C) 与旧口径的**差集**：同一份合成矩阵，新臂判重复的集合必须是旧臂的子集，
     且差集恰好等于「abs ≥ 档 且 signed < 档」那条集合（=镜像那两条），多一条少一条都红；
     被放开的那两条必须**逐条落进**「危险区(同簇)」而不是「可提名」（政策闸接得住）。

只读：不碰面板、不写 `data/results/`，产物只落 `stock/v1/temp/tmp_signed_bar_0930/`。

负对照：`SIGNED_NEGCTL=1 /usr/bin/python3.10 check_signed_bar_0930.py` 把 `verdict` 换回
旧口径（硬闸也吃 |corr|），本场必须红**恰好 6 格**（A·镜像、A·格数、B·镜像判词、
B·半镜像判词、C·差集恰好两条、C·政策闸接住）；红集多一格或少一格都算对照失效。
"""
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
import pandas as pd                                     # noqa: E402
import run_ashare_redundancy_check as R                 # noqa: E402

OUT = f"{ROOT}/stock/v1/temp/tmp_signed_bar_0930"
os.makedirs(OUT, exist_ok=True)
BAR, NEAR = R.ASHARE_RED_BAR, R.NEAR_DUP
FAIL, DONE = [], []

# 负对照：`SIGNED_NEGCTL=1` 把 `verdict` 换回 09-30 之前的旧口径（硬闸也吃 |corr|），
# 这一场**必须**红、且红集必须恰好是下面这 6 格。全绿 = 夹具没牙；红集不同 = 尺子错位。
# 换的是模块属性 ⇒ `summarize()` 里那句 `verdict(...)` 也走这份补丁，顺带证明判词真的
# 只有那一个单点。
NEGCTL = os.environ.get("SIGNED_NEGCTL", "") == "1"
EXPECT_RED_NEGCTL = (
    "A·镜像 · 最近邻 −0.995", "A·格数",
    "B·镜像判词", "B·半镜像判词",
    "C·差集恰好两条", "C·政策闸接住")


def old_caliber(signed_max, abs_max):
    if abs_max >= R.ASHARE_RED_BAR:
        return "会被判重复"
    if abs_max >= R.NEAR_DUP:
        return "危险区(同簇)"
    return "可提名"


def check(cid, label, ok, detail):
    (DONE if ok else FAIL).append(cid)
    print(f"  {'✅' if ok else '❌'} [{cid}] {label}｜{detail}")


def verdict_table(bar, near):
    """六格真值表：(格名, signed_max, abs_max, 期望判词)"""
    R.ASHARE_RED_BAR, R.NEAR_DUP = bar, near
    return [
        # ---- 这一刀**没**放开的三格：正号重复照旧挡 ----
        ("正号近重复 +0.995", 0.995, 0.995, "会被判重复"),
        ("含等号 · 恰好等于红线", BAR, BAR, "会被判重复"),
        ("逐字重提（相关=+1.0）", 1.0, 1.0, "会被判重复"),
        # ---- 这一刀**唯一**放开的一型：跟在库反号的镜像 ----
        # 循环 `IC_max[IC_max < 0.99]` 留它（IC_max 是 −0.995），本线硬闸从此不许说"会被判重复"；
        # 但它与在库同簇 ⇒ 政策闸仍挡在 CANDIDATE LIST 外，罪名从"循环会丢"换成"同簇"
        ("镜像 · 最近邻 −0.995", -0.995, 0.995, "危险区(同簇)"),
        # ---- 政策闸带与提名带：两把尺都不碰 ----
        ("0.95（政策闸带）", 0.95, 0.95, "危险区(同簇)"),
        ("0.85（可提名）", 0.85, 0.85, "可提名"),
    ]


def main():
    print(f"[配置] 进程实读 ASHARE_RED_BAR={BAR} NEAR_DUP={NEAR}"
          + ("｜⚠️ 负对照场 SIGNED_NEGCTL=1：`verdict` 已换回旧口径（硬闸吃 |corr|），本场必须红"
             if NEGCTL else ""))
    if NEGCTL:
        R.verdict = old_caliber
    print("\n===== A) verdict() 六格真值表 =====")
    tbl = verdict_table(BAR, NEAR)
    n_block = 0
    for label, s, a, want in tbl:
        got = R.verdict(s, a)
        ok = got == want
        check(f"A·{label}", f"verdict({s:+.3f}, {a:.3f}) 必须是「{want}」", ok, f"实得「{got}」")
        if got == "会被判重复":
            n_block += 1
    # 格数钉死：六格里「会被判重复」必须恰好 3 条（放宽只放开镜像那一型，不是整体松绑）
    check("A·格数", "六格里「会被判重复」必须**恰好 3 条**（该红的还得红 3 格）",
          n_block == 3, f"实得 {n_block} 条｜判词分布 "
          f"{pd.Series([R.verdict(s, a) for _, s, a, _ in tbl]).value_counts().to_dict()}")

    # 两条「旋钮是活的」：判词必须跟着拨动的数翻，否则政策闸/硬闸是写死的
    R.ASHARE_RED_BAR, R.NEAR_DUP = BAR, 0.99
    check("A·拨NEAR", "把政策闸 NEAR_DUP 从 0.90 拨到 0.99 ⇒ 0.95 那条必须从「危险区」翻成「可提名」",
          R.verdict(0.95, 0.95) == "可提名", f"实得「{R.verdict(0.95, 0.95)}」")
    R.ASHARE_RED_BAR, R.NEAR_DUP = 0.90, NEAR
    check("A·拨BAR", "把硬闸 ASHARE_RED_BAR 从 0.99 拨到 0.90 ⇒ 0.95 那条必须翻成「会被判重复」",
          R.verdict(0.95, 0.95) == "会被判重复", f"实得「{R.verdict(0.95, 0.95)}」")
    R.ASHARE_RED_BAR, R.NEAR_DUP = BAR, NEAR
    check("A·复位", "两把旋钮复位后 0.95 那条必须回到「危险区(同簇)」（证明前面两次翻是拨档不是漂移）",
          R.verdict(0.95, 0.95) == "危险区(同簇)", f"实得「{R.verdict(0.95, 0.95)}」")

    # ---------- B) summarize() 端到端：合成矩阵，不走面板 ----------
    print("\n===== B) summarize() 端到端（合成 P/S，候选×在库）=====")
    LIBS = ["SMA(Volume,20)", "STD(Volume,20)", "MOM(Price,5)"]
    P = pd.DataFrame(                             # 带符号逐日截面 Pearson 均值，行=候选 列=在库
        [[-1.0, -0.92, -0.30],    # mirror_exact：在库 #18 取负＝逐字镜像
         [0.20, -0.995, 0.10],    # mirror_half：最近邻是反号 0.995，另有一条 +0.20 的伙伴
         [0.995, 0.30, 0.10],     # pos_dup：正号近重复
         [BAR, 0.10, 0.05],       # eq_bar：恰好等于红线（含等号）
         [0.85, 0.20, -0.10],     # clean：两把尺都不碰
         [0.60, 0.55, 0.50]],     # spearman_only：只用来看 spearman 那列有没有串位
        index=["mirror_exact", "mirror_half", "pos_dup", "eq_bar", "clean", "spearman_only"],
        columns=LIBS)
    S = P * -1.0                                  # 秩相关故意取负：B 里核对它没喂进任何判据
    res = R.summarize(P, S, expr_of=lambda n: f"expr<{n}>")
    print(res.drop(columns=["expr"]).to_string(index=False))
    res.to_csv(f"{OUT}/synthetic_summary.csv", index=False)

    def row(n):
        return res[res["name"] == n].iloc[0]

    # 合成表真造出了 6 行才往下走：`summarize()` 拿到空表会抛一句看不懂的 KeyError，
    # 那等于"尺子没跑"被读成"跑出问题"——先把行数钉住。
    check("B·行数", "合成矩阵必须被 `summarize()` 原样吐出 6 行（下面每一格都按名字取行，空表即全场作废）",
          len(res) == 6 and list(res["name"]) == list(res["name"].unique()),
          f"{len(res)} 行｜列 {list(res.columns)}")
    if len(res) != 6:
        print("⇒ 合成表没进判据，B/C 全部作废");  sys.exit(1)

    check("B·镜像判词", "逐字镜像（全行 −1.0/−0.92/−0.30）必须落「危险区(同簇)」，"
          "带符号最大值 = −0.30、|corr| 最大值 = 1.0",
          row("mirror_exact")["verdict"] == "危险区(同簇)"
          and abs(row("mirror_exact")["pearson_signed_max"] + 0.30) < 1e-12
          and abs(row("mirror_exact")["pearson_max"] - 1.0) < 1e-12,
          f"signed_max={row('mirror_exact')['pearson_signed_max']:.4f} "
          f"pearson_max={row('mirror_exact')['pearson_max']:.4f} "
          f"判词={row('mirror_exact')['verdict']}")
    check("B·半镜像判词", "半镜像（+0.20 与 −0.995 并存）判词必须是「危险区(同簇)」——"
          "旧口径在这里会说「会被判重复」，这一格就是这一刀的全部内容",
          row("mirror_half")["verdict"] == "危险区(同簇)"
          and abs(row("mirror_half")["pearson_signed_max"] - 0.20) < 1e-12
          and abs(row("mirror_half")["pearson_max"] - 0.995) < 1e-12,
          f"signed_max={row('mirror_half')['pearson_signed_max']:.4f} "
          f"pearson_max={row('mirror_half')['pearson_max']:.4f}")
    check("B·含等号", f"带符号最大值恰好 = {BAR} 的那条必须判「会被判重复」（>= 含等号，与循环"
          "保留 `< 0.99` 互补）",
          row("eq_bar")["verdict"] == "会被判重复", f"判词={row('eq_bar')['verdict']}")
    check("B·两列不同源", "镜像那两条的 `nearest_lib`（|corr| 最近邻）与 `signed_nearest_lib`"
          "（带符号最近邻）必须**指向不同的在库因子** ⇒ 证明两根尺真的各自取 max",
          all(row(n)["nearest_lib"] != row(n)["signed_nearest_lib"] for n in ("mirror_exact", "mirror_half")),
          f"mirror_exact: |corr|→{row('mirror_exact')['nearest_lib']} / 带符号→{row('mirror_exact')['signed_nearest_lib']}；"
          f"mirror_half: |corr|→{row('mirror_half')['nearest_lib']} / 带符号→{row('mirror_half')['signed_nearest_lib']}")
    check("B·pearson_signed 是带符号读数", "|corr| 最近邻那一格的**带符号**值必须是负的（镜像行）"
          " ⇒ 落盘列 `pearson_signed` 没被 |corr| 污染",
          all(row(n)["pearson_signed"] < 0 for n in ("mirror_exact", "mirror_half")),
          f"{ {n: round(float(row(n)['pearson_signed']), 4) for n in ('mirror_exact', 'mirror_half')} }")
    check("B·spearman 没串位", "合成里 S = −P ⇒ `spearman_max` 必须等于 `pearson_max`（取绝对值后同数）"
          "且判词不受它影响（六行里没有任何一行按 spearman 判）",
          bool((res["spearman_max"] == res["pearson_max"]).all()),
          f"max|Δ|={float((res['spearman_max'] - res['pearson_max']).abs().max()):.1e}")
    check("B·bar 列跟进程", "落盘 `bar` 列必须等于**本次调用时**的 ASHARE_RED_BAR（拨档要看得见）",
          bool((res["bar"] == BAR).all()), f"{sorted(set(res['bar']))}")

    # ---------- C) 新旧口径的差集：必须恰好是镜像那两条 ----------
    print("\n===== C) 与旧口径的差集（放宽的边界必须数得清）=====")
    old = [R.verdict(float(x), float(x)) for x in res["pearson_max"]]     # 旧臂：|corr| 喂硬闸
    new = list(res["verdict"])
    names = list(res["name"])
    diff = {n for n, o, v in zip(names, old, new) if o != v}
    want_diff = {"mirror_exact", "mirror_half"}
    check("C·差集恰好两条", f"新旧口径判词不同的必须**恰好**是 {sorted(want_diff)}（多一条少一条都红）",
          diff == want_diff, f"实得 {sorted(diff)}｜旧臂判重复 {old.count('会被判重复')} 条 → 新臂 {new.count('会被判重复')} 条")
    check("C·只会变松不会变紧", "新臂「会被判重复」集合必须是旧臂的子集（这一刀只放开镜像，"
          "不许把原本能提名的判成重复）",
          set(n for n, v in zip(names, new) if v == "会被判重复")
          <= set(n for n, v in zip(names, old) if v == "会被判重复"),
          f"旧 {old.count('会被判重复')} 条 / 新 {new.count('会被判重复')} 条")
    # 这一条是「放宽之后闸门还剩什么」的牙：被硬闸放开的每一条，必须被政策闸接住、
    # 判成「危险区(同簇)」而不是「可提名」。若日后有人把政策闸也改成吃带符号值（"彻底统一"），
    # 镜像就会被放进 CANDIDATE LIST —— 这一格当场红。
    caught = {n: v for n, o, v in zip(names, old, new)
              if o == "会被判重复" and v != "会被判重复"}
    check("C·政策闸接住", "凡被硬闸放开的候选，判词必须是「危险区(同簇)」（政策闸吃 |corr| ⇒ "
          "镜像仍不进 CANDIDATE LIST，只换罪名）",
          caught and all(v == "危险区(同簇)" for v in caught.values()),
          f"{caught}")

    print(f"\n===== 汇总：{len(DONE)}/{len(DONE) + len(FAIL)} 通过 =====")
    print(f"[RED_IDS] {'、'.join(FAIL) if FAIL else '无'}")
    if NEGCTL:
        red = set(FAIL)
        want = set(EXPECT_RED_NEGCTL)
        print(f"===== 负对照判读：红集必须**恰好**等于期望的 {len(want)} 格 =====")
        print(f"  期望 {sorted(want)}")
        print(f"  实得 {sorted(red)}")
        print(f"  漏红（该红没红 = 夹具没牙）{sorted(want - red)}｜多红（尺子错位）{sorted(red - want)}")
        ok = red == want
        print(f"{'✅' if ok else '❌'} [NEGCTL] 旧口径下恰好 {len(want)} 格红 ⇒ 这一刀真的被读到了")
        pd.DataFrame([{"negctl": ok, "expect": "|".join(EXPECT_RED_NEGCTL),
                       "got": "|".join(sorted(red))}]).to_csv(f"{OUT}/negctl.csv", index=False)
        sys.exit(0 if ok else 1)
    pd.DataFrame([{"id": i, "ok": i in DONE} for i in DONE + FAIL]).to_csv(
        f"{OUT}/checks.csv", index=False)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
