# -*- coding: utf-8 -*-
"""重跑日更：把「已经在库里的那一场」按链路顺序再走一遍，跑完逐块盘点 + 逐字节对表（09-29）

为什么需要另一个入口（`run_ashare_daily_chain.py` 买不到这件事）
    那条链的 ① 干的是「往 bin 里贴**还没进库的那一天**」。而「重跑」的意思是
    「bin 末格已经有 09-28 了，我要拿它把后面几步再走一遍」⇒ 链路自己算出的该补场次是
    **下一场**（09-29）：
      * 15:00 之前起它 ⇒ 抓不到那一场的收盘行情 ⇒ ① 非零退出 ⇒ `run()` 一非零就整链停
        ⇒ **②③④⑤ 一步都不跑**（09-29 实测：整条链停在第一步，不是"跳过 ① 继续"）
      * 15:00 之后起它 ⇒ 它真会往库里贴新的一天，那就不再是"重跑"而是"往前更一天"
    ⇒ 所以重跑必须**钉场次 = bin 末格**，并且由脚本自己说清「① 这一刻为什么不起」。

两档（默认安全，`--from-bin` 才碰数据）
    默认      跳过 ①，只走 ②面板 → ③名单 → ④次日真账 → ⑤仓位账本。bin 一个字节不动，
              跑完核「① 确实没动」（day.txt 的 mtime 与末格都没变）。
    --from-bin 先调 ① 自带的 `--rollback` 把 bin 末格那一场**撤掉**（削每只票的尾格 +
              还原 day.txt/all.txt），再**从 ① 起重走五步**。这一档才是"整条链从零再算一遍"：
                - 前置：`<快照目录>/bin_backup_<场次>/` 必须在（rollback 还原 all.txt 只认它）
                - 起表前把**整份 provider**（本线 849MB）`cp -a` 走，跑完逐字节 `diff -r` 对表
                - 外部快照 `spot_<场次>.csv` **保留不动**：① 会复用缓存（复用前要过
                  `align_against_bin` 对表）⇒ 这样"重跑链路"和"重取行情"是两件事，
                  对表才只证前者。真要连行情一起重取，就自己先删那份缓存，本脚本不替你删。

产出七块验收（任一红 ⇒ exit 1 并指名丢在哪一块，这就是"防止脚本单独运行时链路块丢失"）
    B0 ① 与本次模式一致：默认档 = 没动字节；--from-bin = 动了字节且末格回到本场
    B1 ② 面板：要么自报 `wrote` 要么自报 `reuse`，且 h5 的 mtime 不早于 day.txt
    B2 ③ 名单：四份产物都在、且比本次起表新；meta 四项（场次/面板末格/闸门档/排序轴）与进程口径一致
    B3 ④ 审计：exit 0，且它对本场的**独立复算**与 ③ 自报的漏斗逐格对上
    B4 ⑤ 仓位账本：本场写过、行数不涨（同一场只覆盖自己那一行）；新场次则要求恰好 +1 行
              且旧行没被重算 —— 见下面「第三种形态」
    B5 逐字节对表：三份名单 + 仓位账本与起表前那份全等，meta 除墙钟格 `industry.age_days` 外全等
       ⇒ B5 是本脚本的正对照所在：同一份数据、同一套判据，重算必须给出同一个字节。
          它红了说明重跑不可复现（面板不是同一版、或某步偷偷读了墙钟/外部数据），那才是需要人看的事。
       ⚠️ 基准是「本场起表前磁盘上那份」：隔天再跑时那份已经包含昨天重跑的输出 ⇒ B5 绿只证**可复现**，
          不证「与生产归档同版」（要后者得自己指一份生产期的归档当基准）。
    B6 （仅 --from-bin）整份 provider 与起表前的拷贝 `diff -r` 零差异 + 记账表逐字节相同
       ⇒ 这一条把"bin 能被链路自己重建成同一个形状"说死，而不是只验下游。
       ⚠️ 唯一放行的形态（09-29 实测发现、用户裁「三」由 `b6_judge` 核验后放行）：当天有**复牌**的票
       ⇒ rollback 只砍"超出保留日历"的格，① 为停牌补的那格 NaN 占位落在保留区间里被留了下来
       ⇒ 再贴时 `write_day` 算出"要补 0 格"，那条「复牌首日涨跌幅跨空档、留 NaN」
       （`data/update_qlib_bin_daily.py:540`）认不出来 ⇒ `features/<票>/change.day.bin` 末格由 NaN
       变成真实涨幅。放行要三条同时成立（差字段名/差方向/差出不止一格，任一条不过就照红）：
       只许 `change.day.bin`、只许「NaN→真实涨幅」这一个方向、除末格外其余字节必须全等。
       这型翻不动任何判据：`$change` 压根不进面板（`pregen_source_data.py:39` 只取六列），
       下游五份产物仍与生产逐字节相同（B5 绿就是证据）。记账表也看不出来——那两格在 `cell_at`
       眼里都是同一个 None。

**恒真的坑（两条都在这脚本里踩过，改它之前先读）**
    ⑤ 是"非零只打 ⚠️、不挡链路"的那一步 ⇒ 只看「行数没涨 + 场次在场」时，「⑤ 压根起不来、
      文件原封不动」同样满足这两条，会绿得毫无信息量 ⇒ 必须补「账本 mtime 晚于本次起表」。
    ④ 一次跑**三场**（09-23/24/28），只有被重跑那一场没有 T+1 快照 ⇒ 拿整段 stdout 里
      有没有那句 `[无 T+1 快照]` 当结论会说谎（读数没错、归属错了）⇒ 先按场次切片再读。

第三种形态：库里**还没有**的这一场（09-30 接进 09-29 那一格之后第一次撞上）
    前面两档验的都是「已经在库里的一天再算一遍」，B5 的基准 = 起表前磁盘上那份。
    但「往库里接了新的一天，再用本脚本把 ②③④⑤ 走一遍」时，起表前压根没有
    `signal_20260929.csv` / `meta_20260929.json` 这些产物 ⇒
      * B5 那几块**没有基准**：按老写法会把「产物是新的」报成「重跑不可复现」（4 条假红），
        而 meta 那一行会直接 `FileNotFoundError` 崩在显示行前面，把整张盘点表丢掉。
      * B4 的账本按老写法「行数不涨」也是假红 —— 新场次**该**多出一行。
    ⇒ 起表前先问一次「meta 在不在」，据此把这几块换成新场次的判据：
      B4 = 恰好 +1 行**且旧行没被重算**：文本列（日期/持仓/段内第几天/备注）逐字不动，
           数值列只容浮点末位噪声（相对差 ≤1e-9；实测 ② 全量重写面板会让 域等权日收益/市场宽度
           翻 1~19 个 ULP = 相对 ≤3.2e-15，那是重加顺序变了、不是数据变了）
           —— 有牙：多两行、少一行、换持仓、改建议E、数值挪 1e-8 都判红；
      B5 的产物四块与账本 = ⚪（不适用，什么都没证，既不算红也不算绿）；B0–B3 照常有效。
    ⚠️ 这一档**证不了可复现**（没有第二份可比）。要把新场次也变成可复现的，跑完再空跑一次
      默认档即可：那时起表前已有产物 ⇒ B5 全场逐字节对表有效。

用法
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_rerun_chain.py --dry-run      # 体检+备份，四步都不起
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_rerun_chain.py                # 跳过 ①，重跑 ②③④⑤
    cd stock/v1/src && /usr/bin/python3.10 run_ashare_rerun_chain.py --from-bin     # 撤掉那一格，五步全走
    # 实测代价（09-29，09-28 那场）：默认档整轮 223~267s（② 冷 79s/热 49s、③ 66~67s、④ ~55s、⑤ 37~41s），
    # 每次备份 754MB（含 0.79GB 面板）；--from-bin 另加 provider 拷贝 849MB + rollback + ① append + diff -r。
"""
import _bootstrap  # noqa: F401  (必须最先导入：裸模块名导入的 sys.path 引导)

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time

