# -*- coding: utf-8 -*-
"""多 LLM 生成脚本"""

import argparse
import os
from feedback.multi_llm_generator import (
    run_multi_llm_generation,
    run_multi_llm_generation_batch,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=0)
    parser.add_argument("--report-path", type=str, default=None)
    args = parser.parse_args()
    if args.days > 0:
        results = run_multi_llm_generation_batch(args.days)
        for r in results:
            print(f"  {os.path.basename(r['file'])}: "
                  f"胜出 {r['winner']}")
    else:
        result = run_multi_llm_generation(
            report_path=args.report_path)
        if result:
            print(f"\n  🏆 胜出: {result['final']['model']}")


if __name__ == "__main__":
    main()