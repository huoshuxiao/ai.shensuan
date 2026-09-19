# -*- coding: utf-8 -*-
"""月度复盘脚本"""

import argparse
import _bootstrap  # noqa: F401  必须先于项目模块导入
from monthly_review import generate_monthly_review


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--lang", type=str, default="zh")
    parser.add_argument("--archive", action="store_true")
    args = parser.parse_args()
    print("=" * 60)
    print(f"  📅 月度复盘 | {args.days} 天")
    print("=" * 60)
    text = generate_monthly_review(days=args.days)
    if text:
        print("\n" + "─" * 60)
        print(text[:3000])
        print("─" * 60)
    print("\n🎉 完成！报告: live_data/report_monthly.md")


if __name__ == "__main__":
    main()