# 单点复用：五步的命令、环境变量、场次判据、取消闸都从链路模块本身拿，这里不抄第二份
import run_ashare_daily_chain as C
from ashare_screen import BUY_EXPR
from config import ASHARE_SNAPSHOT_DIR

# ② 全量重写面板 + ③⑤ 各 2GB 上下：低于这个可用内存就不启动，让人先去停占用者
MEM_FLOOR_GIB = 4.0
BACKUP_ROOT = os.path.join(C.REPO, "stock", "v1", "temp", "rerun_backups")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def mem_available_gib():
    for ln in open("/proc/meminfo"):
        if ln.startswith("MemAvailable:"):
            return int(ln.split()[1]) / 1024 / 1024
    raise SystemExit("[起前体检] /proc/meminfo 里没有 MemAvailable ⇒ 判不出内存，不启动")


def top_consumers():
    out = subprocess.run(["ps", "-eo", "pid,rss,args", "--sort=-rss"],
                         stdout=subprocess.PIPE, text=True).stdout.splitlines()
    keep = [ln for ln in out[1:] if any(k in ln for k in
            ("python", "conda", "llama", "ollama", "docker", "node"))][:8]
    return "\n".join("  " + ln.strip()[:150] for ln in keep) or "  （没认出谁在吃内存）"


