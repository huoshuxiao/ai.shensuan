"""报告输出测试：终端格式（capsys 捕获 stdout）与 Markdown 落盘

覆盖盲区：此前只验证 signal 数据对象，从未验证 print_* 的
终端输出格式，导致 ranking 超长单行问题未被发现。
本文件对输出格式与 Markdown 落盘做回归保护。
"""
import json
from datetime import datetime

from src.output.models import TradeSignal
from src.output.reporter import Reporter


def make_signal(detail: dict | None = None) -> TradeSignal:
    return TradeSignal(
        mode="pre", signal="BUY", code="512480", name="半导体ETF",
        price=1.234, score=73.75, reason="评分达标",
        detail=detail if detail is not None else {},
        generated_at="2026-09-13 09:00:00",
    )


# ================= print_signal =================

def test_print_signal_contains_key_fields(capsys):
    Reporter().print_signal(make_signal())
    out = capsys.readouterr().out
    assert "BUY" in out
    assert "半导体ETF" in out
    assert "512480" in out
    assert "73.75" in out


def test_factor_detail_dict_values_compact(capsys):
    """dict 子值输出紧凑 JSON"""
    signal = make_signal(detail={
        "factors": {"sentiment": {"score": 50.0, "vol_ratio": 1.08}},
    })
    Reporter().print_signal(signal)
    out = capsys.readouterr().out
    assert '{"score": 50.0, "vol_ratio": 1.08}' in out


def test_factor_detail_list_values_multiline(capsys):
    """列表值（如 ranking）必须多行输出，禁止超长单行（回归保护）"""
    ranking = [
        {"code": f"51{i:04d}", "name": f"ETF{i}", "category": "行业",
         "total": 70.0 - i, "factors": {"sentiment": 50.0, "oversold": 80.0}}
        for i in range(10)
    ]
    signal = make_signal(detail={"ranking": ranking})
    Reporter().print_signal(signal)
    out = capsys.readouterr().out

    # 修复后：list 以缩进 JSON 多行输出，任意行都不应超长
    long_lines = [line for line in out.splitlines() if len(line) > 200]
    assert not long_lines, f"存在超长输出行: {long_lines[:1]}"
    # 多行 JSON 中应包含换行后的字段（区别于旧的单行 str(list)）
    assert '"code": "510000"' in out


def test_print_signal_empty_signal_no_detail(capsys):
    signal = TradeSignal(mode="pre", signal="EMPTY", reason="无达标标的")
    Reporter().print_signal(signal)
    out = capsys.readouterr().out
    assert "EMPTY" in out
    assert "无达标标的" in out
    assert "因子明细" not in out  # 无 detail 不打印空表格


# ================= print_ranking =================

def test_print_ranking_table(capsys):
    Reporter().print_ranking([
        {"code": "512480", "name": "半导体ETF", "total": 73.75,
         "factors": {"sentiment": 50.0, "oversold": 100.0, "timing": 85.0, "position": 50}},
    ])
    out = capsys.readouterr().out
    assert "全池评分排名" in out
    assert "512480" in out
    assert "半导体ETF" in out


def test_print_ranking_empty_no_output(capsys):
    Reporter().print_ranking([])
    assert capsys.readouterr().out == ""


# ================= Markdown 落盘（按日期目录） =================

def test_save_signal_writes_markdown_in_date_dir(tmp_path):
    """信号保存为 Markdown，位于 reports/YYYY-MM-DD/ 日期目录下"""
    reporter = Reporter(report_dir=tmp_path)
    path = reporter.save_signal(make_signal())

    assert path.suffix == ".md"
    assert path.parent.name == datetime.now().strftime("%Y-%m-%d")  # 日期目录
    assert path.parent.parent == tmp_path
    content = path.read_text(encoding="utf-8")
    assert "# 场内ETF交易策略" in content
    assert "512480" in content
    assert "BUY" in content
    assert "合规声明" in content


def test_save_signal_preserves_factor_detail(tmp_path):
    """因子明细写入 Markdown 表格"""
    signal = make_signal(detail={"factors": {"oversold": {"score": 100.0}}})
    path = Reporter(report_dir=tmp_path).save_signal(signal)
    content = path.read_text(encoding="utf-8")
    assert "## 因子明细" in content
    assert "factors.oversold" in content
    assert "100.0" in content


def test_save_signal_ranking_in_json_block(tmp_path):
    """排名以折叠 JSON 代码块写入，避免表格内超长行"""
    signal = make_signal(detail={
        "ranking": [{"code": "512480", "name": "半导体ETF", "total": 73.75,
                    "factors": {"sentiment": 50.0}}],
    })
    path = Reporter(report_dir=tmp_path).save_signal(signal)
    content = path.read_text(encoding="utf-8")
    assert "```json" in content
    assert '"code": "512480"' in content


def test_save_report_markdown_in_date_dir(tmp_path):
    """日报保存为 reports/{date}/daily_report.md"""
    from src.output.models import DailyReport
    report = DailyReport(
        date="2026-09-13", signal=make_signal(),
        position={"code": ""}, realized_pnl=0.0,
        notes=["当前空仓"],
        ranking=[{"code": "512480", "name": "半导体ETF", "total": 73.75,
                  "factors": {"sentiment": 50.0, "oversold": 100.0,
                              "timing": 85.0, "position": 50,
                              "downtrend_guard": 35.0}}],
    )
    path = Reporter(report_dir=tmp_path).save_report(report)

    assert path == tmp_path / "2026-09-13" / "daily_report.md"
    content = path.read_text(encoding="utf-8")
    assert "盘后日报" in content
    assert "当前空仓" in content
    assert "趋势" in content  # 新因子列头
    assert "512480" in content
