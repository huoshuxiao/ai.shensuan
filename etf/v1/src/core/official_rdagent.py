# -*- coding: utf-8 -*-
"""官方 RD-Agent(Q) 封装"""

import os
import json


def try_official_rdagent(output_dir="./rdagent_output"):
    try:
        from rdagent.app.qlib_rd_loop.factor import main as factor_main
    except Exception as e:
        print(f"  ⚠️ 官方 RD-Agent 不可用: {e}")
        return None

    print("  ✅ 官方 RD-Agent 已安装，开始执行 factor_mining_loop ...")
    os.makedirs(output_dir, exist_ok=True)

    try:
        factor_main()
    except Exception as e:
        print(f"  ⚠️ 官方执行失败: {e}")
        return None

    candidates = [
        os.path.join(output_dir, "factors.json"),
        os.path.join(output_dir, "result.json"),
        os.path.join(output_dir, "latest", "factors.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            factors = []
            for i, item in enumerate(data):
                factors.append({
                    "name": item.get("name", f"official_{i}"),
                    "expr": item.get("expr", item.get("expression", "")),
                    "mean_ic": item.get("IC", item.get("ic", 0.0)),
                    "icir": item.get("ICIR", item.get("icir", 0.0)),
                })
            print(f"  ✅ 官方产出 {len(factors)} 个因子")
            return factors
    print("  ⚠️ 未找到官方输出")
    return None