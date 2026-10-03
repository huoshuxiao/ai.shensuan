# -*- coding: utf-8 -*-
"""ETF 日更链的反证夹具（#53）：六道判据全部**不写生产字节**

跑法：`/usr/bin/python3.10 etf/v1/temp/chain_probe_etf_0928.py`（实测 <1 分钟）

为什么要有这份而不是"跑一次链路看看"：链路里三条闸在正常日子的真字节样本是**拿不到的**
——收盘闸要到下午 3 点前才可能为 False、复跑闸要真的并发第二个实例、取消闸要真有人
按下请求。等不到就验不了，等验不了就不能说它接上了（这条是 09-28 被逐字纠正过的那条：
「断言不许恒真」）。所以这里：
  T1 收盘闸：注入两个假时刻（14:59 / 15:00）——同一条判据必须给出 False/True 两个方向，
     再对真实"现在"取一次数（现在已过 15:00 ⇒ True），三个读数缺一即红。
  T2 复跑闸：起一个**真子进程**（`--dry-run`，只读），在它活着的时候从外面数 /proc
     ⇒ 必须认到它；它退出后再数 ⇒ 必须为 0。反证 = 判据不是"永远返回空列表"。
     同时验 argv[0] 那道筛：`bash -c "...run_etf_daily_chain.py..."` 这种包装行
     **不算**实例（否则第一次起链的自己会被拒）。
  T3 取消闸：真写那个 request 文件 ⇒ 真跑一次 `--dry-run` ⇒ 必须非零退出、必须把标记
     **删掉**、且 stdout 里不得出现"前置体检"之后的读数（说明它挡在体检之前）。
  T4 幂等读数：`--dry-run` 跑前后各读一次 `freshness_report()` ⇒ 七个面（镜像×2 +
     池缓存×2 + 风险长表×3）的末日与只数必须逐格相同 ⇒ 证明排练真的一个字节没写。
  T5 日历闸 `decide_sessions`：拿**注入**的基准日和"今天"各算一次（真日历、假日期），
     四支读数必须都出现——该补（pending ≤ 今天）/ 还没发生（pending > 今天）/
     **两张面裂开**（沪该补、深已到位）/ 取不到日历（把 `trade_days` 换成抛异常的
     假函数）。为什么不能只跑真"今天"一次：那只会给出四支里的一支，判据就算写反了
     这份夹具也照绿（09-28 撞上过的同款：链路第一版把四张价面取了 **max**，于是
     "深@09-28 / 沪@09-24"被念成"价面末日 09-28、已到位"，而真跑日志里那句 ⚠️
     根本没出现——是当天 18:02 手算末日才发现的）。

T6 `--auto` 的写面：链路文档写「② 不碰判据」，这句话得有真字节背书 ⇒ 把
     `ConfigUpdater(config_path=…)` 指到 `etf/v1/temp/tmp_conc_0928/config_copy.py`
     上跑同一份代码，两支都要出现（负：本线 config 没有 `SLIPPAGE` 这个行首常量
     ⇒ 返回 False、副本逐字节不变；正：`MIN_COMMISSION` 真实存在 ⇒ True、文件真被改）。
     生产 `config.py` 不开——它被正则改写是源码级事故。

覆写面：只碰 `etf/v1/log/etf_chain_cancel.request`（T3 自己写自己删）与
`etf/v1/temp/tmp_conc_0928/`（T6 的 config 副本）；`etf/v1/data/` 全程只读
（不调 ① 的真跑分支，② 更是只在副本上验写面）。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "etf", "v1", "src")
TMP = os.path.join(HERE, "tmp_conc_0928")
os.makedirs(TMP, exist_ok=True)
sys.path.insert(0, SRC)
os.chdir(SRC)

import pandas as pd  # noqa: E402
import run_etf_daily_chain as ch  # noqa: E402

fails = []

# ---------- T1 收盘闸（注入时刻，三个读数必须两个方向都有） ----------
ok_1459, why_1459 = ch.close_gate(pd.Timestamp("2026-09-28 14:59:00"))
ok_1500, why_1500 = ch.close_gate(pd.Timestamp("2026-09-28 15:00:00"))
ok_now, why_now = ch.close_gate()
t1 = ok_1459 is False and ok_1500 is True and isinstance(ok_now, bool)
print(f"T1 收盘闸：14:59→{ok_1459} / 15:00→{ok_1500} / 真现在({why_now[-9:]})→{ok_now} "
      f"{'✅' if t1 else '❌'}")
if not t1:
    fails.append("T1 收盘闸判据没牙（14:59 必须 False、15:00 必须 True）")

# ---------- T2 复跑闸（真并发子进程） ----------
child = subprocess.Popen([sys.executable, ch.__file__, "--dry-run"],
                         cwd=SRC, stdout=subprocess.DEVNULL,
                         stderr=subprocess.STDOUT)
time.sleep(1.0)
during = ch.find_running()
t2a = str(child.pid) in during
code = child.wait(timeout=600)
after = ch.find_running()
t2b = str(child.pid) not in after and after == []
# argv[0] 那道筛：bash 包装行里出现脚本名，不该被数成实例
wrap = subprocess.run(["bash", "-c", f"sleep 3  # {os.path.basename(ch.__file__)}"],
                      shell=False)
t2c = wrap.returncode == 0
during_wrap_probe = ch.find_running()
t2 = t2a and t2b and during_wrap_probe == []
print(f"T2 复跑闸：子进程 {child.pid} 活着时认到 {during}（exit={code}）⇒ {t2a}；"
      f"退出后剩 {after} ⇒ {t2b}；bash 包装行不被误认 ⇒ {t2c} {'✅' if t2 else '❌'}")
if not t2:
    fails.append(f"T2 复跑闸不成立（活着认到={t2a}, 退出归零={t2b}）")

# ---------- T3 取消闸（真写标记 → 真起一次 → 必须停 + 必须删标记） ----------
# 断言不能查"输出里没有『前置体检』四个字"：中止语本身就带着这一步的名字（第一版
# 就是这么自相矛盾地永远红的）。改查体检**真的开跑了吗**——那张只读表只有体检
# 起来以后才会出现。
with open(ch.CANCEL_FLAG, "w") as f:
    f.write("probe\n")
r = subprocess.run([sys.executable, ch.__file__, "--dry-run"], cwd=SRC,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
t3 = (r.returncode != 0 and "已按请求中止" in r.stdout
      and not os.path.exists(ch.CANCEL_FLAG)
      and "前置体检（只读）" not in r.stdout and "收盘闸" not in r.stdout)
print(f"T3 取消闸：exit={r.returncode}、输出首行 "
      f"{r.stdout.splitlines()[0][:60]!r}、标记已删="
      f"{not os.path.exists(ch.CANCEL_FLAG)}、体检未开跑="
      f"{'前置体检（只读）' not in r.stdout} {'✅' if t3 else '❌'}")
if not t3:
    fails.append("T3 取消闸没拦住（要么退出码 0，要么标记没删，要么它继续跑了体检）")

# ---------- T4 幂等：dry-run 前后六个面逐格相同 ----------
b0 = ch.read_ends()
r = subprocess.run([sys.executable, ch.__file__, "--dry-run"], cwd=SRC,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
b1 = ch.read_ends()
t4 = r.returncode == 0 and b0 == b1
print(f"T4 排练幂等：dry-run exit={r.returncode}，{len(b0)} 个面末日/落后/只数逐格相同 "
      f"{'✅' if t4 else '❌'}")
if not t4:
    diff = {k: (b0.get(k), b1.get(k)) for k in set(b0) | set(b1)
            if b0.get(k) != b1.get(k)}
    fails.append(f"T4 dry-run 竟然改了数据面读数（这必须是零覆写）：{diff}")
print("\n体检读数（只读，给链路的第一步当基线）：")
for k, v in sorted(b0.items()):
    print(f"  {k:<24} {v[0]}  批内落后 {v[1]} 工作日  {v[2]} 只")
print(f"\n[② 滞后读数] 分析面 vs 镜像：" + str(ch.backtest_lag_days(b0)))
print(f"[价面末日] " + str(ch.price_surface_ends(b0)) + "　日历判据（真今天）："
      + str(ch.decide_sessions(b0)[0] or ch.decide_sessions(b0)[1]))


# ---------- T5 日历闸：三支读数 + 「两张面裂开」那一支，都必须出现 ----------
# 注入的日期一律**从日历本身反推**（下方 gaps/one_later），不写死"09-25 休市"这种
# 我以为对的事实——写死了判据一旦与表不符就是夹具自己红，而不是链路红。
def fake_ends(d, d2=None):
    """一张面落后、另一张（可）到位：09-28 真跑就是这形状（深@09-28/沪@09-24）"""
    return {"全市场镜像|沪": (d, 0, 483),
            "全市场镜像|深": (d2 or d, 0, 388),
            "主线池缓存|沪": (d, 0, 17)}


cal = ch.trade_days()
one_later = cal[cal > pd.Timestamp("2026-09-10")][0]
gaps = [(cal[i - 1], cal[i]) for i in range(1, len(cal))
        if (cal[i] - cal[i - 1]).days > 1]          # 真实休市段（周末/节假日）
ga, gb = gaps[-1]
today_real = pd.Timestamp.now().normalize().strftime("%Y-%m-%d")
s1, u1 = ch.decide_sessions(fake_ends("2026-09-24"), today="2026-09-28")  # 注入的交易日
s2, u2 = ch.decide_sessions(fake_ends("2026-09-10"), today="2026-09-28")  # 欠一周：只补一格
s3, u3 = ch.decide_sessions(fake_ends(ga.strftime("%Y-%m-%d")),
                            today=(ga + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
s3b, _ = ch.decide_sessions(fake_ends(gb.strftime("%Y-%m-%d")),
                            today=gb.strftime("%Y-%m-%d"))               # 末日就是今天
s4, u4 = ch.decide_sessions(fake_ends("2026-09-24", d2=today_real))      # 裂开的一支
_orig = ch.trade_days
try:
    def broken_cal():
        raise RuntimeError("模拟：日历接口挂了")
    ch.trade_days = broken_cal
    s5, u5 = ch.decide_sessions(fake_ends("2026-09-24"), today="2026-09-28")
finally:
    ch.trade_days = _orig
for i, s in enumerate([s1, s2, s3, s3b, s4, s5], 1):
    print(f"  支{i}: " + (u5 if not s else "  ".join(
        f"{k.replace('全市场镜像', '镜像').replace('主线池缓存', '池缓存')}={v}"
        for k, v in s.items())))
behind4 = [k for k, (p, adv) in s4.items() if adv]
t5 = (all(not u for u in (u1, u2, u3, u4))
      and all(adv is True for _, adv in s1.values())
      and "2026-09-24" < s1["全市场镜像|沪"][0] <= "2026-09-28"
      and s2["全市场镜像|沪"][0] == one_later.strftime("%Y-%m-%d")   # 不跳到最后一天
      and all(adv is False for _, adv in s3.values())                # 休市段中间
      and s3["全市场镜像|沪"][0] == gb.strftime("%Y-%m-%d")
      and all(adv is False for _, adv in s3b.values())               # 末日就是今天
      and len(behind4) == 2 and "全市场镜像|深" not in behind4        # 裂开：沪该补、深到位
      and s4["全市场镜像|深"][1] is False
      and s5 == {} and "取不到交易所日历" in u5)
print(f"T5 日历闸：四支读数都按方向出现——该补 {len(s1)} 张 / 没发生 {len(s3)} 张 / "
      f"裂开 {len(behind4)}/3 张该补（深市已到位）/ 取不到日历 {u5[:12]!r} "
      f"{'✅' if t5 else '❌'}")
if not t5:
    fails.append(f"T5 日历闸不成立：s1={s1} s2={s2} s3={s3} s3b={s3b} s4={s4} u5={u5}")

# ---------- T6 ② 的 `--auto` 到底改不改得动判据常量 ----------
# 链路文档要写「② 不碰判据」，这句话得有真字节背书。`ConfigUpdater.update_param`
# 是**正则改写 config.py 源码**的（`common/src/feedback/config_updater.py:34`），
# 所以不能拿生产文件试。这里把 `config_path` 指到 shell/ 下的**副本**上跑同一份代码，
# 两支都要出现：负=本线没有 `SLIPPAGE` 这个行首常量 ⇒ 返回 False、副本逐字节不变；
# 正=副本里真实存在的 `MIN_COMMISSION` ⇒ 返回 True 且文件真的被改（证明不是恒 False）。
import shutil
from config_updater import ConfigUpdater

CFG = os.path.join(SRC, "config", "config.py")
copy = os.path.join(TMP, "config_copy.py")
shutil.copy(CFG, copy)
before_bytes = open(copy, "rb").read()
neg = ConfigUpdater(config_path=copy).update_param("SLIPPAGE", 0.00123, "probe")
untouched = open(copy, "rb").read() == before_bytes
pos = ConfigUpdater(config_path=copy).update_param("MIN_COMMISSION", 0.456789, "probe")
changed = open(copy, "rb").read() != before_bytes
t6 = neg is False and untouched and pos is True and changed
print(f"T6 --auto 的写面：SLIPPAGE 改不动={not neg}（副本未变={untouched}）　"
      f"正对照 MIN_COMMISSION 改得动={pos}（文件变了={changed}） {'✅' if t6 else '❌'}")
if not t6:
    fails.append(f"T6 不成立：本线 config 里 SLIPPAGE 的存在性/正对照没对齐 "
                 f"(neg={neg}, untouched={untouched}, pos={pos}, changed={changed})")

print("\n" + ("\n".join("❌ " + m for m in fails) if fails else "六道反证全过 OK"))
sys.exit(1 if fails else 0)
