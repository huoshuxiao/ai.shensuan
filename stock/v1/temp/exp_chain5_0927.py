# -*- coding: UTF-8 -*-
"""⑤ 挂进链路的那 15 行真跑三支：出账 / 未出账（非致命）/ --no-exposure（09-27 选项D）

为什么不直接跑整条链路：`decide_session` 现在会说「该补 09-25 那场」⇒ ① 会去抓一份**今天盘中**
的快照往 bin 上贴（场次闸只挡未来与休市，挡不住「补一个已经过去的日子」）⇒ 生产 bin 有被贴错
数据的风险。所以这里只把新加的 `run_exposure()` 单独起起来跑，①②③④ 一个字节都不碰。

三支都必须看到自己那句标记，且「未出账」那支**不许把异常抛出来**（抛了就等于 ⑤ 会打断链路）：
  A 出账　　　　 → 打印 `[⑤ 出账]`，且覆写面里的账本真的多了一行
  B 未出账　　　 → 账本路径指向一个不存在的目录 ⇒ 子进程非零，打印 `[⚠️ ⑤ 未出账]`、函数正常返回
  C --no-exposure → 打印 `[⑤ 跳过]`，且账本文件的 mtime 一字不动
"""
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/sunwenkun/Developer/agent-workspace/ai.shensuan.git"
SRC = os.path.join(ROOT, "stock/v1/src")
OUT = os.path.join(HERE, "tmp_exposure_fwd_0927")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401
import run_ashare_daily_chain as C  # noqa: E402


def tee(fn, *args, **kw):
    buf = io.StringIO()
    real = sys.stdout
    try:
        sys.stdout = buf
        rv = fn(*args, **kw)
    finally:
        sys.stdout = real
    text = buf.getvalue()
    print(text.rstrip())
    return rv, text


def case(tag, ledger, no_exposure, want, want_file):
    os.environ["STOCK_EXPOSURE_LEDGER"] = ledger
    before = os.path.getmtime(ledger) if os.path.exists(ledger) else None
    try:
        _, text = tee(C.run_exposure, types.SimpleNamespace(no_exposure=no_exposure))
        err = ""
    except BaseException as e:      # 未出账那一支抛出来就是 bug（⑤ 不许打断链路），接住并报
        err = f"{type(e).__name__}: {e}"
        text = ""
    ok_mark = want in text
    exists = os.path.exists(ledger)
    after = os.path.getmtime(ledger) if exists else None
    if want_file == "created":
        ok_file = exists and before is None
    elif want_file == "rewritten":
        ok_file = exists and after is not None and after > (before or 0)
    else:                           # untouched
        ok_file = (after == before) if before is not None else not exists
    state = ("未动" if before == after else "被改") if exists else "仍不存在"
    print(f"[{tag}] 标记「{want}」{ok_mark}　账本 {ledger} 存在={exists} mtime "
          f"{'新建' if before is None and exists else state}　异常={err or '无'}"
          f"　⇒ {'过' if ok_mark and ok_file and not err else '不过'}")
    return ok_mark and ok_file and not err


def main():
    a = case("A 出账", os.path.join(OUT, "ledger_chain.csv"), False, "[⑤ 出账]", "created")
    # 覆写面故意选在 /root：⑤ 落盘前会 makedirs(dirname)，非root用户在这儿必炸 ⇒ 真失败注入
    b = case("B 未出账", "/root/tmp_exp_0927/ledger.csv", False, "[⚠️ ⑤ 未出账]", "untouched")
    c = case("C 跳过", os.path.join(OUT, "ledger_chain.csv"), True, "[⑤ 跳过]", "untouched")
    d = case("D 同场重跑（幂等）", os.path.join(OUT, "ledger_chain.csv"), False,
             "[⑤ 出账]", "rewritten")
    print("\n[判定] " + ("四支全过 ✅ ⇒ ⑤ 挂着不会咬人：出得了账、出不了账也只打 ⚠️、能跳过、重跑幂等"
                        if all((a, b, c, d)) else "❌ 有支不过"))
    return 0 if all((a, b, c, d)) else 1


if __name__ == "__main__":
    sys.exit(main())