def leaf(p):
    """日志里只报文件名：整条绝对路径会把读数表挤歪"""
    return os.path.basename(os.path.abspath(p))


def bin_last_cell(path):
    """读 qlib `<field>.day.bin` 的末格：整个文件是 float32 数组、[0] 是起始日历下标"""
    with open(path, "rb") as fh:
        fh.seek(-4, os.SEEK_END)
        return struct.unpack("<f", fh.read(4))[0]


def bin_body_md5(path):
    """除末格（最后 4 字节）以外全部字节的 md5 ⇒ 用来把差异钉死在「只有最后一格」上"""
    n = os.path.getsize(path)
    h = hashlib.md5()
    if n > 4:
        with open(path, "rb") as fh:
            h.update(fh.read(n - 4))
    return h.hexdigest()


def b6_judge(diff_lines, provider, prov_bak):
    """把 `diff -rq` 的差异行分成「已核验的复牌末格」与「其余」（其余非空 ⇒ B6 红、exit 1）

    放行要三条**同时**成立，少一条就退回红 —— 否则这条判据就退化成「见到 change 就放」的恒真：
      1) 只许 `features/<代码>/change.day.bin`（别的字段、别的目录、`Only in` 一律不算）
      2) 只许「起表前 NaN → 本场真实涨幅」这一个方向（反过来 = 链路把数据弄丢了，那是事故）
      3) 两份文件除末格外**逐字节相同**（出不止一格的差 ⇒ 不是复牌那条规则能解释的）
    → (放行清单[人话], 其余差异原始行)
    """
    waived, other = [], []
    for ln in diff_lines:
        m = re.match(r"^Files (.+) and (.+) differ$", ln.strip())
        if not m:
            other.append(ln)                      # "Only in ..." 之类：多出来/缺了文件，照红
            continue
        live, bak = m.group(1), m.group(2)
        rel_live = os.path.relpath(live, provider)
        rel_bak = os.path.relpath(bak, prov_bak)
        if rel_live != rel_bak or not re.fullmatch(r"features/[0-9a-z]+/change\.day\.bin", rel_live):
            other.append(ln)                      # 字段/形状不对，或两边不是同一个相对路径
            continue
        if os.path.getsize(live) != os.path.getsize(bak) or bin_body_md5(live) != bin_body_md5(bak):
            other.append(ln + "　(除末格外还有别的字节不同，不是复牌那一型)")
            continue
        v_live, v_bak = bin_last_cell(live), bin_last_cell(bak)
        if not (v_bak != v_bak and v_live == v_live):   # 只放行 NaN→真实涨幅（NaN 与自己不等）
            other.append(ln + f"　(末格 {v_bak} → {v_live}，方向不是 NaN→真实涨幅)")
            continue
        waived.append(f"{rel_live.split('/')[1]} 末格 NaN→{v_live:+.2%}")
    return waived, other


def meta_diff(a, b):
    """两份 meta 的差集。显式放行墙钟格 industry.age_days（它 = 现在时刻 − 面板 mtime，跨场次必变）"""
    def flat(m, p=""):
        out = {}
        for k, v in m.items():
            if isinstance(v, dict):
                out.update(flat(v, p + k + "."))
            else:
                out[p + k] = v
        return out
    fa, fb = flat(a), flat(b)
    return [f"{k}: {fa.get(k, '<无此键>')!r} ≠ {fb.get(k, '<无此键>')!r}"
            for k in sorted(set(fa) | set(fb))
            if k != "industry.age_days" and fa.get(k, "<无此键>") != fb.get(k, "<无此键>")]


# ⑤ 账本在新场次的判据用得上：文本列逐字、数值列容末位噪声
LEDGER_TEXT_COLS = ["日期", "持仓", "段内第几天", "备注"]
LEDGER_NUM_TOL = 1e-9       # 数值列相对差上限：比实测 ULP 尾巴(3e-15)宽 6 个数量级、比任何真改动窄


