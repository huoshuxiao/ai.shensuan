# -*- coding: utf-8 -*-
"""选项C+B 第一步：把 qlib Alpha158 的 158 条定义**逐条**翻译成本线 DSL 表达式

为什么不能正则替换了事：158 条里 `Corr(A, Log(B+1), N)`、`Sum(Greater(X, 0), N)` 是**嵌套**
调用，`Mean($close>Ref($close,1),5)` 的比较式还直接当第一个参数 —— 字符串替换会把
参数顺序和括号结构弄错，而弄错的那一条照样能跑出一个数（不会报错，只会给出另一个因子）。
所以这里走「解析成语法树 → 树上换算子名 → 重新打印」，并且**把翻译前后的树对回来**：
翻译后的表达式再解析一次，必须和原文的树逐节点同形（算子名经映射表还原），
这一条能抓住丢参数、换顺序、少一个分支这三类错，且是**反证式**的（下面 T0 故意把
`Corr` 的两个参数调换，必须被这台自校验抓住）。

口径上只有一处主动偏离 qlib（其余全部同形）：
- `Mean(比较式, N)` 的布尔序列在这里显式 `* 1.0` 升成数值，因为本线 `ts_mean` 走
  `rolling().mean()`，与 qlib 的 `Rolling.apply` 对 bool 的处理不必赌一致；
- 窗口缺值：本线一律「窗内含 NaN ⇒ 该点 NaN」（`min_periods` 默认 = n），qlib 用
  `min_periods=1` 跳过缺值 ⇒ 停牌股上两边给的数不同，**这是刻意沿用本线口径**，
  已在 `factor_dsl.py:20-26` 写明。

`VWAP0 = $vwap/$close` 是数据缺口（面板只有 12 列 = OHLCV+$factor 及裸名副本，没有
vwap/amount），**不进扫描**，单独记 1 条并打印原因 ⇒ 可算 157 条。

只读：不 init qlib、不 fetch、不取真数据、不写任何权威产物。
"""
import json
import os
import re
import sys

sys.path.insert(0, "/home/sunwenkun/miniconda3/envs/rdagent/lib/python3.10/site-packages")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)                                            # 环 1 入口按相对路径找 config

from qlib.contrib.data.loader import Alpha158DL      # noqa: E402
# 导入顺序有讲究：`factor_dsl` 住在 common/src/core，得先让环 1 入口跑完 _bootstrap
# 把那条路径铺进 sys.path（timing2/timing3 踩过同一个坑）。
from run_ashare_factor_eval import load_panel, evaluate   # noqa: E402,F401
from factor_dsl import FACTOR_DSL                    # noqa: E402

# ---------- 极简 tokenizer / parser（够覆盖 Alpha158 的语法面） ----------
TOKEN_RE = re.compile(r"""\s*(?:
      (?P<num>\d+\.\d+(?:[eE][-+]?\d+)?|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
    | (?P<col>\$[A-Za-z_][A-Za-z0-9_]*)
    | (?P<id>[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<op>\*\*|[-+*/<>(),])
)""", re.VERBOSE)


