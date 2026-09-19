# -*- coding: utf-8 -*-
"""实盘启动脚本"""

import os
import json
import argparse
import pandas as pd
import _bootstrap  # noqa: F401  必须先于项目模块导入
from config_live import ACCOUNT, MODE
from paper_broker import PaperBroker
from live_engine import LiveEngine
from market_data import MarketDataManager


def load_optimized_config(path="optimized_params.json"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_broker():
    broker_name = ACCOUNT["broker"]
    if broker_name == "qmt":
        from qmt_broker import QMTBroker
        return QMTBroker()
    elif broker_name == "easytrader":
        from easytrader_broker import EasytraderBroker
        return EasytraderBroker()
    print("  ℹ️ 使用模拟盘")
    return PaperBroker(initial_capital=ACCOUNT["initial_capital"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=str,
                        default="510300,510500,513100,518880")
    parser.add_argument("--mode", type=str, default="paper",
                        choices=["qmt", "easytrader", "paper"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--auto-order", action="store_true")
    args = parser.parse_args()

    ACCOUNT["broker"] = args.mode
    MODE["live_trading"] = args.live
    MODE["auto_order"] = args.auto_order
    MODE["dry_run"] = not args.auto_order

    codes = args.codes.split(",")
    broker = create_broker()

    def simple_signal_fn(snapshot, positions, account, factor_weights):
        """简化信号：选价格最高的 ETF"""
        prices = {c: tick.get("price", 0) for c, tick in snapshot.items()}
        if not prices:
            return None
        target = max(prices, key=prices.get)
        return {"target_code": target, "prices": prices,
                "codes": list(prices.keys())}

    engine = LiveEngine(broker, simple_signal_fn)
    try:
        engine.start(codes)
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()