"""CLI 入口集成测试：main.py 交易记录路径的边界行为

覆盖盲区：此前从未测试 main.py 入口，导致池外代码 KeyError 崩溃、
无持仓卖出误导输出等问题未被发现。
通过 monkeypatch 隔离 StateStore 与 Reporter 的落盘路径，不污染真实状态。
"""
import sys

import pytest

import src.core.strategy as strategy_mod
from src.storage.state import StateStore
from src.output.reporter import Reporter


def run_cli(args: list[str], monkeypatch, tmp_path) -> int:
    """在隔离存储/报告目录下运行 main.main()"""
    monkeypatch.setattr(strategy_mod, "StateStore",
                        lambda: StateStore(tmp_path / "state.json"))
    monkeypatch.setattr(strategy_mod, "Reporter",
                        lambda: Reporter(tmp_path / "reports"))
    monkeypatch.setattr(sys, "argv", ["main.py"] + args)
    import main
    return main.main()


def state_file(tmp_path):
    return tmp_path / "state.json"


# ================= 参数校验 =================

def test_buy_missing_code_returns_error(monkeypatch, tmp_path, capsys):
    code = run_cli(["--mode", "pre", "--action", "buy", "--price", "3.50"],
                   monkeypatch, tmp_path)
    assert code == 1
    assert "买入需要 --code 与 --price" in capsys.readouterr().out
    assert not state_file(tmp_path).exists()  # 未写入状态


def test_buy_missing_price_returns_error(monkeypatch, tmp_path, capsys):
    code = run_cli(["--mode", "pre", "--action", "buy", "--code", "510300"],
                   monkeypatch, tmp_path)
    assert code == 1
    assert "买入需要 --code 与 --price" in capsys.readouterr().out


def test_sell_missing_price_returns_error(monkeypatch, tmp_path, capsys):
    code = run_cli(["--mode", "post", "--action", "sell"], monkeypatch, tmp_path)
    assert code == 1
    assert "卖出需要 --price" in capsys.readouterr().out


# ================= 边界行为 =================

def test_buy_unknown_code_friendly_error(monkeypatch, tmp_path, capsys):
    """池外代码不应崩溃，应给出友好错误（回归保护）"""
    code = run_cli(["--mode", "pre", "--action", "buy",
                    "--code", "999999", "--price", "3.50"], monkeypatch, tmp_path)
    assert code == 1
    out = capsys.readouterr().out
    assert "ETF代码 999999 不在标的池中" in out
    assert "Traceback" not in out


def test_sell_without_position_hint(monkeypatch, tmp_path, capsys):
    """无持仓卖出应提示，而非误报盈亏 0.00（回归保护）"""
    code = run_cli(["--mode", "post", "--action", "sell", "--price", "3.60"],
                   monkeypatch, tmp_path)
    assert code == 1
    out = capsys.readouterr().out
    assert "当前无持仓，无法卖出" in out
    assert "累计已实现盈亏 0.00" not in out


def test_buy_insufficient_capital_friendly_error(monkeypatch, tmp_path, capsys):
    """买入金额超过初始资金（1W）时应友好提示而非崩溃"""
    code = run_cli(["--mode", "pre", "--action", "buy",
                    "--code", "510300", "--price", "99.99"], monkeypatch, tmp_path)
    assert code == 1
    out = capsys.readouterr().out
    assert "资金不足" in out
    assert "Traceback" not in out
    assert not state_file(tmp_path).exists()  # 未写入状态


# ================= 完整流程 =================

def test_buy_then_sell_full_flow(monkeypatch, tmp_path, capsys):
    """买入 -> 卖出：盈亏计算、状态落盘、合规声明"""
    code = run_cli(["--mode", "pre", "--action", "buy",
                    "--code", "510300", "--price", "3.50"], monkeypatch, tmp_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "已记录" in out and "510300" in out
    assert "合规声明" in out  # 交易记录同样带合规声明

    # 状态已落盘
    store = StateStore(state_file(tmp_path))
    pos = store.load()
    assert not pos.is_empty and pos.buy_price == 3.50 and pos.quantity == 1000

    code = run_cli(["--mode", "post", "--action", "sell", "--price", "3.60"],
                   monkeypatch, tmp_path)
    assert code == 0
    out = capsys.readouterr().out
    assert "本次盈亏 +100.00" in out      # (3.60-3.50)*1000
    assert "累计已实现盈亏 100.00" in out
    assert "合规声明" in out

    # 卖出后空仓且累计盈亏保留
    pos = store.load()
    assert pos.is_empty
    assert pos.realized_pnl == pytest.approx(100.0)
