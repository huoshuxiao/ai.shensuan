# -*- coding: utf-8 -*-
"""丁-3 的牙口检查（10-01）：那条「正对照」夹具改成现推日历之后，还有没有牙。

起因：`tests/test_bin_daily_guards.py` 的假日历原先写死 09-22~09-30 六天 ⇒ 10-01（国庆）
「今天」不在表里，那条**本该放行**的正对照自己先红了。改法是日历跟着当天现推。
但「改掉一条红」最容易被做成「把判据调瞎」⇒ 这一场要证的不是它变绿了，而是：

  C1 表里必须有「今天」，且是**排好序的 DatetimeIndex**（Series 那条坑见 test 内注释）
  C2 `_off_day()` 给的那天必须真的不在表里，且是周六（负对照的锚点没飘）
  C3 正对照：日历里 + 已到收盘日 ⇒ 放行（不抛）
  C4 **有牙证明**：只把日历换成「少了今天」那张，同一场输入必须当场抛 ⇒
     证明 C3 的放行是「因为今天真在表里」，不是这道闸根本不看表（恒放行）
  C5 归因：09-29 那版写死的日历在**今天**确实不含今天 ⇒ 那枚红是真的，本次改动是它变绿的原因
  C6 边界：未来那天（今天 +1）即使被塞进日历也照样拒 ⇒ ① 那道闸没被这次改动绕过

只读：不发网络请求、不碰面板、不写生产产物（临时目录收在 /tmp，收尾自删）。
"""
import os
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="bin-cal-teeth-1001-")
os.environ["STOCK_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["STOCK_BASE_DATA_DIR"] = os.path.join(_TMP, "base_data")
os.environ["STOCK_REPORT_DIR"] = os.path.join(_TMP, "report")
os.environ["STOCK_LOG_DIR"] = os.path.join(_TMP, "log")
os.environ["STOCK_DAILY_H5"] = os.path.join(_TMP, "no_such_panel.h5")
os.environ["STOCK_FACTORS_JSON"] = os.path.join(_TMP, "factors.json")
for _k in list(os.environ):
    if _k.startswith("STOCK_LLM_") or _k in ("OPENAI_API_KEY", "OLLAMA_HOST"):
        os.environ.pop(_k, None)

HERE = os.path.dirname(os.path.abspath(__file__))          # .../stock/v1/temp
V1 = os.path.dirname(HERE)                                 # .../stock/v1
SRC = os.path.join(V1, "src")
sys.path.insert(0, SRC)
import _bootstrap  # noqa: E402,F401

import pandas as pd
import update_qlib_bin_daily as bin_daily

sys.path.insert(0, os.path.join(V1, "tests"))
import test_bin_daily_guards as TB

RED = []


def chk(ok, label, detail=""):
    print(f"  {'✅' if ok else '❌'} {label}" + (f"　—— {detail}" if detail else ""), flush=True)
    if not ok:
        RED.append(label)


def _raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except SystemExit as e:
        return str(e)
    except BaseException as e:                     # 拒绝型判据换了异常类型也算抓到
        return f"!!{type(e).__name__}: {e}"
    return None


today = TB._today()
today_s = today.strftime("%Y-%m-%d")
print(f"场次口径：今天 = {today_s}（weekday {today.weekday()}），假日历表长 {len(TB.CAL)} 格")

# ---- C1 ----
chk(isinstance(TB.CAL, pd.DatetimeIndex), "C1a 假日历是 DatetimeIndex（不是 Series ⇒ `in` 查值不查行号）",
    type(TB.CAL).__name__)
chk(list(TB.CAL) == sorted(TB.CAL), "C1b 假日历已排序")
chk(today in TB.CAL, f"C1c 今天 {today_s} 在表里（正对照的立足点）",
    f"表跨度 {TB.CAL[0]:%Y-%m-%d} ~ {TB.CAL[-1]:%Y-%m-%d}")

# ---- C2 ----
off = TB._off_day()
off_s = off.strftime("%Y-%m-%d")
chk(off not in TB.CAL, f"C2a 表外那天 {off_s} 确实不在表里")
chk(off.weekday() == 5, f"C2b 那天是周六（weekday==5，天然非交易日）", str(off.weekday()))
chk(off < today, f"C2c 那天早于今天（不能靠『未来』那条闸蒙过 C4 的对照）")

_real = bin_daily.trade_days
try:                                        # 读数，不判红：真交易日历怎么看待今天
    print(f"  ·  读数：真日历（{_real.__module__}）里今天是交易日吗 → {today in _real()}")
except BaseException as e:
    print(f"  ·  读数：真日历取不到（本检查不依赖网络，跳过）→ {type(e).__name__}")
bin_daily.trade_days = lambda: TB.CAL          # 与 pytest 那个 fake_calendar 夹具同一件事

# ---- C3 正对照：不抛 ----
msg = _raises(bin_daily.guard_session, today_s, explicit=True)
chk(msg is None, "C3 正对照：日历里 + 已到收盘日 ⇒ 放行", msg or "没抛")

# ---- C4 有牙：日历去掉今天 ⇒ 同一场必须拒 ----
bin_daily.trade_days = lambda: TB.CAL[TB.CAL != today]
try:
    msg = _raises(bin_daily.guard_session, today_s, explicit=True)
    chk(msg is not None and "不在交易所交易日历" in msg,
        "C4 牙口：只把「今天」从日历里删掉，同一场输入必须被拒（否则 C3 是恒放行）", msg or "没抛 ⇒ 无牙")
finally:
    bin_daily.trade_days = lambda: TB.CAL

# ---- C5 归因：09-29 那版写死的日历在今天确实不含今天 ----
old_cal = pd.DatetimeIndex(["2026-09-22", "2026-09-23", "2026-09-24",
                            "2026-09-28", "2026-09-29", "2026-09-30"])
chk(today not in old_cal, "C5 旧版写死日历在今天不含今天 ⇒ 那枚红是真的、改动是它变绿的原因",
    f"旧表末格 {old_cal[-1]:%Y-%m-%d}，今天 {today_s}")

# ---- C6 边界：未来那天塞进日历也照样拒（① 没被绕过） ----
tomorrow = (today + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
bin_daily.trade_days = lambda: TB.CAL.append(pd.DatetimeIndex([today + pd.Timedelta(days=1)]))
try:
    msg = _raises(bin_daily.guard_session, tomorrow, explicit=True)
    chk(msg is not None and "还没发生" in msg,
        "C6 牙口：未来那天即使在日历里也照样拒（『晚于今天』那道闸还活着）", msg or "没抛 ⇒ 无牙")
finally:
    bin_daily.trade_days = _real

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n{'EXIT=0 全绿' if not RED else 'EXIT=1 判红: ' + ' | '.join(RED)}　（{len(RED)} 红）")
sys.exit(1 if RED else 0)
