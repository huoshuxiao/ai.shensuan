# -*- coding: utf-8 -*-
"""甲-2 构造级探针：CoSTEER 跨场知识库那两个环境变量到底认不认（10-04 23:5x）

要回答的问题：`Dump knowledge base path is not set, skip dumping.`（10-04 那场日志
3196／3205／5817 行）能不能只靠**给驱动子进程注入环境变量**关掉。接缝在
site-packages `components/coder/CoSTEER/config.py`：前缀 `CoSTEER_` ＋ 两个字段
`knowledge_base_path`（读）／`new_knowledge_base_path`（写），`__init__.py:37-62` 把它们
交给 `CoSTEERRAGStrategy`，后者 `dump_knowledge_base()`（knowledge_management.py:83）只在
写路径非 None 时才落盘。

六臂（每臂都必须是"可失败"的，坏值不许静默通过）：
  A 负对照：一个键都不设 ⇒ 两个字段都该是 None，且 dump 走那条 warning 分支（不落盘）
  B 注入：设两个键 ⇒ 字段读到值（证明大小写/前缀这套写法进程内真认）
  C 往返：真 pickle 一份 V2 知识库 → 用**同一个函数**读回来 ⇒ 类型对、文件非空
  D 不兼容负对照：放一份 V1 在读路径 ⇒ 该抛 ValueError（这就是本场若开闸可能踩的崩法）
  E 只设写路径：读路径为空 ⇒ 不崩、从空知识库开始（安全的第一档）
  F 代价读数：V2 每加一个组件节点都要打一次嵌入 API ⇒ 开闸的代价里含模型调用

⚠️ 10-05 00:0x 落地时本探针**之外**又量到一笔，探针本身抓不到（它验的是 rdagent
那一侧，落地代码的判据在父进程这一侧）：父进程读知识库字节时，`pickletools.genops`
吐的是 `(opcode, arg, pos)` 三元组（3.14 才有 `.name` 的对象），而 rdagent 那句
`pickle.dump` 跟解释器默认协议走＝协议 5 ⇒ 顶层类落的是 `STACK_GLOBAL`（两个字符串
先压栈、操作数为 None），不是 `GLOBAL`。只认 `GLOBAL` 的第一版判据会把真品读成
「认不出版本」⇒ 每场悄悄降成只写不读、甲-2 白改。这条由正式夹具抓出来并长期看住：
`etf/v1/tests/test_rdagent_wiring.py` 的 CoSTEER 知识库那 7 格（含用**真类**落一枚
真字节再交回父进程读的那一格）。
"""
import os
import sys
import pickle
import tempfile
import traceback
from pathlib import Path

RESULTS = []


def arm(name, ok, detail):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{name}] {'PASS' if ok else 'FAIL'} ｜ {detail}", flush=True)


def fresh_settings():
    """重新实例化，让 pydantic-settings 现读 os.environ（同进程换臂的唯一办法）"""
    from rdagent.components.coder.CoSTEER.config import CoSTEERSettings
    return CoSTEERSettings()


def clear_kb_env():
    for k in ("CoSTEER_KNOWLEDGE_BASE_PATH", "CoSTEER_NEW_KNOWLEDGE_BASE_PATH",
              "costeer_knowledge_base_path", "costeer_new_knowledge_base_path"):
        os.environ.pop(k, None)


class StubRag:
    """照搬 CoSTEERRAGStrategy 用到的那两个属性，直接跑它那两个方法"""

    def __init__(self, dump_path):
        self.dump_knowledge_base_path = dump_path
        self.knowledgebase = None

    dump = None  # 运行时绑真函数
    load_or_init = None


