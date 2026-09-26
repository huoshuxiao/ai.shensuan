# -*- coding: utf-8 -*-
"""看板那一格的新文案要真的渲染出来（AppTest 走 streamlit 的 script runner，不是浏览器）

本机 in-app browser 没有 viewport，而且未选中的 tab 根本不在 DOM 里 —— 截图/点击都证不了
这一格。`AppTest` 会把 `with st.tabs[...]` 里的代码全部执行，caption 读得到。
"""
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..",
                                   "stock", "v1", "src"))
sys.path.insert(0, SRC)
os.chdir(SRC)

from streamlit.testing.v1 import AppTest    # noqa: E402

at = AppTest.from_file(os.path.join(SRC, "app.py"), default_timeout=180).run()
texts = [getattr(o, "value", "") or "" for o in list(at.caption) + list(at.markdown)
         + list(at.info) + list(at.error) + list(at.warning)]
blob = "\n".join(texts)

FAILS = []


def chk(tag, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + tag + ("　" + detail if detail else ""))
    if not cond:
        FAILS.append(tag)


chk("R1 页面跑完无异常", not at.exception,
    str(at.exception[0].value)[:120] if at.exception else "")
chk("R2 那一格的说明带上了「该补哪一场问交易所日历」",
    "该补哪一场" in blob and "交易所日历" in blob)
chk("R3 旧的四道闸措辞还在（休市日不落未来场次 / 复用缓存前先对表）",
    "休市日不落未来场次" in blob and "复用缓存前先对表" in blob)
chk("R4 按钮照旧存在且没被锁（本机此刻没有链路进程在跑）",
    any("跑今天这场日更" in (b.label or "") for b in at.button),
    "　".join(b.label for b in at.button)[:60])

print(f"\n{'❌ ' + str(len(FAILS)) + ' 条不过：' + '；'.join(FAILS) if FAILS else '✅ 全部通过'}")
sys.exit(1 if FAILS else 0)
