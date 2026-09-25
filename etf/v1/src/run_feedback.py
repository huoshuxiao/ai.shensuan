# -*- coding: utf-8 -*-
"""反馈批处理

提示词自改写（--optimize-prompt / --ab-apply）在本文件里带一道"校验 + 自动
回滚"：两个写入口都是拿 LLM 输出的整段文本 `re.sub` 覆写
`report/report_prompts.py`，只备份、不校验，坏版本会直接活到下一次 import ——
`common/src/report/prompt_optimizer.py:71-79`（自改写）与
`common/src/report/ab_test.py:152-166`（A/B 应用胜出版）。这里不改共享层
（股票线同用这两个模块），只在 ETF 侧的调用点包一层。
"""

import argparse
import os
import re
import _bootstrap  # noqa: F401  必须先于项目模块导入
from log_kit import setup_logging
from config import REPORT_DIR

# 两个写入口用的都是这个相对 cwd 的路径（prompt_optimizer.py:55/:72，
# ab_test.py:154），所以本线的守卫也只看这一处文件
PROMPT_FILE = "report/report_prompts.py"


def check_prompt_file(path=PROMPT_FILE):
    """提示词文件的健康检查，返回问题描述；None 表示合格。

    顺序有讲究：覆写最常见的坏法是 LLM 把三引号写进正文，`re.sub` 之后整个
    文件语法断裂，下一轮任何入口 import 就崩 —— 所以语法可编译排第一，
    字段还在排第二，长度区间排第三（太短=被截断，太长=提示词里塞进了日报正文）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return f"读不到 {path}: {type(e).__name__}: {e}"
    try:
        compile(content, path, "exec")
    except SyntaxError as e:
        return f"覆写后语法断裂: {e.msg} (line {e.lineno})"
    m = re.search(r'SYSTEM_PROMPT_ZH\s*=\s*"""(.*?)"""', content, re.S)
    if not m:
        return "SYSTEM_PROMPT_ZH 段不在了（覆写没落在原位）"
    n = len(m.group(1).strip())
    if not 100 <= n <= 8000:
        return f"提示词长度 {n} 字越出可用区间 [100, 8000]"
    return None


def guarded_prompt_write(fn, what):
    """执行 fn()（它会覆写提示词文件），随后校验；不合格自动回滚到改前快照。

    保持全自动：不需要人工替换文本。`prompt_optimizer` 自己那份
    `DATA_DIR/prompt_backups/` 只是留档（ab_test 会读它取候选，但没有任何一方
    拿它做恢复），所以这里另存一份内存快照，专门用来就地还原。"""
    before = None
    if os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, encoding="utf-8") as f:
            before = f.read()
    else:
        print(f"  ℹ️ 本线 cwd 下没有 {PROMPT_FILE}：这条通道此刻写不到任何"
              f"文件（提示词迭代未接线），仍按现状执行校验流程")
    res = fn()
    if before is None:
        return res
    with open(PROMPT_FILE, encoding="utf-8") as f:
        after = f.read()
    if after == before:
        print(f"  {what}：提示词文件未变更")
    else:
        bad = check_prompt_file()
        if bad:
            with open(PROMPT_FILE, "w", encoding="utf-8") as f:
                f.write(before)
            print(f"  ⚠️ {what} 产物不合格，已自动回滚：{bad}")
        else:
            print(f"  ✅ {what}：已改写并通过校验（语法/字段/长度）")
    return res


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
        from self_evaluator import SelfEvaluator
        from llm_selfreport import save_self_eval_dims
        ev = SelfEvaluator()
        result = ev.eval_batch(days=args.monthly_days)
        if result:
            # 共享层只落 final_score/grade，四维与"这一轮到底是不是 LLM 评的"
            # 都在内存里丢掉了，这里在本线补一份（判据不变，只是可读）
            dims_path = f"{REPORT_DIR}/self_eval_dims.json"
            payload = save_self_eval_dims(result, ev.enabled, dims_path)
            # 无端点时共享层给的是模板分（四维恒 20、grade 恒 A），不标出来
            # 下次就会被当成质量读数
            how = "LLM 实评" if ev.enabled else "模板分（无端点，非模型所评）"
            print(f"  📄 自评四维已落盘: {dims_path}（评法={how}）")
            # 日更只评当日 1 份 ⇒ 必须看得见累计，否则一周后无从分辨
            # "今天没跑" 和 "每天都跑了"
            print(f"  本批 {payload.get('n_batch')} 天 / "
                  f"累计 {payload.get('n_days_total')} 天"
                  f"（{dims_path} 按日期合并）")
            if not payload.get("n_batch"):
                print("  ⚠️ 本批 0 份：共享层 SelfEvaluator._save 是整表覆写，"
                      "self_eval_detail.csv 与 self_eval_summary.json "
                      "已被写成空 —— 只有本线的 dims 文件保住了历史")
            avg = result.get("summary", {}).get("avg_score")
            if avg is None:
                print("  ⚠️ 无可自评的日报快照"
                      "（先运行 --llm daily 生成当日日报）")
            else:
                print(f"\n  本批平均分: {avg}")

    if args.optimize_prompt:
        from prompt_optimizer import optimize_prompt
        res = guarded_prompt_write(optimize_prompt, "提示词自动改写")
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
            guarded_prompt_write(auto_ab_test_and_apply, "A/B 自动应用")
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
