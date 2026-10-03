# -*- coding: utf-8 -*-
"""ETF 风险数据探源·第三弹微探针（0924）：历史深度 + ETF→指数映射字段
结论见同名 .log；配合 probe_etf_risk_data_0924.py / _data2_ 阅读。
"""
import socket, time
socket.setdefaulttimeout(25)
import pandas as pd, akshare as ak
pd.set_option("display.width",220); pd.set_option("display.max_rows",60)

for dt in ("20180102","20240102"):
    try:
        t0=time.time(); d=ak.fund_etf_scale_sse(date=dt)
        r=d[d["基金代码"].astype(str)=="510300"]
        print(f"sse_scale {dt}: OK rows={len(d)} {time.time()-t0:.1f}s  510300={r['基金份额'].values}")
    except Exception as e:
        print(f"sse_scale {dt}: FAIL {type(e).__name__}: {str(e)[:100]}")
try:
    t0=time.time(); d=ak.fund_etf_spot_ths(date="20180102")
    r=d[d["基金代码"].astype(str)=="510300"]
    print(f"ths_nav 20180102: OK rows={len(d)} {time.time()-t0:.1f}s  510300当前-单位净值={r['当前-单位净值'].values}")
except Exception as e:
    print(f"ths_nav 20180102: FAIL {type(e).__name__}: {str(e)[:100]}")
try:
    print("fund_info_ths(510300) 全字段:")
    print(ak.fund_info_ths(symbol="510300").to_string(index=False))
except Exception as e:
    print(f"fund_info_ths FAIL {type(e).__name__}: {str(e)[:100]}")
