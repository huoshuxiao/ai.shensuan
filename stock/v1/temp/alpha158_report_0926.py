# -*- coding: utf-8 -*-
"""Alpha158 扫描的读数台（只读扫描产物 + 权威归档，不做任何计算重的重扫）

回答四个问题，全部从已落盘的 csv 里读：
1. **跑完了吗**：157 条里多少条出了 ok 行、哪些条 status 不是 ok（不可求值/全 NaN/样本不足）。
2. **谁最强**：按全样本截面 Rank IC 的**绝对值**排序（这批因子 IC 有正有负，绝对值才是
   「排得多准」；方向要单独念，所以两列都印）。
3. **Alpha158 里有没有比在库因子更强的**：把在库 21 条（环 1 归档 + ㉛ 的「稳不稳」表）
   与 157 条放进同一个池子排名，逐个给出在库因子的**名次**。这一条才是「要不要为此换池子」
   的依据 —— 注意它仍是**样本内**排名，157 条是按 qlib 的配方穷举出来的、没有任何一段
   留作样本外，所以名次本身有挑选偏差（跟 RD-Agent 在全样本上挑 21 条是同一类偏差）。
4. **两者是不是同一批信号**：逐日截面 IC 序列的 Spearman 相关 —— 若某条 Alpha158 与某个
   在库因子的 IC 序列相关高，它就不是新信息，只是换个写法。

顺带印成本账单（每批秒数、累计墙钟）。

口径提醒（打印在输出里，不重复在代码里下判断）：
- 扫描用本线 DSL 口径：**窗内含停牌缺值 ⇒ 该点 NaN**（qlib 是 min_periods=1 跳过缺值）
  ⇒ 停牌多的票上两边给的数不同；这是刻意的，见 `common/src/core/factor_dsl.py:20-26`。
- `VWAP0` 未扫（面板无 $vwap），所以分母是 157 不是 158。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
# 命令行第 1 个参数是目录后缀：全市场跑不带参数；看抽样产物传 `_sample19`
SCAN = os.path.join(HERE, "tmp_alpha158", "scan" + (sys.argv[1] if len(sys.argv) > 1 else ""))
RES = os.path.join(ROOT, "stock", "v1", "data", "results")
EVAL_CSV = os.path.join(RES, "ashare_factor_eval.csv")
STAB_CSV = os.path.join(RES, "ashare_rolling_ic.csv")   # 权威：在库 21 条的「稳不稳」
DAILY21_CSV = os.path.join(RES, "ashare_ic_daily.csv")
M = os.path.join(SCAN, "alpha158_metrics.csv")
S = os.path.join(SCAN, "alpha158_stability.csv")
COST = os.path.join(SCAN, "alpha158_batch_cost.csv")
DAILY_DIR = os.path.join(SCAN, "daily")

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)


def _mtime(p):
    return time_str(os.path.getmtime(p)) if os.path.exists(p) else "<不存在>"


def time_str(t):
    import time
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def load_scan():
    return pd.read_csv(M), pd.read_csv(S)


def main():
    for p in (M, S):
        if not os.path.exists(p):
            print(f"[缺文件] {p} 还不存在 ⇒ 扫描没跑或目录不对")
            return 1
    if "_sample" in SCAN:
        print("⚠️⚠️ 读的是**抽样口径**目录（只扫了部分标的）⇒ 下面的 IC 不能与全市场口径的"
              "在库 21 条并排比。这条打印的存在就是为了防止把 smoke 的数当成结论。")
    met, stab = load_scan()
    ok = met[met["status"] == "ok"].copy()
    print(f"===== ① 完成度 =====")
    print(f"  metrics {len(met)} 行　ok {len(ok)} 条　非 ok {(met['status'] != 'ok').sum()} 条"
          f"　（应扫 157）")
    bad = met[met["status"] != "ok"]
    if len(bad):
        print("  " + "　".join(f"{r['name']}={r['status']}" for _, r in bad.iterrows()))
    if len(stab):
        so = stab[stab["status"] == "ok"] if "status" in stab else stab
        print(f"  stability {len(stab)} 行　出读数 {len(so)} 条")

    print(f"\n===== ② 按 |全样本截面 Rank IC| 排名（前 20）=====")
    ok["abs_ic"] = ok["cs_rank_ic_mean"].abs()
    cols = ["name", "cs_rank_ic_mean", "cs_rank_icir", "cs_ic_mean", "ts_ic_mean", "n_days"]
    top = ok.sort_values("abs_ic", ascending=False).head(20)
    print(top[cols].to_string(index=False))

    fam = {r["name"]: r["family"] for r in
           json.load(open(os.path.join(HERE, "tmp_alpha158", "alpha158_exprs_0926.json"),
                          encoding="utf-8"))}
    ok["family"] = ok["name"].map(fam)
    g = ok.assign(a=ok["abs_ic"]).groupby("family")["a"].agg(["max", "median", "count"])
    g.columns = ["族内最强|IC|", "族内中位|IC|", "条数"]
    print("\n===== ②″ 按族看（哪一族整体强、哪一族只是凑数）=====")
    print(g.sort_values("族内最强|IC|", ascending=False).to_string())

    print(f"\n===== ②′ 上面这批前 12 名的「稳不稳」读数（同一次扫描顺手出的，㉛ 那台秤）=====")
    scols = ["name", "n_days", "roll_same_pct", "year_same_pct", "lo_ic_year", "lo_ic",
             "hi_ic_year", "hi_ic", "fold_last", "recent_ic", "drift_ratio", "slope_per_yr"]
    head12 = ok.sort_values("abs_ic", ascending=False).head(12)["name"]
    print(stab[stab["status"] == "ok"].set_index("name").loc[head12]
          [[c for c in scols if c != "name"]].to_string())
    lats = pd.read_csv(STAB_CSV) if os.path.exists(STAB_CSV) else None
    if lats is not None:
        l12 = lats[lats["status"] == "ok"].copy()
        # 按绝对值取前 12：这批 IC 多为负，用 nlargest 会挑到「最不打用」的那几条
        l12["abs_ic"] = l12["rank_ic_full"].abs()
        l12 = l12.nlargest(12, "abs_ic")
        print("  对照：在库 21 条里 |rank IC| 前 12 的同名读数（同一台秤、同一版面板）")
        print(l12[[c for c in scols if c in l12.columns]].to_string(index=False))

    print(f"\n===== ③ 与在库 21 条同池排名（在库因子的名次）=====")
    if not os.path.exists(EVAL_CSV):
        print("  没有环 1 归档可比")
        return 0
    ref = pd.read_csv(EVAL_CSV)
    ref = ref[ref.get("status", "ok") == "ok"] if "status" in ref else ref
    print(f"  环 1 归档落盘 {_mtime(EVAL_CSV)}　"
          f"（面板落盘 {_mtime(os.path.join(RES, 'rdagent_output'))}）")
    pool = pd.concat([
        ok[["name", "cs_rank_ic_mean"]].assign(src="alpha158"),
        ref[["name", "cs_rank_ic_mean"]].assign(src="在库")], ignore_index=True)
    pool["abs_ic"] = pool["cs_rank_ic_mean"].abs()
    pool = pool.sort_values("abs_ic", ascending=False).reset_index(drop=True)
    pool["rank"] = np.arange(1, len(pool) + 1)
    lib = pool[pool["src"] == "在库"][["rank", "name", "cs_rank_ic_mean"]]
    print(f"  合并 {len(pool)} 条一起排（含符号取绝对值）。在库 21 条的名次：")
    print(lib.to_string(index=False))
    best158 = pool[pool["src"] == "alpha158"].head(10)
    print(f"  Alpha158 挤进前十的：")
    print(best158[["rank", "name", "cs_rank_ic_mean"]].to_string(index=False))

    print(f"\n===== ④ 与在库因子「逐日 IC 序列」的 Spearman 相关（找重复信号）=====")
    if not os.path.exists(DAILY21_CSV):
        print(f"  缺 {DAILY21_CSV} ⇒ 跳过")
    else:
        d21 = pd.read_csv(DAILY21_CSV, index_col=0)
        files = sorted(f for f in os.listdir(DAILY_DIR) if f.endswith(".csv"))
        frames = []
        for f in files:
            frames.append(pd.read_csv(os.path.join(DAILY_DIR, f), index_col=0))
        dscan = pd.concat(frames, axis=1) if frames else None
        if dscan is None:
            print("  扫描的逐日 IC 未落盘 ⇒ 跳过")
        else:
            rank21 = d21[[c for c in d21.columns if c.startswith("rank|")]]
            rank158 = dscan[[c for c in dscan.columns if c.startswith("rank|")]]
            common = rank21.index.intersection(rank158.index)
            a = rank21.loc[common].apply(pd.to_numeric, errors="coerce")
            b = rank158.loc[common].apply(pd.to_numeric, errors="coerce")
            # 成对 Spearman = 先转秩再按**成对完整**样本算 Pearson。逐对配 mask 而不是
            # 一次矩阵乘：两个因子各自的「日历空洞」（那天截面股票不足 ⇒ 没有 IC）不重合，
            # 整体 dropna 会把表砍到几百天。
            ar, br = a.rank().to_numpy(), b.rank().to_numpy()
            rows, nocmp = [], []
            for j, cb in enumerate(b.columns):
                bj = br[:, j]
                best_r, best_i = 0.0, None
                for i in range(ar.shape[1]):
                    ca = ar[:, i]
                    m = ~(np.isnan(bj) | np.isnan(ca))
                    if m.sum() < 100 or np.std(ca[m]) == 0 or np.std(bj[m]) == 0:
                        continue
                    r = float(np.corrcoef(ca[m], bj[m])[0, 1])
                    if abs(r) > abs(best_r):
                        best_r, best_i = r, i
                if best_i is None:
                    nocmp.append(cb[5:])
                    continue
                rows.append((cb[5:], a.columns[best_i][5:], best_r))
            dup = pd.DataFrame(rows, columns=["alpha158 因子", "最像的在库因子", "IC 序列相关"])
            dup["|相关|"] = dup["IC 序列相关"].abs()
            print(f"  共同交易日 {len(common)} 天（{common.min()} ~ {common.max()}）")
            print("  与在库某条最像的前 12：")
            print(dup.sort_values("|相关|", ascending=False).head(12)
                  .drop(columns="|相关|").to_string(index=False))
            print("  最不像（信息增量最大的前 8）：")
            print(dup.sort_values("|相关|").head(8).drop(columns="|相关|").to_string(index=False))

            print("\n===== ④′ 前 12 名各自「最像在库哪一条」（决定它是新信息还是换皮）=====")
            t12 = list(ok.sort_values("abs_ic", ascending=False).head(12)["name"])
            sub = dup[dup["alpha158 因子"].isin(t12)].copy()
            sub["名次"] = sub["alpha158 因子"].map({n: i + 1 for i, n in enumerate(t12)})
            print(sub.sort_values("名次")[["名次", "alpha158 因子", "最像的在库因子",
                                           "IC 序列相关"]].to_string(index=False))
            if nocmp:
                print(f"  ⚠️ {len(nocmp)} 条与在库任何一条都配不满 100 个共同交易日 ⇒ 不比："
                      f"{'、'.join(nocmp[:12])}")

    print(f"\n===== ⑤ 成本账单 =====")
    if os.path.exists(COST):
        c = pd.read_csv(COST)
        print(f"  {len(c)} 批、{int(c['n_expr'].sum())} 条、合计 {c['scan_s'].sum():.0f}s "
              f"= {c['scan_s'].sum()/3600:.2f}h　峰值 RSS 最高 {c['peak_rss_gb'].max()}GB")
        print(c[["batch", "n_expr", "scan_s", "per_expr_s", "peak_rss_gb", "ts"]].to_string(index=False))
        print("  注：`load_panel` 每批另付 9~13s（不计入 scan_s），"
              "每行 scan_s 是**一批 8 条合扫**的墙钟（不是逐条），逐条单价见 ㉝ 的探针表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
