# -*- coding: utf-8 -*-
"""实盘启动脚本

研究→实盘衔接有两处，缺一不可：
1. 参数：读取研究管线落盘的 `data/results/optimized_params_{FREQ}.json`
   （因子 ICIR 权重 + 调参后的风控参数），覆盖 config_live.LIVE_RISK；
2. 信号：用因子库（data/library）中的活跃因子表达式对订阅标的打分，
   再走研究侧同一份 select_weights 得出目标组合（只数/权重与回测同口径）；
   因子库为空说明研究管线（main.py）尚未产出，拒绝启动并提示先跑研究。
券商默认走 config_live.ACCOUNT["broker"]（模拟盘 paper），
--mode qmt/easytrader + --live --auto-order 才触碰真实账户。"""

import os
import json
import argparse
import numpy as np
import pandas as pd
import _bootstrap  # noqa: F401  必须先于项目模块导入
from log_kit import setup_logging
from config import RESULTS_DIR, UNIVERSE_CACHE, FREQ
from config_live import ACCOUNT, MODE, LIVE_RISK
from strategy import select_weights
from paper_broker import PaperBroker
from live_engine import LiveEngine
from market_data import MarketDataManager


def load_optimized_config(path=f"{RESULTS_DIR}/optimized_params_{FREQ}.json"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# 研究 risk_params → 实盘 LIVE_RISK 的语义对齐映射
# （min_bars_between_trades 无对应项：实盘按 tick 轮询而非按 bar 推进）
RISK_KEY_MAP = {
    "daily_stop_loss": "daily_stop_loss",
    "max_drawdown_stop": "max_drawdown_stop",
    "single_position_max": "max_position_ratio",
    "max_trades_per_day": "max_orders_per_day",
}

# 每个落地键的取值区间 [lo, hi]。为什么不止查类型：`JOINT_LLM["enabled"]` 时
# risk_params 出自 joint_optimizer.py:68 的 `{**RISK_CONTROL, **risk}` ——
# LLM 给的风控数字**原样生效**（同一函数里 ortho 那半有 clamp[0.3,0.9] 与方法
# 白名单，risk 这半没有）。"冠军由代码按 DSR 选"救不了这一项：代码选的是轮次，
# 赢的那轮风控数字仍是 LLM 打的字。越界的后果是风控**静默失效**而不是报错：
# daily_stop_loss 只要为正数，live_risk.py:41 的 `r <= p["daily_stop_loss"]`
# 永不成立 = 日止损关闭；NaN 会让所有比较短路。
RISK_BOUNDS = {
    # 止损/熔断语义是"跌到该幅度即停"，必须为负
    "daily_stop_loss": (-0.50, -0.005),
    "max_drawdown_stop": (-0.80, -0.02),
    # 单仓比例 >1 隐含加杠杆，≤0 则是永不建仓
    "max_position_ratio": (0.05, 1.00),
    "max_orders_per_day": (1, 50),
    # 上限 10080 分钟 = 7 天挂钟：下面 cooldown_days 的换算口径是 ×24×60
    # （整段非交易时段都算），研究侧 5 个交易日 → 7200 分钟是合法长冷却，
    # 卡到一天会把正常口径误判成越界
    "cooldown_minutes": (1, 10080),
}

# 这两个键在 LIVE_RISK 里是整数计数（订单数/分钟数），小数无意义
RISK_INT_KEYS = {"max_orders_per_day", "cooldown_minutes"}


def sanitize_live_risk(key, v):
    """实盘风控键的区间校验：合格返回数值，越界/非数值返回 None（调用方保留现值）。

    bool 要显式挡（`isinstance(True, int)` 为真），NaN/inf 也要挡。"""
    if key not in RISK_BOUNDS:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not np.isfinite(v):
        return None
    if key in RISK_INT_KEYS and not float(v).is_integer():
        return None
    lo, hi = RISK_BOUNDS[key]
    if not lo <= v <= hi:
        print(f"  ⚠️ 风控 {key}={v} 越出实盘可接受区间 [{lo}, {hi}]，"
              f"不落地，沿用现值 {LIVE_RISK.get(key)}"
              f"（调参产物含不可信数字：联合优化开着时 risk 段来自 LLM）")
        return None
    return int(v) if key in RISK_INT_KEYS else float(v)


def apply_optimized_config(cfg):
    """把研究管线的调参产物落到实盘配置，返回 factor_weights。

    此前 main.py 写了 optimized_params_{FREQ}.json 却无人读取，实盘等于
    用另一套参数下单——研究结论无法传导到执行环节。风控项按语义映射覆盖
    LIVE_RISK，权重项交给信号函数按 ICIR 加权打分。"""
    if not cfg:
        print("  ⚠️ 未找到研究侧调参产物，实盘沿用 config_live 默认参数")
        return {}
    rp = cfg.get("risk_params") or {}
    for src, dst in RISK_KEY_MAP.items():
        v = sanitize_live_risk(dst, rp.get(src))
        if v is None:
            continue
        print(f"  风控 {dst}: {LIVE_RISK.get(dst)} → {v}（研究调参）")
        LIVE_RISK[dst] = v
    # 冷却口径换算：实盘熔断按挂钟时间判定
    # （live_risk: cooldown_until = now + timedelta(minutes=...)），
    # 而研究侧按交易日计数。×24×60 把整段非交易时段（夜间/周末）也算进冷却，
    # 比"交易日×240 分钟"更长——风控参数宁长勿短，短了会在研究判定
    # 仍该观望时就重新进场。
    cd = rp.get("cooldown_days")
    if not isinstance(cd, bool) and isinstance(cd, (int, float)) \
            and np.isfinite(cd):
        minutes = sanitize_live_risk("cooldown_minutes", int(cd * 24 * 60))
        if minutes is not None:
            print(f"  风控 cooldown_minutes: "
                  f"{LIVE_RISK.get('cooldown_minutes')} → {minutes}"
                  f"（研究 cooldown_days={cd}，按挂钟时间计）")
            LIVE_RISK["cooldown_minutes"] = minutes
    w = cfg.get("factor_weights") or {}
    if w:
        print(f"  因子权重: 取研究侧 ICIR 权重 {len(w)} 个 "
              f"(freq={cfg.get('freq')} n_trials={cfg.get('n_trials')})")
    else:
        print("  ⚠️ 调参产物无因子权重，信号退化为等权")
    return w


def default_codes():
    """优先用研究管线的 ETF 池缓存（指数代表），无缓存退回经典宽基"""
    if os.path.exists(UNIVERSE_CACHE):
        try:
            df = pd.read_csv(UNIVERSE_CACHE, dtype={"code": str})
            codes = df["code"].str.zfill(6).tolist()
            if codes:
                print(f"  订阅标的取自 ETF 池缓存: {len(codes)} 只")
                return codes
        except Exception as e:
            print(f"  ⚠️ 池缓存读取失败，退回默认标的: "
                  f"{type(e).__name__}: {e}")
    return ["510300", "510500", "513100", "518880"]


def build_factor_signal_fn(codes):
    """用因子库活跃因子构造实盘信号：
    score(code) = Σ |wᵢ|·符号(ICᵢ)·tanh(因子最新值) / Σ|wᵢ|，
    再交给 select_weights 变成目标组合（top_k 只 + 单标的上限）。
    wᵢ 取研究侧 ICIR 权重（apply_optimized_config 传入），缺失时等权。
    日线数据按日缓存，避免每 30s 轮询都重新拉数据。"""
    from factor_library import get_library
    from factor_dsl import safe_eval
    from data_loader import DataLoader

    lib = get_library()
    # get_active() 返回因子名列表，需回查 lib.factors 取完整定义
    active = [dict(lib.factors[n], name=n) for n in lib.get_active()
              if lib.factors.get(n, {}).get("expr")]
    if not active:
        print("  ❌ 因子库无活跃因子：请先运行 main.py 完成研究管线，"
              "再启动实盘")
        return None
    active.sort(key=lambda f: -abs(f.get("ic", 0.0)))
    active = active[:10]
    print(f"  实盘信号因子 {len(active)} 个: "
          f"{[f['name'] for f in active]}")

    loader = DataLoader(freq="daily")
    cache = {"pool": None, "day": None}

    def _recent_pool():
        today = pd.Timestamp.now().date()
        if cache["pool"] is None or cache["day"] != today:
            cache["pool"] = loader.load_pool(codes)
            cache["day"] = today
        return cache["pool"]

    def signal_fn(snapshot, positions, account, factor_weights):
        pool = _recent_pool()
        # 实盘打分用 tanh(原始值) 而非回测侧的时间序列 z-score（无历史窗口），
        # 故权重只提供占比、方向仍由因子 IC 符号决定；权重缺失则等权
        ws = {f["name"]: abs((factor_weights or {}).get(f["name"], 0.0))
              for f in active}
        if sum(ws.values()) <= 0:
            ws = {f["name"]: 1.0 for f in active}
        w_sum = sum(ws.values())
        scores = {}
        for code in snapshot:
            df = pool.get(code)
            if df is None or len(df) < 60:
                continue
            s = 0.0
            for f in active:
                try:
                    v = float(safe_eval(f["expr"], df).iloc[-1])
                except Exception:
                    continue
                if not np.isfinite(v):
                    continue
                # IC<0 的因子反向计分；tanh 压到 [-1,1] 使不同量纲可加
                s += (1.0 if f.get("ic", 0.0) >= 0 else -1.0) \
                    * ws[f["name"]] / w_sum * float(np.tanh(v))
            scores[code] = s
        if not scores:
            return None
        # 与回测侧同一份选股/权重函数：top_k、min_score、max_weight 不漂移
        targets = select_weights(scores)
        prices = {c: t.get("price", 0) for c, t in snapshot.items()}
        if targets:
            print(f"  信号: 目标组合 {len(targets)} 只 "
                  f"(最高 {max(targets, key=targets.get)} "
                  f"w={max(targets.values()):.2f}, 候选 {len(scores)})")
        else:
            print(f"  信号: 全部标的分数不过门槛 → 空仓（候选 {len(scores)}）")
        return {"targets": targets, "prices": prices,
                "codes": list(prices.keys())}

    return signal_fn


def create_broker():
    broker_name = ACCOUNT["broker"]
    if broker_name == "qmt":
        from qmt_broker import QMTBroker
        return QMTBroker()
    elif broker_name == "easytrader":
        from easytrader_broker import EasytraderBroker
        return EasytraderBroker()
    print("  ℹ️ 券商通道: 模拟盘 paper（默认，不触碰真实账户）")
    return PaperBroker(initial_capital=ACCOUNT["initial_capital"])


def main():
    setup_logging("live")
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=str, default="",
                        help="逗号分隔；默认取 ETF 池缓存")
    parser.add_argument("--mode", type=str, default=None,
                        choices=["qmt", "easytrader", "paper"],
                        help="默认沿用 config_live.ACCOUNT['broker']（模拟盘）")
    parser.add_argument("--live", action="store_true",
                        help="实盘标记（还需 --auto-order 才真正下单）")
    parser.add_argument("--auto-order", action="store_true")
    args = parser.parse_args()

    if args.mode:
        ACCOUNT["broker"] = args.mode
    MODE["live_trading"] = args.live
    MODE["auto_order"] = args.auto_order
    MODE["dry_run"] = not args.auto_order
    print(f"  运行模式: broker={ACCOUNT['broker']} "
          f"live_trading={MODE['live_trading']} "
          f"auto_order={MODE['auto_order']} dry_run={MODE['dry_run']}")
    if MODE["live_trading"] and MODE["auto_order"]:
        print("  ⚠️ 真实下单模式，请确认券商配置！")

    # 就地更新 LIVE_RISK（LiveRiskController 持同一 dict 引用，故先后皆可）
    factor_weights = apply_optimized_config(load_optimized_config())

    codes = args.codes.split(",") if args.codes else default_codes()
    codes = [c.strip().zfill(6) for c in codes if c.strip()]
    broker = create_broker()

    signal_fn = build_factor_signal_fn(codes)
    if signal_fn is None:
        return
    engine = LiveEngine(broker, signal_fn, factor_weights=factor_weights)
    try:
        engine.start(codes)
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
