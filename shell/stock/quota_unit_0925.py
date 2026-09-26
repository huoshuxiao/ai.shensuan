"""选项E 落地的最小自检：apply_board_quota 的四条行为 + 两个开关的报错分支。

不碰面板、不读账本，纯函数级断言 ⇒ 秒级跑完，用来在重量 39 行账单之前先把
「名单形状」这件事钉死（回填、名次重排、席位够不满时报不报）。
"""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "stock", "v1", "src")
sys.path.insert(0, os.path.abspath(SRC))
import _bootstrap  # noqa: F401

import pandas as pd

from config import ASHARE_LIST_QUOTA, ASHARE_LIST_SCHEME
import ashare_screen as S

FAILS = []


def ck(name, got, want):
    ok = got == want
    print(f"{'✅' if ok else '❌'} {name}：got={got!r} want={want!r}")
    if not ok:
        FAILS.append(name)


# 造 60 只候选：主板 46 / 创业板 6 / 北交所 4 / 科创板 4，轴值就是行号（升序已排好）
def make(n_main=46, n_cy=6, n_kc=4, n_bj=4, interleave=True):
    codes, vals = [], []
    groups = [("SZ00%04d" % i, "主板") for i in range(n_main)] \
        + [("SZ30%04d" % i, "创业板") for i in range(n_cy)] \
        + [("SH688%03d" % (i % 1000), "科创板") for i in range(n_kc)] \
        + [("BJ8%04d" % i, "北交所") for i in range(n_bj)]
    if interleave:                     # 段与段交错，模拟「安静度不天然分段」
        for r in range(max(n_main, n_cy, n_kc, n_bj)):
            for g in ("主板", "创业板", "科创板", "北交所"):
                sel = [x for x in groups if x[1] == g]
                if r < len(sel):
                    codes.append(sel[r][0])
    else:
        codes = [c for c, _ in groups]
    vals = list(range(len(codes)))
    return pd.Index(codes), pd.Series(vals, index=codes)


codes, order = make()
ck("候选总数", len(codes), 60)
# board_of 认的是代码前缀，这里造的头必须落在预期的段里（否则整份自检在测空气）
ck("board_of 前缀判定", [S.board_of(c) for c in codes[:5]],
   ["主板", "创业板", "科创板", "北交所", "主板"])

# ---- 1. quota 档：席位 20/10/10/10，段内按轴升序，够不满就回填 ----
S.ASHARE_LIST_SCHEME = "quota"
head, st = S.apply_board_quota(order.index, top_n=50, boards=order.index.map(S.board_of))
ck("quota 档取满 50", len(head), 50)
ck("quota 档名单最终构成（含回填）", st["n_by_board"],
   {"主板": 36, "创业板": 6, "科创板": 4, "北交所": 4})
ck("quota 档席位腿构成（回填不算）", st["n_leg_by_board"],
   {"主板": 20, "创业板": 6, "科创板": 4, "北交所": 4})
ck("quota 档回填只数（席位 50 − 有货 34）", st["n_backfilled"], 16)
ck("quota 档没凑满名单的天数", st["n_short"], 0)
ck("没货的段（按席位腿，主板那 20 格是满的所以不在列）", st["quota_unmet"],
   {"创业板": 4, "科创板": 6, "北交所": 6})
ck("席位表原样报出", st["quota"], ASHARE_LIST_QUOTA)
# 名次必须**按轴重排**，不是按进来的先后
pos = [order.loc[c] for c in head]
ck("名单内名次按轴升序", pos == sorted(pos), True)
# 回填拿的是被跳过里最安静的那批（前 16 只主板）
ck("回填段仍按轴升序", [S.board_of(c) for c in head[34:]], ["主板"] * 16)

# ---- 2. global 档必须与「直接取前 n」逐字相同（旧口径回归） ----
S.ASHARE_LIST_SCHEME = "global"
head_g, st_g = S.apply_board_quota(order.index, top_n=50, boards=order.index.map(S.board_of))
ck("global 档 = 轴上前 50", list(head_g), list(order.index[:50]))
ck("global 档不回填", st_g["n_backfilled"], 0)
ck("global 档口径自报", st_g["scheme"], "global")
S.ASHARE_LIST_SCHEME = "quota"

# ---- 3. 候选不足 top_n：两套口径都不许造出第 51 只 ----
small = order.iloc[:40]
for sch in ("global", "quota"):
    S.ASHARE_LIST_SCHEME = sch
    h2, st2 = S.apply_board_quota(small.index, top_n=50,
                                  boards=small.index.map(S.board_of))
    ck(f"{sch} 档候选只有 40 只时", (len(h2), st2["n_short"]), (40, 10))

# ---- 4. list_desc 要念得出席位表（三处口径措辞共用它） ----
d = S.list_desc()
print(f"[list_desc] {d}")
ck("list_desc 含席位合计", f"合计 {sum(ASHARE_LIST_QUOTA.values())}" in d, True)
ck("list_desc 含回填二字", "回填" in d, True)

# ---- 5. 默认档 = quota（用户拍的「改」），且席位合计 50 = 名单行数 ----
ck("config 默认档", ASHARE_LIST_SCHEME, "quota")
ck("席位合计 = ASHARE_BUY_TOP_N", sum(ASHARE_LIST_QUOTA.values()), 50)

# ---- 6. 两个开关的报错分支：拼错必须当场死，不许静默退回默认档 ----
import subprocess

for env, want_kw in (("STOCK_LIST_SCHEME=quota2", "global|quota"),
                     ("STOCK_LIST_QUOTA=20/10/10", "四段整数")):
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); import _bootstrap; "
                        "import config" % os.path.abspath(SRC)],
                       env=dict(os.environ, **dict([env.split("=", 1)])),
                       capture_output=True, text=True)
    hit = want_kw in (r.stderr + r.stdout)
    ck(f"config 拒收 {env.split('=')[1]!r}（exit={r.returncode}）",
       (r.returncode != 0, hit), (True, True))

print(f"\n共 {len(FAILS)} 条不过" + (f"：{FAILS}" if FAILS else "，全过 ✅"))
sys.exit(1 if FAILS else 0)
