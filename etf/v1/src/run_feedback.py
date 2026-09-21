# -*- coding: utf-8 -*-
"""反馈批处理"""

import argparse
import _bootstrap  # noqa: F401  必须先于项目模块导入
from log_kit import setup_logging


def main():
    setup_logging("feedback")
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true",
                        help="按滑点统计自动回写 config 建议值")
    parser.add_argument("--llm", type=str, default="",
                        choices=["", "daily", "summary", "all"],
                        help="反馈分析后生成 LLM 报告")
    parser.add_argument("--monthly", action="store_true")
    parser.add_argument("--monthly-days", type=int, default=20)
    parser.add_argument("--quarterly", action="store_true")
    parser.add_argument("--annual", action="store_true")
    parser.add_argument("--self-eval", action="store_true")
    parser.add_argument("--blind-spot", action="store_true")
    parser.add_argument("--multi-gen", action="store_true")
    parser.add_argument("--vote-eval", action="store_true",
                        help="多 LLM 投票评估历史日报")
    parser.add_argument("--blind-eval", action="store_true",
                        help="对多模型日报做跨模型盲评排名")
    parser.add_argument("--ab-test", action="store_true",
                        help="提示词 A/B 测试")
    parser.add_argument("--ab-apply", action="store_true",
                        help="A/B 测试且 B 胜时自动应用新提示词")
    parser.add_argument("--optimize-prompt", action="store_true",
                        help="根据自评结果 LLM 改写提示词（需先 --self-eval）")
    args = parser.parse_args()

    print("=" * 60)
    print("  反馈闭环")
    print("=" * 60)

    from feedback_engine import run_feedback
    run_feedback(auto_update=args.auto)

    if args.llm:
        from llm_report_generator import (
            generate_daily_report, generate_summary)
        if args.llm in ("daily", "all"):
            text = generate_daily_report()
            if text:
                print("  ✅ 日报已生成: report/report_daily.md")
        if args.llm in ("summary", "all"):
            summary = generate_summary()
            if summary:
                print("\n" + summary)

    if args.self_eval:
        from self_evaluator import run_self_evaluation
        result = run_self_evaluation(days=args.monthly_days)
        if result:
            avg = result.get("summary", {}).get("avg_score")
            if avg is None:
                print("  ⚠️ 无可自评的日报快照"
                      "（先运行 --llm daily 生成当日日报）")
            else:
                print(f"\n  平均分: {avg}")

    if args.optimize_prompt:
        from prompt_optimizer import optimize_prompt
        res = optimize_prompt()
        if res:
            print(f"\n  提示词已改写: {res.get('changes')}")
        else:
            print("  ⚠️ 提示词优化跳过：需先配置 LLM 端点"
                  "且存在自评汇总 self_eval_summary.json")

    if args.vote_eval:
        from multi_llm_voter import run_voting_evaluation
        res = run_voting_evaluation(days=args.monthly_days)
        avg = (res or {}).get("summary", {}).get("avg_score")
        if avg is not None:
            print(f"\n  投票平均分: {avg}")
        else:
            print("  ⚠️ 投票评估跳过：无可用 LLM 端点或无日报快照")

    if args.blind_eval:
        from multi_llm_blind_eval import evaluate_historical_reports
        res = evaluate_historical_reports()
        if res and res.get("ranking"):
            print("\n  盲评排名:", res.get("ranking"))
        else:
            print("  ⚠️ 盲评跳过：无可用 LLM 模型或历史日报不足")

    if args.ab_test or args.ab_apply:
        if args.ab_apply:
            from ab_test import auto_ab_test_and_apply
            auto_ab_test_and_apply()
        else:
            from ab_test import run_prompt_ab_test
            res = run_prompt_ab_test()
            if res:
                print(f"\n  A/B 结论: {res['summary']}")
            else:
                print("  ⚠️ A/B 测试跳过：未配置 LLM 端点"
                      "（OPENAI_API_KEY 或 ETF_LLM_BASE_URL）")

    if args.blind_spot:
        from blind_spot_detector import detect_blind_spots
        res = detect_blind_spots(days=args.monthly_days)
        if res:
            print(f"\n  🔍 盲区检测（规则法，{res.get('n_days', 0)} 天日报）: "
                  f"从未提及 {len(res.get('never_mentioned', []))} 项、"
                  f"很少提及 {len(res.get('rarely_mentioned', []))} 项"
                  " → report/blind_spots.md")
        else:
            print("  ⚠️ 盲区检测跳过：无 report_daily*.json 快照可分析")

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
        res = run_multi_llm_generation()
        if not res or not res.get("final"):
            print("  ⚠️ 多模型生成跳过：无可用 LLM 模型"
                  "（配置端点后重试）")

    print("\n🎉 完成")


if __name__ == "__main__":
    main()
