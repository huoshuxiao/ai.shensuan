# -*- coding: utf-8 -*-
"""丙：本账里最后两根「没量过」的人工数字 —— 判重红线 `ASHARE_RED_BAR`、三条起点日期。

完全离线：只读四张已有产物（三份环3 归档 + 环1 的逐日 IC 表），**不读 0.8GB 面板、
不跑任何回放、不写生产目录**。产物只落 `stock/v1/temp/tmp_red_bar_ladder_0930/`。

两根各自要回答的问题：

  1) `ASHARE_RED_BAR = 0.99`（环3 判重红线）
     它管的是「一条候选与在库因子的逐日截面相关 ≥ 这根线 ⇒ 判重复、不提名」。
     在库归档只给过生产那一档的判词（三份 check csv 的 `verdict` 列），从没逐档量过
     「线挪到 0.90 / 0.95 / 0.98 会多挡几条」。这一步把 21 条历史候选（1+7+13 三份归档、
     按名字并起来）在 0.80~1.01 十档上各判一遍。
     ⚠️ 一根必须先说清楚的结构性事实：这根线**管不到循环**。RD-Agent 的
     `factor_runner.deduplicate_new_factors` 里那条 0.99 是**写死在源码里的字面量**
     （本地已核：conda 环境 `rdagent` 的 site-packages，rdagent 0.8.0，
     `rdagent/scenarios/qlib/developer/factor_runner.py:71` 的 `IC_max[IC_max < 0.99]`，
     全仓 `scenarios/qlib/` 只有这一处 0.99、没有任何环境变量或 conf 字段喂它）
     ⇒ 拨本线的数只改「提名预检」和落盘 `bar` 列，不改循环那把刀。
     （**本 docstring 以前写的是"本机没有 rdagent 可复核"，那句是错的**：判据用的是
     系统 python3.10 的 `import rdagent`，而官方循环**按设计**跑在独立 conda 环境里
     （`official_rdagent.py:5-8` 的隔离约定）⇒ 那个 ModuleNotFoundError 只证明
     「管线解释器看不见它」，不证明「本机没装」。教训同 [[config 同名键重复]] 那一类：
     **import 失败只能证「这个解释器没有」，要证「本机没有」得扫 conda env 与镜像。**）
     ⚠️ **09-30 用户裁定「统一为 `< 0.99`」＝本线硬闸从 |corr| 改成带符号值**（见
     `run_ashare_redundancy_check.verdict()`）。本表因此改成**两臂**：`判xx` 列 = 旧口径
     复刻（把 |corr| 同时喂给 `verdict` 的两个自变量 ⇒ 与历史归档逐字同一把尺，V1 钉住），
     `符xx` 列 = 现生产口径（带符号 `signed_max` 喂硬闸、|corr| 只喂政策闸）。
     **带符号那一臂不靠重跑面板**：三份 `_detail.csv` 存的是候选×在库的**带符号**逐对
     均值，`groupby.max()` 就是循环的 `IC_max` ⇒ 离线可复算，但必须先过身份闸 V0
     （重算出的 |corr| 最大值要与归档 `pearson_max` 逐位相等，`max|Δ| < 1e-9`；实测 0.0），
     否则「两臂相同」这个读数就是尺子坏了而不是口径没差。
     两臂在 21 条历史候选上**逐格相同**（V12 读数），这**不等于**统一口径没意义：
     牙在 V11 —— 三格合成镜像候选里两臂必须翻（旧"会被判重复"/新"危险区(同簇)"），
     翻不出来就说明新臂是死代码。
     剩下的口径差（本地预检 vs 循环内部，聚合顺序相同——都是「逐日截面 Pearson →
     对配对取日均值 → 对在库伙伴取最大」，含等号两边一致＝V4 钉住的那格——但仍不等价）：
       a) 本地有 `ASHARE_MIN_CS` 截面样本数闸、且当日任一列是常量就**整日丢弃**
          （`run_ashare_redundancy_check.daily_corr()` 循环开头那两条 `continue`）；
          循环没有截面数闸，常量列让 pandas 给 NaN、`.mean()` 跳过 NaN
          ⇒ 分母不同（同一天一边算进均值、一边被跳过），临界值上可翻判词。
       b) 比的**对象集**不同：循环对的是它自己那一叠 SOTA 实验的因子，本地对的是
          `factors.json` 在库 ⇒ 两边看到的"最近邻"本来就不是同一批人。
       c) 本线另有一道循环**没有**的政策闸 `NEAR_DUP=0.90`（它仍吃 |corr|，所以镜像
          因子照旧不提名，只是罪名从"循环会丢"换成"同簇"）。

  2) `ASHARE_EVAL_START=2010` / `ASHARE_PORT_START=2015` / `ASHARE_RED_START=2015`
     三条人为起点各自决定一条链有多长样本。这里只算**日历层**：每个候选起点 ⇒
     剩多少 live 天、多少调仓场、逐日序列够不够 `ASHARE_ROLL_WINDOW` 天。
     ⚠️ 10-01 改口径：这一列原来印「铺满 F 折? = 够 ⟺ n ≥ WINDOW × FOLDS」，那个算术
     在总账 §十三 V7 已判作废（`np.array_split` 的真下界只有两条：**序列 ≥ WINDOW 天**、
     **F ≤ 天数**，从来不需要 252×F）⇒ 折数拨到 25 之后旧列会对所有起点恒印「不够」，
     是一把量不到东西还照常亮绿灯的尺子。现在两列分开印：序列够不够 252 天、现档折数下每折多少天。
     ⚠️ 换起点会**重新筛池**（环1/环2 的 MIN_OBS=250 是按样本内计数的），本表没重筛 ⇒
     这一列只回答"样本有多长"，不回答"换起点结论翻不翻"（那要重跑，代价见结尾）。

判据 14 行（红线 11 行＝V0~V5 + V7~V11；起点 3 行＝S1~S3；V6/V12 是读数不判）。任何一行红 ⇒ exit=1。
恒真防护：V2/V3/S2 三行是**故意要让尺子翻脸**的档（0 与 1.01 与"起点在末日之后"），
它们绿不代表尺子好，**红才代表尺子坏**；S3 拿场数公式自我对表；V7 不许拿"对照行被挡"
冒充真战果（生产档挡下的必须全部是「与在库逐字同式」那几条），V8 是它的正对照：
同一把尺钉到 0.90 必须翻出真候选被挡，翻不出来就是 V7 恒真；
**两臂之差实测为 0（V12）⇒ 新臂自带一条 V11 合成镜像三格夹具**，两臂不翻就判红，
免得"改了口径但没人能证明尺子读得到口径"。
"""
import os
import sys

ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, f"{ROOT}/stock/v1/src")
import pandas as pd                                            # noqa: E402
import run_ashare_redundancy_check as R                        # noqa: E402  复用 verdict() 单点
import config as C                                             # noqa: E402

