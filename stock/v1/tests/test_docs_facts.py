# -*- coding: utf-8 -*-
"""说明书正文里的事实型断言，必须能在过期时变红（丙 7 选的「A」）。

为什么要有这个文件：`stock/v1/temp/check_agentmd_citations_0925.py` 那把尺子只核它自己
维护的 `文件:行号` 表，**不读正文**。所以 09-29 那天它 115/115 全绿的同时，正文里还写着
「`stock/v1/src/data/` 存在」「952M」「股票 125 个」——三件都已经是假的。
本文件补的就是这一层：**正文里凡是能从磁盘量出来的数，一律现量对表。**

六类判据（都按「零漂移」优先，会随行情天天变的东西不做硬相等）：
  F1 入口数        目录树 src 那行的「N 个入口」 == `ls stock/v1/src/run_*.py` 现量
  F2 草稿区计数    正文两处「股票 N 个」彼此一致，且不离 temp 里 `.py/.sh` 现量超 10%
  F3 数据层脚本数  正文「股票 2 个」 == `common/src/data/stock/*.py` 现量
  F4 编号引用      §18 条号连续；正文「§18 第 N 条」的 N 必须真的存在
  F5 路径存在性    正文反引号里带 `/` 且以仓库顶层目录开头的路径必须还在
                   （行号对不对由那把老尺子管，这里只挡搬家留下的死指针）
  F6 体积声明      目录树里「N[MG]（… du …）」与现量差 ≤35%，M 按 `du -h` 口径=MiB
                   （面板每天重生成，允许缓漂；没写 du 的数字不当实测值）

N1 那组是**负对照**：拿人为改错的文本喂同一套函数，必须每一条都抓到 ⇒ 证明本文件有牙。
错法一律用正则注入、不钉整句原文 —— 并行会话随时在改这份文档的措辞（09-29 15:2x 就有
人把 `1.7G（09-29 14:4x 实测 du）` 整行换成 `853M（…）`），钉原文的对照会自己先失效。
"""

import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT

DOC = REPO_ROOT / "stock" / "v1" / "AGENT.md"
TOP_DIRS = ("common", "stock", "etf", "live2etf", "shell")

_DATA_LAYER_RE = re.compile(r"股票\s*(\d+)\s*个、ETF\s*(\d+)\s*个脚本")
_ITEM_RE = re.compile(r"^(\d+)\.\s+\*\*", re.M)
_ITEM_REF_RE = re.compile(r"§18 第\s*(\d+)\s*条")
# 反引号里的整串 token（含 glob 字符），再按前缀白名单与 glob 过滤
_TOKEN_RE = re.compile(r"`([^`\n]+)`")
# 体积声明：数字 + M/G + 紧跟一个提到 du 的括号（句子怎么改都能命中）
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([MG])（[^）]*du[^）]*）")
# 目录树里那一行 src 的入口数（只认这行，避免把「合并 24 个入口」那种跨线口径误当本线数）
_SRC_LINE_RE = re.compile(r"^.*├──\s+src/.*?(\d+)\s*个入口", re.M)
# 树里两种行首：顶层 `├── stock/v1/`，嵌套 `│   ├── data/{a,b}/   # 注释`
_TOP_RE = re.compile(r"^[├└]── (\S+)/")
_NEST_RE = re.compile(r"^[│ ├└─]+\S")
_GLOB_CH = "*<>{}?[]|"


def count_scratch(dir_path: Path) -> int:
    """草稿区脚本数：只算 `.py` / `.sh`（目录里还躺着一堆日志与夹具产物）。"""
    return len([p for p in dir_path.iterdir() if p.suffix in (".py", ".sh")])


def count_entries() -> int:
    return len(list((REPO_ROOT / "stock" / "v1" / "src").glob("run_*.py")))


def dir_bytes(path: Path) -> int:
    """目录体积（字节）：st_size 求和。块对齐误差远小于本文件 35% 的容差。"""
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def check_entries(text: str) -> list:
    """只认目录树那一行的「N 个入口」：口径 = `ls stock/v1/src/run_*.py` 现量。

    （§18 第 7 条那句「合并 24 个入口」讲的是**两线合计**的另一种口径，不在这条管。）
    """
    got = count_entries()
    ms = _SRC_LINE_RE.findall(text)
    if not ms:
        return ["F1 正文里找不到「src/ … N 个入口」那行（判据本身失效，先确认 §4 树还在）"]
    return [f"F1 入口数：正文写 {n}，磁盘现量 {got}" for n in ms if int(n) != got]


