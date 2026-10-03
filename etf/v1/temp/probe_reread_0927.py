# -*- coding: utf-8 -*-
"""一次性探针（D 那 3 次重读到底是谁）：复用 appcheck 的沙箱搭法，只跑两遍整页

跑法：`/usr/bin/python3.10 etf/v1/temp/probe_reread_0927.py`
"""
import os
import runpy
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "appcheck_etf_dashboard_refresh_0927.py")

head = open(SCRIPT, encoding="utf-8").read().split('print("[0]')[0]
ns = {"__file__": SCRIPT}
exec(compile(head, SCRIPT, "exec"), ns, ns)
ns["build_sandbox"]()

BODY = r"""
import collections
import pandas as pd
_real = pd.read_csv
HITS = collections.Counter()

def counting(*a, **k):
    p = a[0] if a else k.get("filepath_or_buffer")
    HITS[str(p)] += 1
    return _real(*a, **k)

pd.read_csv = counting
at = new_app()
at.run()
first = dict(HITS)
HITS.clear()
at.run()
second = dict(HITS)
print("@@@ n_first = %d n_second = %d" % (len(first), len(second)))
for p in sorted(set(second) - set(first)):
    print("@@@ REREAD_NEW = %s" % p)
for p in sorted(set(first) & set(second)):
    if first[p] != second[p]:
        print("@@@ REREAD_COUNT = %s  %d -> %d" % (p, first[p], second[p]))
"""

p = subprocess.run([sys.executable, "-c", ns["CHILD_HEAD"] + BODY],
                   capture_output=True, text=True, cwd=ns["SRC"], timeout=900)
print(p.stdout[-3000:])
print(p.stderr[-1200:])
