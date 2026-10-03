# -*- coding: utf-8 -*-
"""B4「新场次该多一行」这条判据有没有牙（09-30）

来历：09-30 把 09-29 接进 bin 之后第一次用「新的一天」形态跑重跑链，B4 按老写法要求
「旧行逐字未动」⇒ 当场判红。真差在哪：⑤ 每次把**整张面板**重算一遍历史，而 ② 每天全量
重写 daily_pv.h5 ⇒ 同一格的浮点重加顺序变了。09-28 那行实测只有两列动了、且只动末位：
    域等权日收益  -0.010384252294898033 → -0.010384252294898   |Δ|=3.3e-17（19 个 ULP）
    市场宽度       0.26131134969325154  → 0.2613113496932515    |Δ|=5.6e-17（ 1 个 ULP）
其余 16 列（日期/持仓/段内第几天/换仓费/满仓净值/四档建议E/四档生效E/备注）逐字节相同。
⇒ 判据改成两层：文本列逐字、数值列容末位（相对差 ≤1e-9）。

放宽一条尺子必须同时交付它的牙，否则「容噪声」会退化成「什么都容」：
    甲 放行 = 真数据 + **真 ULP 噪声**（拿 struct 把末位翻一次，形状与 09-30 实测一致）
    乙 放行 = 纯 +1 行、旧行一字不动
    丙~己 判红 = 换持仓 / 改段内第几天 / 改备注 / 数值列挪 1e-8
    庚 判红 = 多两行
甲与己是一对**卡着阈值的夹逼**：2e-16 必须放行、1e-8 必须判红 —— 只有两条都跑过，
才说得上「1e-9 这条线画在了噪声与真改动之间」，而不是一句注释。

行形全部来自生产账本真实行（读进来 → 改一格 → csv.writer 写回），不手写。
"""
import csv
import io
import os
import struct
import sys

REPO = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
sys.path.insert(0, os.path.join(REPO, "stock", "v1", "src"))
LEDGER = os.path.join(REPO, "stock/v1/data/results/exposure_forward.csv")

from run_ashare_rerun_chain import ledger_rows_check, LEDGER_NUM_TOL   # noqa: E402


def read_lines(path):
    return [ln for ln in open(path).read().splitlines() if ln.strip()]


def to_lines(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().splitlines()


def ulp(x, k=1):
    """把 float64 往任一方向挪 k 个末位（ULP）—— 造「重加顺序变了」那一档噪声用的真尺子"""
    bits = struct.unpack("<q", struct.pack("<d", x))[0]
    return struct.unpack("<d", struct.pack("<q", bits + k))[0]


def mutate(rows, i, col, new_text, hdr):
    out = [list(r) for r in rows]
    k = hdr.index(col)
    old = out[i][k]
    out[i][k] = new_text
    assert old != out[i][k], f"注入没落地（{col} 两边都是 {old!r}）⇒ 这一格是恒真的"
    return out


def main():
    lines = read_lines(LEDGER)
    hdr = next(csv.reader([lines[0]]))
    rows = list(csv.reader(lines[1:]))
    if len(rows) < 2:
        raise SystemExit(f"生产账本只有 {len(rows)} 行，凑不出「旧行 + 新增一行」的真夹具 ⇒ 先跑一场日更")
    base = rows[:-1]          # 起表前磁盘上那份（旧行）
    live = rows               # 本场落盘（多一行）
    # 拿 09-30 实测「动了的那一列」当噪声夹具：域等权日收益（漂移 19 个 ULP、相对 3.2e-15）
    NUM = "域等权日收益"
    fails = []

    # 甲：旧行带**真 ULP 噪声**（末位翻 19 次，与实测同量级）⇒ 必须放行
    noisy = [list(r) for r in base]
    k = hdr.index(NUM)
    noisy[0][k] = repr(ulp(float(base[0][k]), 19))
    ok, msg = ledger_rows_check(to_lines(noisy, hdr), to_lines(live, hdr))
    rel_noise = abs(float(noisy[0][k]) - float(live[-2][k])) / abs(float(live[-2][k]))
    fails.append(("甲 放行：旧行带 19 个 ULP 噪声", ok,
                  f"注入后相对差 {rel_noise:.1e}　{msg}"))

    # 乙：纯 +1 行、旧行一字不动 ⇒ 必须放行
    ok, msg = ledger_rows_check(to_lines(base, hdr), to_lines(live, hdr))
    fails.append(("乙 放行：恰好 +1 行、旧行逐字未动", ok, msg))

    # 丙~己：四种真改动 ⇒ 必须判红
    red_cases = [
        ("丙 判红：旧行持仓换一只票", mutate(base, 0, "持仓",
                                        base[0][hdr.index("持仓")].replace("SZ002032", "SZ000001"), hdr)),
        ("丁 判红：旧行段内第几天挪一格", mutate(base, 0, "段内第几天",
                                        str(int(base[0][hdr.index("段内第几天")]) + 7), hdr)),
        ("戊 判红：旧行备注改一个字", mutate(base, 0, "备注", base[0][hdr.index("备注")] + "。", hdr)),
        ("己 判红：旧行数值列挪 1e-8（超容差）",
         mutate(base, 0, NUM, repr(float(live[-2][hdr.index(NUM)]) * (1 + 5e-8)), hdr)),
    ]
    for name, mutated in red_cases:
        ok, msg = ledger_rows_check(to_lines(mutated, hdr), to_lines(live, hdr))
        fails.append((name, not ok, ("仍判红 ✅　" if not ok else "❌ 被放行 ⇒ 尺子没牙了：") + msg))

    # 庚：多两行 ⇒ 判红
    ok, msg = ledger_rows_check(to_lines(base, hdr), to_lines(live + [list(live[-1])], hdr))
    fails.append(("庚 判红：账本多了两行", not ok, ("仍判红 ✅　" if not ok else "❌ 被放行：") + msg))

    print(f"容差 LEDGER_NUM_TOL = {LEDGER_NUM_TOL:.0e}　噪声档注入 {rel_noise:.1e}（该放行）"
          f"　真改动档注入 5e-8（该判红）")
    print("──────── 逐格 ────────")
    bad = 0
    for name, got, msg in fails:
        print(f"  {'✅' if got else '❌'} {name}　{msg}")
        if not got:
            bad += 1
    if bad:
        raise SystemExit(f"[夹具红] {bad}/{len(fails)} 格不合预期 ⇒ B4 这条尺子不能引")
    print(f"\n[B4 夹具 {len(fails)} 格全绿] 放行 {sum(1 for n, *_ in fails if n.startswith(('甲', '乙')))} "
          f"· 判红 {sum(1 for n, *_ in fails if n.startswith(('丙', '丁', '戊', '己', '庚')))}"
          f"　噪声档放行、1e-8 档判红 ⇒ 阈值夹在中间，两头都验过")


if __name__ == "__main__":
    main()
