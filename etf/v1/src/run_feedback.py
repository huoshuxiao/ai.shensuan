# -*- coding: utf-8 -*-
"""反馈批处理"""

import argparse
import _bootstrap  # noqa: F401  必须先于项目模块导入


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--llm", type=str, default="",
                        choices=["", "daily", "weekly",
                                 "incident", "summary", "all"])
    parser.add_argument("--lang", type=str, default="zh")
    parser.add_argument("--monthly", action="store_true")
    parser.add_argument("--monthly-days", type=int, default=20)
    parser.add_argument("--quarterly", action="store_true")
    parser.add_argument("--annual", action="store_true")
    parser.add_argument("--self-eval", action="store_true")
    parser.add_argument("--blind-spot", action="store_true")
    parser.add_argument("--multi-gen", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("  反馈闭环")
    print("=" * 60)

    if args.self_eval:
        from self_evaluator import run_self_evaluation
        result = run_self_evaluation(days=args.monthly_days)
        if result:
            print(f"\n  平均分: {result['summary']['avg_score']}")

    if args.blind_spot:
        from blind_spot_detector import detect_blind_spots
        detect_blind_spots(days=args.monthly_days)

    if args.monthly:
        from monthly_review import generate_monthly_review
        generate_monthly_review(days=args.monthly_days)

    if args.quarterly:
        from quarterly_review import generate_quarterly_review
        generate_quarterly_review(n_months=3)

    if args.annual:
        from quarterly_review import generate_annual_review
        generate_annual_review(n_months=12)

    if args.multi_gen:
        from multi_llm_generator import (
            run_multi_llm_generation)
        run_multi_llm_generation()

    print("\n🎉 完成")


if __name__ == "__main__":
    main()