def check_scratch_counts(text: str) -> list:
    """正文里凡是「股票 N 个」这类本线草稿区计数：彼此必须一致，且不能离现量太远。

    草稿区是**天天在动**的数（谁都可能往 temp/ 丢一枚探针），所以这里只做两件事：
    同文自洽（两处必须给同一个数）+ 挡离谱过期（差 >10%）。不挡 ±1 的漂移。
    """
    stated = []
    for line in text.splitlines():
        if "temp/：" not in line and "一次性脚本只进" not in line:
            continue
        m = re.search(r"股票\s*(\d{2,4})\s*(?:个脚本|/)", line) or \
            re.search(r"(\d{2,4})\s*个脚本", line)
        if m:
            stated.append((line.strip()[:40], int(m.group(1))))
    if not stated:
        return ["F2 正文里找不到本线草稿区计数（判据本身失效，先确认 §4 那段还在）"]
    got = count_scratch(REPO_ROOT / "stock" / "v1" / "temp")
    bad = [f"F2 草稿区计数过期：该行写 {n}，现量 {got}（差 >10%）｜ {head}"
           for head, n in stated if abs(n - got) / got > 0.10]
    if len({n for _, n in stated}) > 1:
        bad.append(f"F2 同一份正文里「股票 N 个」自相打架：{stated}")
    return bad


def check_data_layer(text: str) -> list:
    m = _DATA_LAYER_RE.search(text)
    if not m:
        return ["F3 正文里找不到「股票 N 个、ETF M 个脚本」那句（判据本身失效）"]
    got_stock = len(list((REPO_ROOT / "common" / "src" / "data" / "stock").glob("*.py")))
    if int(m.group(1)) != got_stock:
        return [f"F3 股票数据层脚本：正文写 {m.group(1)}，现量 {got_stock}"]
    return []


def check_item_refs(text: str) -> list:
    sec = text.split("## 18.")[1] if "## 18." in text else ""
    nums = [int(m.group(1)) for m in _ITEM_RE.finditer(sec)]
    if not nums:
        return ["F4 §18 里没解析到编号条目（判据本身失效）"]
    out = []
    if nums != list(range(1, len(nums) + 1)):
        out.append(f"F4 §18 条号不连续：{nums}（markdown 会自己吞断号 ⇒ 引用会指错）")
    for m in _ITEM_REF_RE.finditer(text):
        if int(m.group(1)) not in nums:
            out.append(f"F4 正文引用「§18 第 {m.group(1)} 条」，但该条不存在（现有 {max(nums)} 条）")
    return out


def checked_path_tokens(text: str) -> list:
    """正文里「被 F5 当真路径核对过」的反引号 token（原样，可能带 `:行号` 尾巴）。"""
    out = []
    for m in _TOKEN_RE.finditer(text):
        token = m.group(1).strip()
        if "/" not in token or not token.startswith(TOP_DIRS):
            continue
        raw = re.split(r"[:：]", token)[0].rstrip("/")
        if any(ch in raw for ch in _GLOB_CH):
            continue
        out.append(token)
    return out


def check_paths(text: str) -> list:
    """只挡「搬家留下的死指针」：反引号里带 / 、以仓库顶层目录开头的 token。

    裸文件名（`etf_admission.py:177`）不当地点用；含 glob 字符的（`*/v1/temp/`、
    `common/data/{stock,etf,live2etf}/`）是「一类」不是「一条」，跳过。
    """
    toks = checked_path_tokens(text)
    if not toks:
        return ["F5 正文里一个可核路径 token 都没有 ⇒ 判据本身失效"]
    out = []
    for token in toks:
        raw = re.split(r"[:：]", token)[0].rstrip("/")
        if not (REPO_ROOT / raw).exists():
            out.append(f"F5 正文引用了不存在的路径：{raw}")
    return out


def _expand_braces(frag: str) -> list:
    """`data/{library,live,results}` → 三条路径（树里就这一种花括号用法）。"""
    m = re.match(r"(.*?)\{([^}]*)\}(.*)$", frag)
    if not m:
        return [frag]
    pre, braced, post = m.groups()
    return [f"{pre}{part}{post}" for part in braced.split(",")]


def _tree_dirs(text: str) -> dict:
    """把 §4 目录树每行解析成「该行指的是哪个目录」：行号 -> 该行的目录路径列表。

    顶层行 `├── stock/v1/` 定住前缀，嵌套行 `│   ├── data/{a,b}/  # 注释` 接在前面。
    """
    lines = text.splitlines()
    if not lines.count("```"):                       # 围栏不配对就不猜
        return {}
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith("```") and "shensuan" in lines[i + 1]), None)
    if start is None:
        return {}
    out, root = {}, None
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.startswith("```"):
            break
        top = _TOP_RE.match(ln)
        if top:
            root = top.group(1)
            out[i] = [root] if root in TOP_DIRS else []
            continue
        nest = _NEST_RE.match(ln)
        if nest and root:
            # 先剥树形前缀（`│   ├── `，里面就带空格），再切注释/两段以上空格
            body = re.sub(r"^[│ ├└─]+", "", ln)
            frag = re.split(r"\s{2,}|#", body, maxsplit=1)[0].strip().rstrip("/")
            out[i] = [f"{root}/{p}" for p in _expand_braces(frag) if p]
    return out