def ledger_rows_check(lines_bak, lines_now):
    """新场次接进账本：恰好 +1 行，且旧行不许被改写 → (ok, 读数)

    为什么旧行不能拿「整行逐字相同」当判据（09-30 实测）：域等权日收益、市场宽度这两列
    是把域内 5,000+ 只票的日收益加一遍再除，而 ② 每天**全量重写**面板 ⇒ 同一格的重加顺序
    变了，末位就翻。09-28 那行实测 |Δ| = 5.6e-17（市场宽度 1 个 ULP、域等权日收益 19 个
    ULP，相对差 ≤3.2e-15）—— 浮点尾巴，不是数据变了。
    ⇒ 分两层验：**文本列**（日期/持仓/段内第几天/备注）逐字不动，它们一动就是「历史被重算成
       另一场决策」；**数值列**只许末位噪声，超容差即红。
    """
    hdr = next(csv.reader([lines_bak[0]]))
    miss = [k for k in LEDGER_TEXT_COLS if k not in hdr]
    if miss:
        return False, f"账本表头缺列 {miss} ⇒ 判据无法定位文本列"
    t_cols = {hdr.index(k) for k in LEDGER_TEXT_COLS}
    rows_bak = [r for r in csv.reader([ln for ln in lines_bak[1:] if ln.strip()])]
    rows_now = [r for r in csv.reader([ln for ln in lines_now[1:] if ln.strip()])]
    grew = len(rows_now) - len(rows_bak)
    text_bad, max_rel, worst_col = [], 0.0, ""
    for o, n in zip(rows_bak, rows_now):
        if len(o) != len(n):
            text_bad.append(f"{o[0]}(列数 {len(o)}→{len(n)})")
            continue
        for i, (va, vb) in enumerate(zip(o, n)):
            if i in t_cols:
                if va != vb:
                    text_bad.append(f"{o[0]}.{hdr[i]}")
                continue
            try:
                fa, fb = float(va), float(vb)
            except ValueError:
                if va != vb:
                    text_bad.append(f"{o[0]}.{hdr[i]}")
                continue
            rel = abs(fa - fb) / max(abs(fa), abs(fb), 1e-300)
            if rel > max_rel:
                max_rel, worst_col = rel, f"{o[0]}.{hdr[i]}"
    ok = grew == 1 and not text_bad and max_rel <= LEDGER_NUM_TOL
    return ok, (f"实涨 {grew} 行（要求恰好 +1）　旧行 {len(rows_bak)} 行文本列"
                + ("逐字未动 ✅" if not text_bad else f"被改动 ❌ {'、'.join(text_bad[:4])}")
                + f"　数值列最大相对差 {max_rel:.1e}（{worst_col or '—'}，容差 {LEDGER_NUM_TOL:.0e}）"
                + ("✅ 只是浮点尾巴" if max_rel <= LEDGER_NUM_TOL else "❌ 超出末位噪声 ⇒ 历史被重算"))


def rollback_bin(session, dry=False):
    cmd = [C.P310, os.path.normpath(os.path.join(C.SRC, *[".."] * 3, "common", "src", "data", "stock", "update_qlib_bin_daily.py")), "--rollback"]
    if dry:
        cmd.append("--dry-run")
    return C.run("① 撤销 bin 末格那一场（--rollback）", cmd)


