"""主入口：python main.py --mode pre|intra|post|backtest [--code CODE] [--price PRICE] [--action buy|sell]

示例：
  python main.py --mode pre                # 盘前生成交易计划
  python main.py --mode intra              # 盘中实时监控
  python main.py --mode post               # 盘后复盘
  python main.py --mode backtest           # 历史回测（默认 2026-01-01 ~ 2026-09-12）
  python main.py --mode backtest --start 2026-03-01 --end 2026-09-12
  python main.py --mode pre --action buy --code 510300 --price 3.50   # 记录买入
  python main.py --mode pre --action sell --price 3.60                # 记录卖出
"""
import argparse
import logging
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="场内ETF交易策略系统")
    parser.add_argument("--mode", required=True, choices=["pre", "intra", "post", "backtest"],
                        help="运行模式：pre=盘前 / intra=盘中 / post=盘后 / backtest=历史回测")
    parser.add_argument("--action", choices=["buy", "sell"],
                        help="交易执行记录（buy 需 --code 与 --price；sell 需 --price）")
    parser.add_argument("--code", help="ETF代码（买入时必填）")
    parser.add_argument("--price", type=float, help="成交价格")
    parser.add_argument("--quantity", type=int, default=1000, help="买入份额")
    parser.add_argument("--start", help="回测开始日期（默认 2026-01-01）")
    parser.add_argument("--end", help="回测结束日期（默认 2026-09-12）")
    parser.add_argument("--verbose", action="store_true", help="输出DEBUG日志")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from src.core.strategy import StrategyEngine
    from src.output.reporter import DISCLAIMER
    from src.runner import intra_day, post_market, pre_market

    engine = StrategyEngine()

    # ---- 交易执行记录 ----
    if args.action == "buy":
        if not args.code or not args.price:
            print("[错误] 买入需要 --code 与 --price 参数")
            return 1
        try:
            position = engine.apply_buy(args.code, args.price, args.quantity)
        except KeyError as err:
            print(f"[错误] {err}")
            return 1
        except ValueError as err:
            print(f"[错误] {err}")
            return 1
        print(f"[已记录] 买入 {position.name}({position.code}) "
              f"价格 {position.buy_price:.3f}，份额 {position.quantity}")
        print(DISCLAIMER)
        return 0
    if args.action == "sell":
        if not args.price:
            print("[错误] 卖出需要 --price 参数")
            return 1
        prev = engine.store.load()
        if prev.is_empty:
            print("[提示] 当前无持仓，无法卖出")
            print(DISCLAIMER)
            return 1
        position = engine.apply_sell(args.price)
        pnl = (args.price - prev.buy_price) * prev.quantity
        print(f"[已记录] 卖出 {prev.name}({prev.code})，成交价 {args.price:.3f}，"
              f"本次盈亏 {pnl:+.2f}，累计已实现盈亏 {position.realized_pnl:.2f}")
        print(DISCLAIMER)
        return 0

    # ---- 策略运行 ----
    if args.mode == "pre":
        pre_market.run_pre_market(engine)
    elif args.mode == "intra":
        intra_day.run_intra_day(engine)
    elif args.mode == "post":
        post_market.run_post_market(engine)
    elif args.mode == "backtest":
        from src.runner.backtest import run_backtest
        run_backtest(engine, start=args.start, end=args.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