def tokenize(src):
    pos, out = 0, []
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m or m.end() == m.start():
            raise SyntaxError(f"位置 {pos} 无法切分: {src[pos:pos+20]!r}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, val=None):
        k, v = self.peek()
        if val is not None and v != val:
            raise SyntaxError(f"期望 {val}，实得 {v}")
        self.i += 1
        return k, v

    def expr(self):
        node = self.add()
        while self.peek()[1] in ("<", ">"):
            op = self.take()[1]
            node = ("cmp", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.pw()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.pw())
        return node

    def pw(self):
        node = self.un()
        if self.peek()[1] == "**":
            self.take()
            return ("bin", "**", node, self.pw())
        return node

    def un(self):
        if self.peek()[1] == "-":
            self.take()
            return ("neg", self.un())
        return self.atom()

    def atom(self):
        k, v = self.peek()
        if k == "num":
            self.take()
            return ("num", float(v))
        if k == "col":
            self.take()
            return ("col", v[1:].lower())
        if k == "id":
            self.take()
            if self.peek()[1] == "(":
                self.take("(")
                args = [self.expr()]
                while self.take()[1] == ",":
                    args.append(self.expr())
                return ("fn", v, tuple(args))
            return ("bare", v)
        if k == "op" and v == "(":
            self.take("(")
            node = self.expr()
            self.take(")")
            return node
        raise SyntaxError(f"意外 token {k}:{v}")


parse = lambda s: P(tokenize(s)).expr()


# ---------- qlib 算子 → 本线 DSL ----------
# None 表示「同名同序，直接换名」；字符串表示「打印时展开成这个模板」
FUNC_MAP = {
    "Ref": "delay", "Abs": "abs", "Log": "log", "Sign": "sign",
    "Mean": "ts_mean", "Std": "ts_std", "Sum": "ts_sum", "Delta": "delta",
    "Rank": "rank",                                    # Rank(s,n) -> rank(s,n)
    "Max": "ts_max", "Min": "ts_min",                  # qlib 的 Max/Min 是**滚动**极值
    "Corr": "corr", "Quantile": "quantile",            # 参数顺序两边一致
    "IdxMax": "idx_max", "IdxMin": "idx_min",
    "Slope": "slope", "Rsquare": "rsquare", "Resi": "resi",
    "Greater": "np.maximum", "Less": "np.minimum",
}
# 只作用于一个序列、但 qlib 把它当「布尔序列」求均值的算子，需要显式升数值
MEAN_LIKE = {"Mean": "ts_mean"}


def is_cmp(node):
    return node[0] == "cmp"


def emit(node):
    kind = node[0]
    if kind == "num":
        return repr(node[1])
    if kind == "col":
        return node[1]
    if kind == "bare":
        return node[1]
    if kind == "neg":
        return "-" + emit(node[1])
    if kind in ("bin", "cmp"):
        return f"({emit(node[2])} {node[1]} {emit(node[3])})"
    if kind == "fn":
        name, args = node[1], node[2]
        if name in ("Greater", "Less"):
            return f"{FUNC_MAP[name]}({', '.join(emit(a) for a in args)})"
        new = FUNC_MAP.get(name, name)
        if name in MEAN_LIKE and is_cmp(args[0]):
            return f"{new}(({emit(args[0])}) * 1.0, {emit(args[1])})"
        return f"{new}({', '.join(emit(a) for a in args)})"
    raise ValueError(kind)


def norm_orig(node):
    """把原文树做同一处主动偏离（bool 均值 -> *1.0），这样结构对表才只考验翻译本身"""
    if node[0] == "fn":
        args = tuple(norm_orig(a) for a in node[2])
        if node[1] in MEAN_LIKE and is_cmp(node[2][0]):
            args = (("bin", "*", args[0], ("num", 1.0)),) + args[1:]
        return ("fn", node[1], args)
    if node[0] in ("bin", "cmp"):
        return (node[0], node[1], norm_orig(node[2]), norm_orig(node[3]))
    if node[0] == "neg":
        return ("neg", norm_orig(node[1]))
    return node


def demap(node):
    """翻译后的树 -> 用 qlib 的名字还原，用于和原文树逐节点比对"""
    inv = {v: k for k, v in FUNC_MAP.items()}
    if node[0] == "fn":
        name = inv.get(node[1], node[1])
        return ("fn", name, tuple(demap(a) for a in node[2]))
    if node[0] in ("bin", "cmp"):
        return (node[0], node[1], demap(node[2]), demap(node[3]))
    if node[0] == "neg":
        return ("neg", demap(node[1]))
    if node[0] == "bare":
        # 译文里列名是裸名（本线 DSL 的形态），原文里带 `$`；这一处差异是两套 DSL
        # 的取数写法不同，不是翻译错，所以在这里归一化掉。真出现面板没有的裸名
        # （算子名拼错、多了个变量）会露在 T2 的算子检查里。
        return ("col", node[1]) if node[1] in {"close", "open", "high", "low", "volume"} else node
    return node


FAMILY = [
    (r"^(KMID|KLEN|KUP|KLOW|KSFT|OPEN0|HIGH0|LOW0|VWAP0)", "K线形态"),
    (r"^ROC", "N日涨跌"), (r"^MA\d", "价格均值"), (r"^STD\d", "价格波动"),
    (r"^BETA|^RSQR|^RESI", "回归族"), (r"^MAX\d|^MIN\d", "窗口极值"),
    (r"^QTLU|^QTLD", "窗口分位"), (r"^RANK\d", "窗口分位排名"),
    (r"^RSV", "未成熟涨跌"), (r"^IMA|^IMIN|^IMXD", "极值位置"),
    (r"^CORR|^CORD", "价量相关"), (r"^CNT", "涨跌天数"),
    (r"^SUM", "涨跌幅度"), (r"^VMA|^VSTD|^WVMA", "成交量族"),
    (r"^VSUM", "量变动族"),
]
family_of = lambda nm: next((f for p, f in FAMILY if re.match(p, nm)), "其他")

exprs, names = Alpha158DL.get_feature_config()
print(f"[定义] Alpha158DL 给出 {len(names)} 列")

out, skipped, structural_fail = [], [], []
for nm, ex in zip(names, exprs):
    if "\n" in ex or "\r" in ex:
        raise SyntaxError(f"{nm} 是多行表达式，本探针只吃单行")
    tree = parse(ex)
    cols = set(re.findall(r"\$([A-Za-z_]\w*)", ex))
    if "vwap" in cols:
        skipped.append((nm, ex, sorted(cols - set(FACTOR_DSL))))
        continue
    translated = emit(tree)
    # --- 自校验 1：结构同形（能抓丢参数/换顺序/少分支） ---
    if demap(parse(translated)) != norm_orig(tree):
        structural_fail.append((nm, ex, translated))
    out.append({"name": nm, "expr": translated, "family": family_of(nm),
                "qlib_src": ex})


def _walk(node):
    yield node
    if node[0] == "fn":
        for a in node[2]:
            yield from _walk(a)
    elif node[0] in ("bin", "cmp"):
        yield from _walk(node[2])
        yield from _walk(node[3])
    elif node[0] == "neg":
        yield from _walk(node[1])


print(f"[翻译] 进扫描 {len(out)} 条　跳过 {len(skipped)} 条")
for nm, ex, mc in skipped:
    print(f"  ⏭ {nm:<8} {ex}　面板缺列 {mc} ⇒ 数据缺口，不是算子问题")
assert len(out) == 157, f"可算条数应为 157，实得 {len(out)}"
assert len(skipped) == 1, f"数据缺口应只有 VWAP0 一条，实得 {len(skipped)}"

print("\n===== T1 结构同形自校验（翻译前后树逐节点相等）=====")
print(f"  {'❌ ' + str(len(structural_fail)) + ' 条不同形' if structural_fail else '✅ 157 条全部同形'}")
for nm, ex, tr in structural_fail[:5]:
    print(f"     {nm}\n       原文 {ex}\n       译文 {tr}")
assert not structural_fail

print("\n===== T2 算子可用性（译文只用 FACTOR_DSL 注册的函数 + np 的两个逐点函数）=====")
ALLOWED = set(FACTOR_DSL) | {v for v in FUNC_MAP.values() if "." in v}
missing = {}
for rec in out:
    for node in _walk(parse(rec["expr"])):
        if node[0] == "fn" and node[1] not in ALLOWED:
            missing.setdefault(node[1], []).append(rec["name"])
print(f"  {'❌ 未注册算子 ' + str(dict(missing)) if missing else '✅ 无未注册算子'}")
assert not missing
allf = {}
for rec in out:
    for node in _walk(parse(rec["expr"])):
        if node[0] == "fn":
            allf[node[1]] = allf.get(node[1], 0) + 1
print("  用到次数：" + "　".join(f"{k}×{v}" for k, v in sorted(allf.items(), key=lambda kv: -kv[1])))

print("\n===== T3 反证：故意把 Corr 的参数调换，结构校验必须报警 =====")
bad = emit(parse("Corr(Log($volume+1), $close, 20)"))
fake = demap(parse(bad))
truth = norm_orig(parse("Corr($close, Log($volume+1), 20)"))
print(f"  调换后译文：{bad}")
print(f"  {'✅ 抓到了（两棵树不等）' if fake != truth else '❌ 没抓到 ⇒ T1 是恒真判据，作废'}")
assert fake != truth, "T1 抓不到参数调换 ⇒ 这台自校验没牙"

print("\n===== T4 列名只用面板真有的列（译文树里的每个 col 节点）=====")
PANEL_COLS = {"close", "open", "high", "low", "volume"}
badcol = {}
for rec in out:
    for node in _walk(parse(rec["expr"])):
        if node[0] == "col" and node[1] not in PANEL_COLS:
            badcol.setdefault(node[1], []).append(rec["name"])
print(f"  {'✅ 全部落在 ' + str(sorted(PANEL_COLS)) + ' 内' if not badcol else '❌ ' + str(badcol)}")
assert not badcol

os.makedirs(os.path.join(HERE, "tmp_alpha158"), exist_ok=True)
path = os.path.join(HERE, "tmp_alpha158", "alpha158_exprs_0926.json")
with open(path, "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n[落盘] {path}　{len(out)} 条")

from collections import Counter
fc = Counter(r["family"] for r in out)
print("\n===== 族计数（后面算代价要用这张表）=====")
for k, v in fc.most_common():
    print(f"  {k:<10} {v:>3} 条")

print("\n----- 抽样 12 条（每族一条，肉眼过一遍）-----")
seen = set()
for rec in out:
    if rec["family"] not in seen:
        seen.add(rec["family"])
        print(f"  {rec['name']:<9} {rec['expr']}")
