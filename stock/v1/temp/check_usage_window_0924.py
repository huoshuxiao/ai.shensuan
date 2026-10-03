# -*- coding: utf-8 -*-
"""一次性校验：看板时效状态条 + 过期标灰（app.py 的 usage_window 那条链）。

三个模式，各验一件事：
  app        真实 data/results/daily_signal 起整页 AppTest，看状态条文案与 tab 名
  expired    造一个临时信号目录（把 09-23 那份改名叫 09-22），让「T+1 已跨过」这个
             分支真的在页面里发生：tab 名带「已过期」+ 顶部 st.error + 表格走 Styler
             灰化那一条不报错（本机浏览器指点不动，AppTest 会执行所有 tab 的代码）
  branches   把 usage_window / trade_days_after 用 ast 抠出来单测时钟判据：
             盘前/盘中/盘后/休市日/待用(T+1 未进日历)/已过期 逐个枚举

只读真产物；expired 模式写的临时目录在系统 /tmp 下，不碰 data/。
"""

import ast
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
SRC = os.path.join(ROOT, "stock", "v1", "src")
REAL_SIG_DIR = os.path.join(ROOT, "stock", "v1", "data", "results", "daily_signal")
MODE = sys.argv[1] if len(sys.argv) > 1 else "app"

from streamlit.testing.v1 import AppTest


def run_app():
    at = AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=300).run()
    print(f"异常 {len(at.exception)} 个")
    for e in at.exception:
        print("  ❌", e.value, "\n", (e.stack_trace or "")[-1200:])
    print("tab 名:", [t.label for t in at.tabs])
    print("\n顶层 markdown（状态条那行）:")
    for m in at.markdown:
        if any(w in m.value for w in ("窗口", "交易日", "日历")):
            print("  •", m.value)
    print("badge:", [b.value for b in getattr(at, "badge", [])] or "（AppTest 无 badge 注册）")
    print("error 条:", [e.value[:120] for e in at.error] or "无")
    tb = at.tabs[1]
    print("待买入页 error 条:", [e.value[:160] for e in tb.error] or "无")
    print("待买入页 dataframe 数:", len(tb.dataframe))
    # 灰化到底有没有落到渲染层：canvas 表格读不到 DOM CSS，只看 Streamlit 发出的
    # arrow_data.styler —— 前端就照它的 styles（CSS）与 display_values（格式化串）画
    sty = tb.dataframe[0].proto.arrow_data.styler
    if sty.styles:
        print(f"styler 落地：styles {len(sty.styles)} 字，"
              f"含 color:#9aa0a6 {'是' if '#9aa0a6' in sty.styles else '否'}")
        print("  CSS 片段:", sty.styles[:120])
        import io
        import pyarrow.ipc as ipc
        buf = bytes(sty.display_values)
        try:
            dv = ipc.open_bytes(buf).read_all().to_pandas()
        except Exception:
            dv = ipc.open_stream(buf).read_all().to_pandas()
        print("  前端实际显示的前 3 行（格式化后是字符串）:")
        print(dv.head(3).to_string(index=False))
    else:
        print("styler 未落地（那这版灰表等于没灰）")
    print("结果:", "有异常" if at.exception else "全页无异常")
    return at


if MODE == "branches":
    # 把两个函数与两个时钟常量从 app.py 里抠出来单测：不为这几行去 import 整个看板
    tree = ast.parse(open(os.path.join(SRC, "app.py"), encoding="utf-8").read())
    keep, consts = [], {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in ("trade_days_after",
                                                        "usage_window"):
            n.decorator_list = []
            keep.append(n)
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") \
                .startswith("MARKET_"):
            consts[n.targets[0].id] = n

    class _St:                                    # app 里 trade_days_after 的 cache 装饰
        cache_data = staticmethod(lambda **kw: (lambda f: f))

    import datetime as _dt
    import json as _json
    ns = {"st": _St, "os": os, "date": _dt.date, "datetime": _dt.datetime,
          "RDAGENT_QLIB_PROVIDER": os.path.join(ROOT, "stock", "v1", "data",
                                                "qlib", "qlib_data", "cn_data")}
    exec(compile(ast.Module(body=list(consts.values()) + keep, type_ignores=[]),
                 "<app.py>", "exec"), ns)
    uw = ns["usage_window"]
    D = _dt.date
    cases = [
        # sig 全部选日历中间的交易日（09-23 是日历末格 ⇒ T+1 未知 ⇒ 走「待用」那条，
        # 覆盖不到盘后/盘中分支）
        ("T 日 15:30 → 盘后",          D(2026, 9, 22), D(2026, 9, 22), (15, 30)),
        ("T 日 11:00 → 盘中",          D(2026, 9, 22), D(2026, 9, 22), (11, 0)),
        ("T+1 08:50 → 盘前(窗口内)",   D(2026, 9, 22), D(2026, 9, 23), (8, 50)),
        ("T+1 09:40 → 已过期",         D(2026, 9, 22), D(2026, 9, 23), (9, 40)),
        ("T+2 → 已过期",               D(2026, 9, 22), D(2026, 9, 24), (10, 0)),
        ("日历末格 → 待用(不判过期)",  D(2026, 9, 23), D(2026, 9, 24), (10, 0)),
        ("休市日(名单 09-18) → 待用",  D(2026, 9, 18), D(2026, 9, 20), (12, 0)),
    ]
    print(f"{'场景':28s} {'阶段':6s} {'过期':4s} 说明")
    for nm, sig, today, hm in cases:
        real_now = _dt.datetime.combine(today, _dt.time(*hm))
        stage, stale, why = uw(sig, now=real_now)
        print(f"{nm:28s} {stage:6s} {str(stale):5s} {why}")

elif MODE == "expired":
    tmp = "/tmp/qoder_sig_expired_0924"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for f in os.listdir(REAL_SIG_DIR):
        if "20260923" in f:
            shutil.copy(os.path.join(REAL_SIG_DIR, f),
                        os.path.join(tmp, f.replace("20260923", "20260922")))
    os.environ["STOCK_SIGNAL_DIR"] = tmp
    print(f"[临时信号目录] {tmp} = "
          f"{sorted(x[7:15] if x.startswith('signal') else x[4:12] for x in os.listdir(tmp))}"
          f"（T=09-22 ⇒ T+1=09-23 已过 ⇒ 应判过期）")
    run_app()
    shutil.rmtree(tmp, ignore_errors=True)

else:
    run_app()