def main():
    from rdagent.components.coder.CoSTEER.knowledge_management import (
        CoSTEERKnowledgeBaseV1, CoSTEERKnowledgeBaseV2, CoSTEERRAGStrategy)

    StubRag.dump = CoSTEERRAGStrategy.dump_knowledge_base
    StubRag.load_or_init = CoSTEERRAGStrategy.load_or_init_knowledge_base

    tmp = Path(tempfile.mkdtemp(prefix="costeer_kb_probe_"))
    print(f"探针目录：{tmp}", flush=True)

    # ---------- A 负对照：不设 ⇒ None ＋ dump 不落盘 ----------
    clear_kb_env()
    s = fresh_settings()
    p = tmp / "A_should_not_exist.pkl"
    rag = StubRag(None)
    rag.dump()
    arm("A 不设键=两字段 None", s.knowledge_base_path is None and s.new_knowledge_base_path is None,
        f"load={s.knowledge_base_path!r} dump={s.new_knowledge_base_path!r}")
    arm("A 不设键=dump 真的不落盘", not p.exists(), f"{p} 存在={p.exists()}")

    # ---------- B 注入：本线打算发出去的那副写法 ----------
    load_p, dump_p = tmp / "kb_v2.pkl", tmp / "kb_v2.pkl"
    os.environ["CoSTEER_KNOWLEDGE_BASE_PATH"] = str(load_p)
    os.environ["CoSTEER_NEW_KNOWLEDGE_BASE_PATH"] = str(dump_p)
    s = fresh_settings()
    arm("B 注入后字段读到值",
        str(s.knowledge_base_path) == str(load_p) and str(s.new_knowledge_base_path) == str(dump_p),
        f"load={s.knowledge_base_path!r} dump={s.new_knowledge_base_path!r}")

    # ---------- C 往返：写出来再读回来 ----------
    # ⚠️ 这里**故意传空 component_list**：V2 的 `__init__` 会给每个组件节点
    # `create_embedding()`（knowledge_management.py:774 → vector_base.py:48），
    # 传非空就等于**打一次嵌入 API**。探针第一次就是栽在这里：无凭据时 litellm
    # 10 连败后 RuntimeError ⇒ 这条本身就是 甲-2 的一笔代价读数。
    rag = StubRag(Path(dump_p))
    rag.knowledgebase = CoSTEERKnowledgeBaseV2(init_component_list=[])
    rag.dump()
    exists, size = dump_p.exists(), (dump_p.stat().st_size if dump_p.exists() else 0)
    back = rag.load_or_init(former_knowledge_base_path=Path(load_p), evolving_version=2)
    arm("C 落盘＋读回是 V2", exists and size > 0 and isinstance(back, CoSTEERKnowledgeBaseV2),
        f"文件存在={exists} 字节={size} 读回类型={type(back).__name__}")

    # ---------- D 不兼容负对照：V1 放在读路径 ----------
    bad = tmp / "kb_v1.pkl"
    pickle.dump(CoSTEERKnowledgeBaseV1(), open(bad, "wb"))
    raised = ""
    try:
        StubRag(None).load_or_init(former_knowledge_base_path=bad, evolving_version=2)
    except ValueError as exc:
        raised = f"ValueError: {exc}"
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    arm("D 放错版本必须抛（不许静默吞）", bool(raised), raised or "什么都没抛＝开闸会把整场崩在 coding 之前")

    # ---------- E 只给写路径：安全的第一档 ----------
    clear_kb_env()
    os.environ["CoSTEER_NEW_KNOWLEDGE_BASE_PATH"] = str(tmp / "E_only_dump.pkl")
    s = fresh_settings()
    ok_e = False
    detail_e = ""
    try:
        kb = StubRag(None).load_or_init(former_knowledge_base_path=(
            Path(s.knowledge_base_path) if s.knowledge_base_path else None), evolving_version=2)
        ok_e = isinstance(kb, CoSTEERKnowledgeBaseV2)
        detail_e = f"load={s.knowledge_base_path!r} 起始知识库={type(kb).__name__}"
    except Exception:  # noqa: BLE001
        detail_e = traceback.format_exc(limit=1)
    arm("E 只写不读=从空库起步、不崩", ok_e, detail_e)

    # ---------- F 代价读数：V2 每加一个组件节点要打一次嵌入 ----------
    emb_hit = ""
    try:
        CoSTEERKnowledgeBaseV2(init_component_list=["demo"])
        arm("F 建节点会打嵌入 API＝甲-2 的代价含 LLM 调用", False,
            "组件节点建完没打嵌入＝这条臂没有牙，读数不可信")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        hit = "create_embedding" in tb
        arm("F 建节点会打嵌入 API＝甲-2 的代价含 LLM 调用", hit,
            f"{type(exc).__name__}，栈里含 create_embedding={hit}"
            + ("" if hit else "；栈顶：" + tb.strip().splitlines()[-1][:120]))

    bad_n = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n合计 {len(RESULTS)} 臂，失败 {bad_n} 臂", flush=True)
    print(f"字节读数：kb_v2.pkl={dump_p.stat().st_size if dump_p.exists() else 0}B、"
          f"kb_v1.pkl={bad.stat().st_size}B", flush=True)
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