RES = f"{ROOT}/stock/v1/data/results"
OUT = f"{ROOT}/stock/v1/temp/tmp_red_bar_ladder_0930"
os.makedirs(OUT, exist_ok=True)
CHECKS = []

# 三份历史环3 归档（不同批次提名的候选，按名字并起来；同名同分即一条）
ARCH = ["ashare_redundancy_check.csv", "ashare_redundancy_policy090.csv",
        "ashare_redundancy_axis_std20.csv"]
DETAIL = {"ashare_redundancy_check.csv": "ashare_redundancy_detail.csv",
          "ashare_redundancy_policy090.csv": "ashare_redundancy_policy090_detail.csv",
          "ashare_redundancy_axis_std20.csv": "ashare_redundancy_axis_std20_detail.csv"}
BARS = [0.00, 0.80, 0.85, 0.90, 0.95, 0.97, 0.98, 0.99, 1.00, 1.01]
PROD_BAR = C.ASHARE_RED_BAR
NEAR = R.NEAR_DUP


def check(cid, label, ok, detail):
    CHECKS.append((cid, label, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} [{cid}] {label}｜{detail}")
    return ok


def readout(label, detail):
    print(f"  ·  [{label}·读数] {detail}")


def load_cands():
    frames = []
    for f in ARCH:
        t = pd.read_csv(f"{RES}/{f}")
        t["src"] = f
        # 带符号那臂从 `_detail.csv` 复算：那是候选×在库的**带符号**逐对日均值矩阵，
        # groupby.max() 就是循环的 `IC_max`。不重跑面板，但必须过 V0 的身份闸。
        d = pd.read_csv(f"{RES}/{DETAIL[f]}")
        rec = d.groupby("candidate")["pearson"].agg(
            signed_max="max", abs_recalc=lambda s: s.abs().max()).reset_index()
        miss = set(t["name"]) - set(rec["candidate"])
        if miss:
            raise SystemExit(f"[作废] {f} 的候选在 {DETAIL[f]} 里取不到带符号列：{sorted(miss)}")
        t = t.merge(rec, left_on="name", right_on="candidate", how="left")
        frames.append(t)
    a = pd.concat(frames, ignore_index=True)
    # 同名多档：只有分数也相同才是真重复（不同批次里同名不同分要分开算）
    g = (a.groupby(["name", "pearson_max"], as_index=False)
           .agg(n_src=("src", "size"), srcs=("src", lambda s: "|".join(sorted(set(s)))),
                second=("pearson_second", "max"), spear=("spearman_max", "max"),
                archived=("verdict", lambda s: "|".join(sorted(set(s)))),
                bar_col=("bar", lambda s: "|".join(sorted(set(f"{float(x):.2f}" for x in s)))),
                signed_max=("signed_max", "min"), abs_recalc=("abs_recalc", "max"),
                signed_n=("signed_max", "nunique"))
           .sort_values("pearson_max", ascending=False)
           # 必须 reset：排完序后索引是原位置，位置型布尔掩码与它对不齐会**静默选错行**
           # （09-30 实测踩到：V8 因此把 0.4265 那几条算成「0.90 档被挡」）
           .reset_index(drop=True))
    return g



def main():
    print(f"[配置] 进程实读 ASHARE_RED_BAR={PROD_BAR} NEAR_DUP={NEAR} "
          f"RED_START={C.ASHARE_RED_START} EVAL_START={C.ASHARE_EVAL_START} "
          f"PORT_START={C.ASHARE_PORT_START} HOLD={C.ASHARE_PORT_HOLD} "
          f"MIN_OBS={C.ASHARE_MIN_OBS} FOLDS={C.ASHARE_ROLL_FOLDS} ROLL={C.ASHARE_ROLL_WINDOW}")
    g = load_cands()
    print(f"[候选] 三份归档合计 {len(g)} 条唯一（候选×分数）组合，"
          f"pearson_max 区间 {g.pearson_max.min():.4f}~{g.pearson_max.max():.4f}")

    # ---------- 判据 V0：带符号那臂的**身份闸**——detail 复算的 |corr| 必须与归档逐位等 ----------
    # 没有这一闸，"两臂相同"就只是"我读错表"的另一种写法（两臂恒等不许直接当结论）。
    d_abs = float((g.abs_recalc - g.pearson_max).abs().max())
    check("V0", "身份闸：从 `_detail.csv` 复算的 |corr| 最大值必须与归档 `pearson_max` 逐位相等"
          "（否则带符号那臂读的是另一张表，全部两臂读数作废）",
          d_abs < 1e-9 and int(g.signed_n.max()) == 1 and g.signed_max.notna().all(),
          f"max|Δ|={d_abs:.1e}（{len(g)} 条）｜同名多源里带符号列不一致的 {int((g.signed_n > 1).sum())} 条"
          f"｜带符号最大值区间 {g.signed_max.min():.4f}~{g.signed_max.max():.4f}")
    if d_abs >= 1e-9:
        print("⇒ 复算与归档不是同一张表，带符号那臂作废");  return finish()

    # ---------- 判据 V1：拿入口自己的 verdict() 重算，必须与归档判词逐字相同 ----------
    R.ASHARE_RED_BAR = PROD_BAR                      # 生产档：与归档同一把尺
    v_now = [R.verdict(float(x), float(x)) for x in g.pearson_max]   # 旧口径臂（|corr| 喂两个自变量）
    same = [a == b for a, b in zip(g.archived, v_now)]
    check("V1", "生产档判词逐字复现归档 verdict 列（证明本表与入口同一把尺）",
          all(same), f"{sum(same)}/{len(g)} 条相同"
          + ("" if all(same) else f"｜首个不同：{g.name[same.index(False)][:28]} "
                                  f"归档={g.archived[same.index(False)]} 本表={v_now[same.index(False)]}"))
    if not all(same):
        print("⇒ 口径已经不是一家，后面所有档位读数作废");  return finish()

    # ---------- 逐档梯子：每档现算一次（判据函数是入口那一个，不另写） ----------
    g["identity"] = g.pearson_max >= 1.0 - 1e-9      # 与在库某条逐字同式（相关=1.000000）
    rows = []
    for bar in BARS:
        R.ASHARE_RED_BAR = bar
        vd = [R.verdict(float(x), float(x)) for x in g.pearson_max]     # 旧口径复刻臂
        vn = [R.verdict(float(s), float(a)) for s, a
              in zip(g.signed_max, g.pearson_max)]                      # 现生产口径臂
        g[f"判{bar:.2f}"] = vd                       # 列名带档，后面按列取、不按位置
        g[f"符{bar:.2f}"] = vn
        rows.append({
            "bar": bar, "生产档": bar == PROD_BAR,
            "会被判重复": vd.count("会被判重复"), "危险区": vd.count("危险区(同簇)"),
            "可提名": vd.count("可提名"),
            "挡下占比": round(vd.count("会被判重复") / len(g), 4),
            "现口径会被判重复": vn.count("会被判重复"),
            "两臂差": vn.count("会被判重复") - vd.count("会被判重复")})
    lad = pd.DataFrame(rows)
    lad.to_csv(f"{OUT}/red_bar_ladder.csv", index=False)
    g.to_csv(f"{OUT}/cand_x_bar.csv", index=False)
    print("\n===== 判重红线逐档（21 条历史候选 = 1+7+13 三份归档并集，按名字×分数去重）=====")
    print("（`会被判重复` = 旧口径复刻臂 |corr|；`现口径…` = 带符号 `IC_max`，即 09-30 统一后的生产硬闸）")
    print(lad.to_string(index=False))

    blocked = lad.set_index("bar")["会被判重复"]
    check("V2", "牙·下限：红线 0.00 ⇒ 必须**全部**判重复（尺子关到底要清空）",
          blocked[0.00] == len(g), f"{blocked[0.00]}/{len(g)}")
    check("V3", "牙·上限：红线 1.01 ⇒ 必须**一条不挡**（尺子抬到天上要放空）",
          blocked[1.01] == 0, f"{blocked[1.01]}/{len(g)}")
    dup = g[g.pearson_max >= 1.0]
    R.ASHARE_RED_BAR = 1.00
    check("V4", "真值控制：逐字重提那条（pearson_max=1.000000）在 1.00 档**两臂都仍判重复** ⇒ 钉住「>= 含等号」"
          "（identity 那条带符号值就是 +1.0，两臂在这一格必须同判）",
          len(dup) > 0
          and all(R.verdict(float(x), float(x)) == "会被判重复" for x in dup.pearson_max)
          and all(R.verdict(float(s), float(a)) == "会被判重复"
                  for s, a in zip(dup.signed_max, dup.pearson_max)),
          f"1.00 档判重复的有 {sum(1 for x in g.pearson_max if R.verdict(float(x), float(x)) == '会被判重复')} 条"
          f"（对照行 {len(dup)} 条，带符号值 {sorted(dup.signed_max.round(6))}）")
    mono = all(blocked[b] >= blocked[n] for b, n in zip(BARS[:-1], BARS[1:]))
    n_moves = (blocked.diff().dropna() != 0).sum()
    blocked_new = lad.set_index("bar")["现口径会被判重复"]
    mono_new = all(blocked_new[b] >= blocked_new[n] for b, n in zip(BARS[:-1], BARS[1:]))
    check("V5", "梯子有牙：挡数随红线上升单调不增，且十档里至少挪动 3 次（两臂各自都要满足）",
          mono and mono_new and n_moves >= 3,
          f"单调={mono}/{mono_new}｜旧臂 0.00→1.01 为 "
          f"{'→'.join(str(int(blocked[b])) for b in BARS)}｜现口径臂 "
          f"{'→'.join(str(int(blocked_new[b])) for b in BARS)}｜相邻变化 {n_moves} 次")
    # 生产档挡住的那几条，是"真候选"还是"跟在库某条逐字同式的对照"？分开算才不拿对照冒充战果
    # 判据按分数而不是名字：identity 的定义是「与在库某条相关 = 1.000000」＝同一个因子，
    # 名字里带不带"对照"三个字是文案（三份归档里三条 identity 只有一条名字含"对照"）
    blk = g[f"判{PROD_BAR:.2f}"] == "会被判重复"
    blk_n = g[f"符{PROD_BAR:.2f}"] == "会被判重复"
    real_blk = g[(blk | blk_n) & ~g.identity]
    check("V7", f"生产档 {PROD_BAR} 挡下的（两臂并起来）必须**全部**是「与在库逐字同式」那几条（真候选一条没被挡）",
          len(real_blk) == 0 and int(blk.sum()) >= 1,
          f"旧臂挡 {int(blk.sum())} 条／现口径臂挡 {int(blk_n.sum())} 条、逐字同式 {int(g.identity.sum())} 条"
          f" ⇒ 非 identity 被挡 {len(real_blk)} 条｜两臂集合相同={bool((blk == blk_n).all())}"
          f"｜被挡名单 {[n[:24] for n in g[blk | blk_n].name]}")
    # V8 正对照：同一把尺挪到政策线 0.90，必须出现「非 identity」被挡 ⇒ 钉住 V7 不是恒真
    blk90 = g["判0.90"] == "会被判重复"
    real90 = g[blk90 & ~g.identity]
    check("V8", "正对照：同一判据钉到 0.90 档必须翻出「真候选被挡」（证明 V7 不恒真）",
          len(real90) >= 1, f"0.90 档挡下 {int(blk90.sum())} 条，其中非 identity {len(real90)} 条"
          f"（分数 {sorted(real90.pearson_max.round(4))}｜名字 {[n[:20] for n in real90.name]}）")
    # V9 掩码自证：每一档「被挡」的行必须**恰好**是分数 ≥ 该档的那些（防位置型掩码选错行）
    bad = {f"{b:.2f}": int(((g[f"判{b:.2f}"] == "会被判重复") != (g.pearson_max >= b)).sum())
           for b in BARS}
    check("V9", "掩码自证（旧臂）：十档里「被挡」集合必须恰好等于「|corr| ≥ 该档」集合",
          not any(bad.values()), f"各档错位行数 {bad}")
    # V10 同一条自证挪到现口径臂：判据吃的是**带符号**值，掩码也得按带符号值对表
    bad_n = {f"{b:.2f}": int(((g[f"符{b:.2f}"] == "会被判重复") != (g.signed_max >= b)).sum())
             for b in BARS}
    check("V10", "掩码自证（现口径臂）：十档里「被挡」集合必须恰好等于「带符号 IC_max ≥ 该档」集合",
          not any(bad_n.values()), f"各档错位行数 {bad_n}")
    # V11 牙：两臂在这 21 条上实测**逐格相同**（V12），所以新臂必须靠合成镜像证明它读得到口径。
    # 三格各钉一个判词；两臂不翻 = 新臂是死代码；「正号那条也翻」= 新臂太松。
    R.ASHARE_RED_BAR = PROD_BAR
    MIR = [("镜像·最近邻 −0.995 但另有 +0.20 的伙伴", 0.20, 0.995, "会被判重复", "危险区(同簇)"),
           ("逐字镜像·在库某条取负 = −1.0", -1.0, 1.00, "会被判重复", "危险区(同簇)"),
           ("真·正号重复 +0.995（新臂也必须挡）", 0.995, 0.995, "会被判重复", "会被判重复")]
    cells = []
    for label, s_max, a_max, want_old, want_new in MIR:
        got_old, got_new = R.verdict(a_max, a_max), R.verdict(s_max, a_max)
        cells.append((label, got_old, got_new, got_old == want_old and got_new == want_new))
        print(f"     {'✅' if cells[-1][3] else '❌'} [V11·格] {label}｜旧臂={got_old} 现口径臂={got_new}"
              f"（期望 {want_old} / {want_new}）")
    check("V11", "牙·合成镜像三格：旧口径挡的镜像候选，现口径必须翻成「危险区(同簇)」，"
          "而 +0.995 那条**不许**跟着翻（证明新臂既不死也不过松）",
          all(c[3] for c in cells),
          f"{sum(c[3] for c in cells)}/{len(cells)} 格对｜"
          f"（政策闸 NEAR_DUP={NEAR} 仍吃 |corr| ⇒ 镜像不提名，只是罪名换掉）")
    R.ASHARE_RED_BAR = PROD_BAR
    # 生产档与「政策线 0.90」那档的差：多挡几条（这是这根旋钮的实际代价）
    d90 = int(blocked[0.90] - blocked[PROD_BAR])
    readout("V6 拨到政策线 0.90 的代价",
            f"多挡 {d90} 条／{len(g)} 条历史候选（1+7+13 三份归档并集）；"
            f"这些条的 pearson_max 落在 "
            f"{sorted(g[(g.pearson_max >= 0.90) & (g.pearson_max < PROD_BAR)].pearson_max.round(4))}")
    readout("V6 生产档离红线多近（裕度分布）",
            "距红线 = " + ", ".join(
                f"{r['name'][:22]}… {PROD_BAR - float(r['pearson_max']):+.4f}"
                for r in g.nlargest(4, "pearson_max").to_dict("records")))
    # V12 读数（不判）：统一口径在**这批历史候选**上值几条。它是读数不是判据——
    # 真候选里有没有"最近邻是负号"的，取决于库里有什么，把"差 0 条"写成判据就成了
    # 下一批候选一有镜像就红的变更探测器；牙在 V11。
    diff_i = g[g[f"判{PROD_BAR:.2f}"] != g[f"符{PROD_BAR:.2f}"]]
    gap = (g.pearson_max - g.signed_max)
    readout("V12 统一为带符号 < 0.99 在 21 条历史候选上值几条",
            f"判词不同的 {len(diff_i)} 条｜两臂逐档计数差 {sorted(set(lad['两臂差']))}｜"
            f"|corr| 与带符号最大值的差：中位 {gap.median():.4f}、最大 {gap.max():.4f}"
            f"（差 > 0.001 的 {int((gap > 0.001).sum())} 条 ⇒ 这批候选的最近邻全是正号，"
            f"口径统一对**已有名单**零改动，改动只落在"
            f"「以后一根跟在库反号的因子」那一格）")

    # ---------- 起点日期：只算日历层 ----------
    d = pd.read_csv(f"{RES}/ashare_ic_daily.csv", usecols=["date"])["date"]
    print(f"\n===== 三条人为起点各值多少样本（轴＝环1 逐日 IC 表，{len(d):,} 个 live 交易日，"
          f"末日 {d.iloc[-1]}）=====")
    hold, folds, roll = C.ASHARE_PORT_HOLD, C.ASHARE_ROLL_FOLDS, C.ASHARE_ROLL_WINDOW
    srows = []
    for s in list(dict.fromkeys(
            ["2010-01-01", C.ASHARE_EVAL_START, C.ASHARE_RED_START, "2013-01-01",
             "2015-01-01", "2018-01-01", "2020-01-01", "2022-01-01", "2024-01-01",
             "2099-01-01"])):
        n = int((d >= s).sum())
        n_rebal = len(range(0, max(0, n - 1 - hold), hold))
        srows.append({"起点": s, "生产哪条链用它": " / ".join(
            lbl for lbl, v in (("环1", C.ASHARE_EVAL_START), ("环3", C.ASHARE_RED_START),
                               ("环2", C.ASHARE_PORT_START)) if v == s) or "—",
            "live 天数": n, "折合自然年": round(n / 252, 1),
            "环2 调仓场数": n_rebal, "名义覆盖格数": n_rebal * hold,
            f"逐日序列 ≥ {roll} 天?": "够" if n >= roll else "不够",
            f"现档 {folds} 折 ⇒ 段长": f"{n // folds} 天" if n >= folds else "铺不满"})
    st = pd.DataFrame(srows)
    st.to_csv(f"{OUT}/start_ladder.csv", index=False)
    print(st.to_string(index=False))
    check("S1", "起点梯子的生产三档必须都在表里且天数互不相同（三条链真的不同钟）",
          set([C.ASHARE_EVAL_START, C.ASHARE_RED_START, C.ASHARE_PORT_START]) <= set(st["起点"])
          and st[st["起点"].isin([C.ASHARE_EVAL_START, C.ASHARE_PORT_START])]["live 天数"].nunique() == 2,
          f"2010={int(st.loc[st['起点']=='2010-01-01','live 天数'].iloc[0])} 天 / "
          f"2015={int(st.loc[st['起点']=='2015-01-01','live 天数'].iloc[0])} 天")
    tail = int((d >= "2015-01-01").sum()), int((d >= "2010-01-01").sum())
    check("S2", "牙：起点拨到面板末日之后 ⇒ 天数与场数必须都是 0（这列不是写死的）",
          int(st.loc[st["起点"] == "2099-01-01", "live 天数"].iloc[0]) == 0
          and int(st.loc[st["起点"] == "2099-01-01", "环2 调仓场数"].iloc[0]) == 0,
          f"2099 档 天数={int(st.loc[st['起点']=='2099-01-01','live 天数'].iloc[0])} "
          f"场数={int(st.loc[st['起点']=='2099-01-01','环2 调仓场数'].iloc[0])}")
    ar = st[st["起点"] == C.ASHARE_PORT_START]
    n_r, n_c = int(ar["环2 调仓场数"].iloc[0]), int(ar["名义覆盖格数"].iloc[0])
    check("S3", "算术自我对表：名义覆盖格数必须**恰好**等于场数×持有窗（这张表自己的账要平）",
          n_c == n_r * hold and n_r > 0, f"{n_r} 场 × {hold} = {n_c} 格")
    readout("S5 与环2 归档差几场（两条日历，不是同一把尺）",
            f"本表用环1 的 IC 轴（末日 {d.iloc[-1]}、{len(d)} 天）算出 {n_r} 场；"
            f"环2 归档自报 570 场 / excess_days 2,852（其轴是过闸面板、末日 2026-09-24）"
            f"⇒ 差 {570 - n_r} 场纯粹是**日历差**，不是判决差")
    readout("S4 换起点要不要重跑",
            f"2010→2015 少 {(1 - tail[0] / tail[1]) * 100:.0f}% 样本；拨起点＝换样本，"
            f"环1/环2 判决都要重跑才可比（环2 一场 ≈10 分钟/3.8GB、环1 ≈12 分钟）")
    return finish()


def finish():
    red = sorted(c[0] for c in CHECKS if not c[2])
    print(f"\n===== 汇总：{len(CHECKS) - len(red)}/{len(CHECKS)} 通过 =====")
    print(f"[RED_IDS] {'、'.join(red) if red else '无'}")
    pd.DataFrame(CHECKS, columns=["id", "label", "ok", "detail"]).to_csv(
        f"{OUT}/checks.csv", index=False)
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