def main():
    ap = argparse.ArgumentParser(
        description="重跑日更：默认跳过 ① 走 ②③④⑤；--from-bin 撤掉那一格后从 ① 起整条链")
    ap.add_argument("--session", default=None, help="YYYY-MM-DD（默认取 bin 日历末格）")
    ap.add_argument("--from-bin", action="store_true",
                    help="先 --rollback 撤掉 bin 末格那一场，再从 ① 起重走五步（会动数据，动前整份拷走 provider）")
    ap.add_argument("--dry-run", action="store_true", help="只做体检与备份，一步都不起")
    a = ap.parse_args()

    t_start = time.time()
    cal_end = C.calendar_end()
    if not cal_end:
        raise SystemExit(f"[起前体检] 读不到 {C.DAY_TXT} ⇒ bin 日历是空的，没什么可重跑的")
    session = (a.session or cal_end).strip()
    digits = session.replace("-", "")
    if session != cal_end:
        raise SystemExit(
            f"[起前体检] 要重跑 {session}，但 bin 日历末格是 {cal_end}。\n"
            f"  比它新 ⇒ 那一格还没进库，本脚本不替 ① 贴新格（--from-bin 也只撤末格）；\n"
            f"  比它旧 ⇒ 面板末格已是 {cal_end}，③⑤ 读的是整张面板，重跑出来的名单不是那天的。")

    # —— 为什么默认跳过 ①：把链路自己的判据跑一遍给人看，不靠注释嘴说 ——
    pending, advance, unknown = C.decide_session(cal_end)
    print(f"[起前体检] bin 日历末格 = {cal_end}　今天 = {time.strftime('%Y-%m-%d %H:%M')}　"
          f"provider = {C.RDAGENT_QLIB_PROVIDER}")
    print(f"[起前体检] 交易所日历里 {cal_end} 的下一场 = {pending or '取不到（' + unknown + '）'}"
          f"　链路会判定 ① {'**该补**' if advance else '不起'}")
    if a.from_bin:
        print("[模式 --from-bin] 先撤掉 " + cal_end + " 那一格（rollback），再起 ① 把它贴回去、"
              "走完 ②③④⑤ ⇒ 五步全是本场重算。外部快照 spot_" + digits + ".csv 保留不动："
              "① 复用缓存（复用前过 align_against_bin 对表），这样对表只证「链路可重建」，不掺进「行情重取」。")
        bdir = os.path.join(ASHARE_SNAPSHOT_DIR, f"bin_backup_{digits}")
        if not os.path.isdir(bdir):
            raise SystemExit(f"[起前体检] 缺 {bdir} ⇒ rollback 还原不了 instruments/all.txt"
                             "（END 改动与新入表行按下标反推不出来），不换数据就别 --from-bin。")
    else:
        print("[跳过 ①] 本脚本重跑的是**已经在库里**的 " + cal_end + "，① 要补的是 " + str(pending)
              + " 那一格：现在" + time.strftime("是 %H:%M，") + "起了 ① 就是让整链停在第一步、"
              "②③④⑤ 一步不跑（要五步全走就带 --from-bin）。⇒ B0 会核「① 确实一个字节都没动」。")

    avail = mem_available_gib()
    if avail < MEM_FLOOR_GIB:
        raise SystemExit(
            f"[起前体检] 可用内存 {avail:.1f}GiB < {MEM_FLOOR_GIB}GiB ⇒ **不启动**（②③⑤ 峰值 2GB 以上，"
            f"不够就是拿整条链去赌）。现在在吃内存的进程：\n{top_consumers()}\n"
            "要跑先去停占用者，或等它跑完再重跑本脚本。")
    print(f"[起前体检] 可用内存 {avail:.1f}GiB ≥ {MEM_FLOOR_GIB}GiB ✅")

    # —— 覆写面：先说清楚要动哪些文件，再动手 ——
    ledger = os.path.abspath(os.path.join(os.path.dirname(C.ASHARE_SIGNAL_DIR),
                                          "exposure_forward.csv"))
    products = [os.path.join(C.ASHARE_SIGNAL_DIR, f"{k}_{digits}.{e}")
                for k, e in (("signal", "csv"), ("buy", "csv"), ("order", "csv"), ("meta", "json"))]
    snap = [os.path.join(ASHARE_SNAPSHOT_DIR, f"spot_{digits}.csv"),
            os.path.join(ASHARE_SNAPSHOT_DIR, f"append_{digits}.csv")]
    targets = [os.path.abspath(t) for t in products + [ledger] + snap if os.path.exists(t)]
    # 本场是「已在库里的一天再算一遍」还是「库里还没有的一天」？必须在动手之前问一次：
    # 后一种形态下 B5 的基准（起表前磁盘上那份）压根不存在，拿它当尺子会把「产物是新的」
    # 量成「重跑不可复现」。09-30 接 09-29 那一格之后第一次撞上。
    new_session = not os.path.exists(products[3])
    print(("[起前体检] 本场是**新的一天**（库里还没有 meta_%s.json）⇒ B5 那几块没有基准可比，"
           "只能报 ⚪；B4 的账本按「合理多一行」验。" % digits) if new_session else
          ("[起前体检] %s 那一场已在库里 ⇒ B5 全场逐字节对表有效。" % digits))
    print("[覆写面] 本档会重写这些文件（④ 源码里没有任何写出语句，它只往控制台打印）：")
    for t in targets + [C.H5]:
        exist = os.path.exists(t)
        mt = time.strftime("%F %T", time.localtime(os.path.getmtime(t))) if exist else "—不存在—"
        print(f"  {(f'{os.path.getsize(t) / 2 ** 20:.1f}MB') if exist else '—':>10}  {mt}  {t}")
    h5_before = dict(mtime=os.path.getmtime(C.H5), size=os.path.getsize(C.H5))
    day_txt_mtime = os.path.getmtime(C.DAY_TXT)

    bak = os.path.join(BACKUP_ROOT, f"rerun_{digits}_{time.strftime('%m%d_%H%M')}")
    os.makedirs(bak, exist_ok=True)
    for t in targets:
        shutil.copy2(t, os.path.join(bak, leaf(t)))
    shutil.copy2(C.H5, os.path.join(bak, "daily_pv.h5"))
    prov_bak = None
    if a.from_bin:
        prov_bak = os.path.join(bak, "cn_data")
        print(f"[备份 provider] {C.RDAGENT_QLIB_PROVIDER} → {prov_bak}（--from-bin 要动 bin，"
              "整份拷走是唯一的还原路径）")
        t_cp = time.time()
        subprocess.run(["cp", "-a", C.RDAGENT_QLIB_PROVIDER, prov_bak], check=True)
        print(f"[备份 provider] 完成，用时 {time.time() - t_cp:.0f}s")
    print(f"[备份] 产物已拷到 {bak}")

    if a.dry_run:
        print("[停在这里] --dry-run：体检与备份做完，五步一步都没起，生产路径一个字节没动。")
        return

    # —— ①（仅 --from-bin：先撤、再贴回去）——
    out1 = ""
    if a.from_bin:
        rollback_bin(session)
        if C.calendar_end() == cal_end:
            raise SystemExit("[链路中断] --rollback 之后日历末格没退回 ⇒ 那一格还在库里，"
                             "① 会自己判「日历已含该场次，无需再补」而什么都不写，B0 必红。停在这里不往下走。")
        out1 = C.run("① 当日快照 append 进 qlib bin",
                     [C.P310, os.path.normpath(os.path.join(C.SRC, *[".."] * 3, "common", "src", "data", "stock", "update_qlib_bin_daily.py"))])

    # —— ② 面板（rdagent conda 环境 + 必须带 QLIB_PROVIDER_URI）——
    conda = C.find_conda()
    if not conda:
        raise SystemExit(f"[② 起不来] 没找到 conda ⇒ 面板没法重生成。手工补："
                         f"QLIB_PROVIDER_URI={C.RDAGENT_QLIB_PROVIDER} conda run -n "
                         f"{C.RDAGENT_CONDA_ENV} python {C.PREGEN_PY} {C.RDAGENT_OUTPUT_DIR}")
    env = dict(os.environ, QLIB_PROVIDER_URI=C.RDAGENT_QLIB_PROVIDER)
    out2 = C.run("② 重生成 daily_pv.h5（rdagent conda 环境）",
                 [conda, "run", "--no-capture-output", "-n", C.RDAGENT_CONDA_ENV,
                  "python", C.PREGEN_PY, C.RDAGENT_OUTPUT_DIR], env=env)
    pregen_said = ("wrote" in out2, "reuse" in out2)

    # —— ③ 名单（生产路径，不带任何 STOCK_* 覆写）——
    C.run("③ 出当日名单 signal/buy/order",
          [C.P310, os.path.join(C.SRC, "run_ashare_daily_signal.py")])

    # —— ④ 次日真账（只读；不走 run() 的 raise，因为它多场次并列，要自己切片读数）——
    cmd4 = [C.P310, os.path.join(C.SRC, "run_ashare_daily_audit.py")]
    print("\n──────── ④ 次日真账（只读）────────\n$ " + " ".join(cmd4))
    r4 = subprocess.run(cmd4, cwd=C.SRC, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
    out4 = r4.stdout or ""
    print(out4.rstrip())
    print(f"[④] exit={r4.returncode}")

    # —— ⑤ 仓位层前向账本（不参与决策、非零只 ⚠️，故意沿用链路那一份实现）——
    class _A:
        no_exposure = False
    C.run_exposure(_A())

    # —— 逐块盘点 ——
    fails, report = [], []
    # ⚪ = 不适用：这块要拿「起表前磁盘上那份」当基准，而本场是新的一天、起表前压根没有那份
    #     ⇒ 既不能算红（没有东西可以复现），也不能算绿（它什么都没证）。09-30 接进 09-29
    #     那一格之后第一次撞上这个形态：那时 B5 按老写法会把「产物是新的」报成「重跑不可复现」，
    #     而 meta 那一行会直接 FileNotFoundError 崩在显示行前面，把整张盘点表丢掉。
    def chk(name, ok, detail, na=False):
        mark = "⚪" if na else ("✅" if ok else "❌")
        report.append(f"  {mark} {name}　{detail}")
        if not ok and not na:
            fails.append(name)

    moved = os.path.getmtime(C.DAY_TXT) != day_txt_mtime
    if a.from_bin:
        chk("B0 ① 动了字节", moved and C.calendar_end() == cal_end,
            f"day.txt mtime {'已更新' if moved else '未变 ❌'}、末格 {C.calendar_end()}"
            f"（应为 {cal_end}）")
    else:
        chk("B0 ① 没动字节", (not moved) and C.calendar_end() == cal_end,
            f"day.txt mtime {'未变' if not moved else '变了 ❌'}、末格仍 {C.calendar_end()}")

    h5_fresh = os.path.getmtime(C.H5) >= os.path.getmtime(C.DAY_TXT)
    chk("B1 ② 面板", any(pregen_said) and h5_fresh,
        f"自报 wrote={pregen_said[0]} reuse={pregen_said[1]}　"
        f"h5 {'本场重写' if os.path.getmtime(C.H5) > t_start - 1 else '未重写（复用旧面板）'}　"
        f"大小 {os.path.getsize(C.H5) / 2 ** 20:.1f}MB（原 {h5_before['size'] / 2 ** 20:.1f}MB）　"
        f"mtime 比日历{'新 ✅' if h5_fresh else '旧 ❌ 面板没跟着 bin 走'}")

    for t in products:
        chk(f"B2 ③ 产物 {leaf(t)}",
            os.path.exists(t) and os.path.getmtime(t) > t_start - 1,
            f"{os.path.getsize(t) / 2 ** 10:.1f}KB" if os.path.exists(t) else "不存在")
    meta_new = json.load(open(os.path.join(C.ASHARE_SIGNAL_DIR, f"meta_{digits}.json")))
    four = [("signal_date", str(meta_new["signal_date"]).replace("-", ""), digits),
            ("panel_end", str(meta_new["panel_end"]).replace("-", ""), digits),
            ("tradable_gate", meta_new.get("tradable_gate"), C.ASHARE_TRADABLE_GATE),
            ("buy.expr", meta_new["buy"]["expr"], BUY_EXPR)]
    chk("B2 ③ meta 四项", all(got == want for _, got, want in four),
        "　".join(f"{k}{'✅' if got == want else f'❌ {got!r}≠{want!r}'}" for k, got, want in four))

    tail4 = out4[out4.find(f"场次 {digits}"):] if f"场次 {digits}" in out4 else ""
    blk = re.search(r"\[复算\] n_step1=(\d+) n_traded=(\d+) n_chase=(\d+)"
                    r"　（生产 meta：(\d+)/(\d+)/(\d+)）", tail4, re.S)
    mt = meta_new["buy"]
    same = (blk is not None and
            (int(blk.group(1)), int(blk.group(2)), int(blk.group(3))) ==
            (int(blk.group(4)), int(blk.group(5)), int(blk.group(6))) ==
            (mt["n_step1"], mt["n_traded"], mt["n_chase"]))
    chk("B3 ④ 审计", r4.returncode == 0 and same,
        f"exit={r4.returncode}　本场复算 "
        + (f"{blk.group(1)}/{blk.group(2)}/{blk.group(3)}" if blk else "（④ 没打印这一场的 [复算] 行）")
        + f" vs ③ 自报 {mt['n_step1']}/{mt['n_traded']}/{mt['n_chase']}"
        + ("　✅ 逐格对上" if same else "　❌ 对不上 ⇒ 两步用的判据不是同一套")
        + ("　[T+1 快照] 次日两腿已进账" if tail4 and "[无 T+1 快照]" not in tail4
           else "　[无 T+1 快照] 次日两腿做不了，④ 改出空挡检验这一腿"))

    ledger_bak = os.path.join(bak, "exposure_forward.csv")
    lines_now = open(ledger).read().splitlines()
    rows = [ln.split(",")[0] for ln in lines_now[1:]]
    n_now = len(lines_now)
    touched = os.path.getmtime(ledger) > t_start - 1
    if new_session:
        # 库里还没有这一场的账本行 ⇒ 它**该**多出一行，多两行/多零行才是坏了；旧行由
        # ledger_rows_check 分两层验（文本列逐字 / 数值列容浮点末位噪声）
        if not os.path.exists(ledger_bak):
            chk("B4 ⑤ 账本", False, f"缺 {ledger_bak} ⇒ 无从判断多了几行")
        else:
            ok4, msg4 = ledger_rows_check(open(ledger_bak).read().splitlines(), lines_now)
            chk("B4 ⑤ 账本", ok4 and touched and cal_end in rows,
                f"本场写过={touched}　{msg4}　在场场次 {rows[-2:]}")
    else:
        n_bak = len(open(ledger_bak).read().splitlines())
        chk("B4 ⑤ 账本", n_now == n_bak and cal_end in rows and touched,
            f"本场写过={touched}　数据行 {n_now - 1}（备份 {n_bak - 1}）　在场场次 {rows}")

    for t in products[:3] + [ledger]:
        b = os.path.join(bak, leaf(t))
        if not os.path.exists(b):
            chk(f"B5 对表 {leaf(t)}", False,
                f"起表前没有这份产物（本场是新的一天 {digits}）⇒ 没有基准可比，这块什么都没证",
                na=True)
            continue
        if new_session and os.path.abspath(t) == ledger:
            chk(f"B5 对表 {leaf(t)}", False,
                "新场次必然多一行，逐字节相同这条在这儿不成立 —— 旧行没被重算 + 恰好 +1 由 B4 判",
                na=True)
            continue
        same_b = os.path.exists(t) and md5(t) == md5(b)
        chk(f"B5 对表 {leaf(t)}", same_b, "与备份逐字节相同" if same_b
            else "⚠️ 与备份不同 ⇒ 重跑不可复现，要查是谁读了墙钟/外部数据")
    bak_meta = os.path.join(bak, f"meta_{digits}.json")
    if not os.path.exists(bak_meta):
        chk(f"B5 对表 meta_{digits}.json", False,
            f"起表前没有 meta_{digits}.json（本场是新的一天）⇒ 无基准；meta 内容本身已在 B2 四项验过",
            na=True)
    else:
        diffs = meta_diff(json.load(open(bak_meta)), meta_new)
        chk(f"B5 对表 meta_{digits}.json", not diffs,
            "除墙钟格 industry.age_days 外逐字相同" if not diffs
            else "⚠️ 差在：" + "；".join(diffs[:6]))
    for t in snap:
        b = os.path.join(bak, leaf(t))
        if not (os.path.exists(t) and os.path.exists(b)):
            continue
        chk(f"B5 对表 {leaf(t)}", md5(t) == md5(b),
            "外部快照/记账表没被改动（① 复用缓存、记账表重算同值）" if md5(t) == md5(b)
            else "⚠️ 变了 ⇒ 本场不是同一份输入")

    if a.from_bin:
        d = subprocess.run(["diff", "-rq", C.RDAGENT_QLIB_PROVIDER, prov_bak],
                           stdout=subprocess.PIPE, text=True)
        waived, held = b6_judge([ln for ln in (d.stdout or "").splitlines() if ln.strip()],
                                C.RDAGENT_QLIB_PROVIDER, prov_bak)
        chk("B6 provider 逐字节", not held,
            f"差异文件 {len(waived) + len(held)} 个 ⇒ 已核验的复牌末格放行 {len(waived)} 个"
            + (f"（{'、'.join(waived[:6])}{'' if len(waived) <= 6 else ' …'}）" if waived else "（无）")
            + ("　⇒ 其余 0 个，整份 bin 可重建 ✅" if not held
               else f"　⚠️ 不在已知形态内的差异 {len(held)} 个：" + "；".join(held[:6])))

    print("\n──────── 逐块盘点 ────────")
    print("\n".join(report))
    el = time.time() - t_start
    if fails:
        raise SystemExit(f"[链路块丢失/不一致] 红的是：{'、'.join(fails)}　用时 {el:.0f}s ⇒ "
                         f"备份在 {bak}（--from-bin 时 provider 的还原路径是那里的 cn_data，"
                         f"或走 ① 自己的 --rollback）。")
    print(f"\n[重跑完成] 场次 {cal_end}　模式 {'五步全走(--from-bin)' if a.from_bin else '跳过 ①'}　"
          f"用时 {el:.0f}s　⇒ " +
          ("没有 ⚪，上面每块都绿，且产物与**本场起表前磁盘上那份**（面板 mtime "
           f"{time.strftime('%m-%d %H:%M', time.localtime(h5_before['mtime']))} 那次跑出来的）逐字节一致。"
           if not new_session else
           f"上面有 ⚪（{digits} 是新的一天，起表前无产物可比）⇒ B0–B4 绿只证「五步都跑了、读数自洽、"
           f"账本恰好多一行且旧行没动」，**不证**可复现；B1 的面板 mtime 基准是 "
           f"{time.strftime('%m-%d %H:%M', time.localtime(h5_before['mtime']))} 那一次。")
          + f"备份留在 {bak}")


if __name__ == "__main__":
    main()