def check_size(text: str) -> list:
    """目录树里带「实测 du」的体积声明，对现量（M=MiB、G=GiB，与 `du -h` 同口径）。

    一条这样的声明都没有 ⇒ 判据本身失效（正文把实测体积删了，F6 就成了空转的绿灯）。
    """
    dirs = _tree_dirs(text)
    out, found = [], 0
    for ln_no, ln in enumerate(text.splitlines()):
        for m in _SIZE_RE.finditer(ln):
            found += 1
            stated, unit = float(m.group(1)), m.group(2)
            targets = dirs.get(ln_no, [])
            if not targets:
                out.append(f"F6 第 {ln_no + 1} 行有体积声明 {m.group(0)[:24]}…，"
                           f"但该行解析不出目录 ⇒ 判据本身失效")
                continue
            got_mib = sum(dir_bytes(REPO_ROOT / t) for t in targets) / 1024 ** 2
            want = got_mib if unit == "M" else got_mib / 1024
            if abs(want - stated) / stated > 0.35:
                out.append(f"F6 体积声明过期：{'、'.join(targets)} 正文写 {stated}{unit}，"
                           f"现量 {want:.2f}{unit}（差 >35%）")
    if not found:
        out.append("F6 正文里找不到任何「N[MG]（… du …）」实测体积 ⇒ 判据本身失效")
    return out


ALL_CHECKS = (check_entries, check_scratch_counts, check_data_layer,
              check_item_refs, check_paths, check_size)


@pytest.mark.parametrize("fn", ALL_CHECKS, ids=lambda f: f.__name__)
def test_正文事实与磁盘对得上(fn):
    assert fn(DOC.read_text()) == []


def _bump(pattern, repl):
    """正则注入器：锚点没了就当场报错（＝这一格对照没建成，别让它悄悄变绿）。"""
    def inject(text: str) -> str:
        new, n = re.subn(pattern, repl, text, count=1)
        assert n == 1, f"锚点 {pattern} 在正文里没找到 ⇒ 这一格负对照没建成"
        assert new != text, "注入后文本没变 ⇒ 替换是空操作"
        return new
    return inject


def _kill_first_path(text: str) -> str:
    """把 F5 正在核的第一条真路径改成不存在的目录名（不钉具体 token，随正文走）。"""
    toks = checked_path_tokens(text)
    assert toks, "正文里没有可核路径 token ⇒ 这一格负对照没建成"
    victim = toks[0]
    new = text.replace(f"`{victim}`", f"`{victim.rstrip('/')}/gone_dir_0929`", 1)
    assert new != text, "路径 token 替换失败 ⇒ 这一格负对照没建成"
    return new


# 每格 = (判据, 注入器)。注入一律走模式而不是钉整句原文：并行会话随时在换措辞。
NEGCTL = [
    (check_entries, _bump(r"(├──\s+src/[^\n]*?)(\d+)(\s*个入口)", r"\g<1>3\g<3>")),
    (check_scratch_counts, _bump(r"(股票\s*)(\d{2,4})(\s*(?:个脚本|/))", r"\g<1>999\g<3>")),
    (check_data_layer, _bump(r"(股票 )(\d+)( 个、ETF)", r"\g<1>99\g<3>")),
    (check_item_refs, _bump(r"(§18 第 )(\d+)( 条)", r"\g<1>9999\g<3>")),
    (check_paths, _kill_first_path),
    (check_size, _bump(r"(\d+(?:\.\d+)?)([MG]（[^）]*du[^）]*）)", r"3\g<2>")),
]


@pytest.mark.parametrize("fn,inject", NEGCTL, ids=[f.__name__ for f, _ in NEGCTL])
def test_负对照_人为改错必须被抓到(fn, inject):
    """往真文本里注入一个错，该判据必须报红 ⇒ 本文件不是恒绿装饰。"""
    text = DOC.read_text()
    assert fn(text) == [], f"{fn.__name__}：真正文本身就不绿，先修判据再谈牙"
    assert fn(inject(text)), f"{fn.__name__} 对人为改错的文本竟然放行 ⇒ 无牙"
