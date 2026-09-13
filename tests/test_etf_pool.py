"""标的池测试：CSV 数据源加载与候选池组装"""
import pytest

from src.config import etf_pool as pool_mod
from src.config.etf_pool import Etf, build_pool, get_etf, load_base_table
from src.config.settings import settings


def make_csv(tmp_path, rows: list[str]) -> str:
    path = tmp_path / "etf_base.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def test_load_base_table_parses_code_and_name(tmp_path):
    """CSV 第一列代码、第二列名称"""
    csv = make_csv(tmp_path, ["158000,鹏华中证港股通内地金融ETF",
                              "510300,华泰柏瑞沪深300ETF",
                              "512010,易方达沪深300医药ETF"])
    table = load_base_table(csv)
    assert table == {
        "158000": "鹏华中证港股通内地金融ETF",
        "510300": "华泰柏瑞沪深300ETF",
        "512010": "易方达沪深300医药ETF",
    }


def test_load_base_table_skips_blank_and_malformed_lines(tmp_path):
    """空行/格式错误行跳过不影响解析"""
    csv = make_csv(tmp_path, ["", "   ", "仅一列", "510300,华泰柏瑞沪深300ETF"])
    assert load_base_table(csv) == {"510300": "华泰柏瑞沪深300ETF"}


def test_load_base_table_missing_file_raises():
    """CSV 不存在 -> FileNotFoundError"""
    with pytest.raises(FileNotFoundError, match="不存在"):
        load_base_table("/nonexistent/path/etf_base.csv")


def test_load_base_table_empty_file_raises(tmp_path):
    """CSV 为空 -> ValueError"""
    csv = make_csv(tmp_path, [])
    with pytest.raises(ValueError, match="为空或格式错误"):
        load_base_table(csv)


def test_build_pool_from_config_and_csv(monkeypatch, tmp_path):
    """候选池按配置组装：名称取自 CSV，分类按宽基集合判定"""
    csv = make_csv(tmp_path, ["510300,华泰柏瑞沪深300ETF",
                              "512480,国联安半导体ETF",
                              "999999,不存在ETF"])
    monkeypatch.setattr(settings, "etf_base_csv", csv)
    monkeypatch.setattr(settings, "etf_candidates", ["510300", "512480", "999999", "123456"])
    monkeypatch.setattr(settings, "wide_base_codes", ["510300"])
    pool = build_pool()
    # 123456 不在 CSV 中被跳过；其余按配置组装
    assert pool == [
        Etf("510300", "华泰柏瑞沪深300ETF", "宽基"),
        Etf("512480", "国联安半导体ETF", "行业"),
        Etf("999999", "不存在ETF", "行业"),
    ]
    assert len(pool) == 3


def test_build_pool_skips_code_missing_in_csv(monkeypatch, tmp_path):
    """候选代码不在 CSV 中时跳过并告警"""
    csv = make_csv(tmp_path, ["510300,华泰柏瑞沪深300ETF"])
    monkeypatch.setattr(settings, "etf_base_csv", csv)
    monkeypatch.setattr(settings, "etf_candidates", ["510300", "999999"])
    pool = build_pool()
    assert [e.code for e in pool] == ["510300"]


def test_get_etf_out_of_pool_raises():
    """池外代码 -> KeyError 友好提示"""
    with pytest.raises(KeyError, match="不在标的池中"):
        get_etf("999999")


def test_default_pool_matches_real_csv():
    """默认候选池全部能从真实 etf_base.csv 解析（名称为 CSV 全名）"""
    pool = pool_mod.build_pool()
    assert len(pool) == len(settings.etf_candidates)  # 17 只全部命中
    names = {e.name for e in pool}
    assert "华泰柏瑞沪深300ETF" in names
    assert "国联安半导体ETF" in names
