"""数据校验模块单元测试"""
import pandas as pd
import pytest

from src.core.data_validator import DataValidator
from tests.conftest import make_daily


@pytest.fixture
def validator() -> DataValidator:
    return DataValidator()


def test_valid_daily_passes(validator, normal_daily):
    result = validator.validate_daily(normal_daily, "510300")
    assert result.ok
    assert not result.errors
    assert len(result.df) == len(normal_daily)


def test_empty_data_fails(validator):
    result = validator.validate_daily(pd.DataFrame(), "510300")
    assert not result.ok
    assert "空数据" in result.errors[0]


def test_missing_column_fails(validator, normal_daily):
    df = normal_daily.drop(columns=["volume"])
    result = validator.validate_daily(df, "510300")
    assert not result.ok
    assert any("缺少字段" in e for e in result.errors)


def test_negative_price_fails(validator, normal_daily):
    df = normal_daily.copy()
    df.loc[10, "close"] = -1.0
    result = validator.validate_daily(df, "510300")
    assert not result.ok
    assert any("负数" in e for e in result.errors)


def test_ohlc_relation_fails(validator, normal_daily):
    df = normal_daily.copy()
    df.loc[10, "low"] = df.loc[10, "high"] + 1.0  # low > high 非法
    result = validator.validate_daily(df, "510300")
    assert not result.ok
    assert any("OHLC" in e for e in result.errors)


def test_duplicate_dates_deduped(validator, normal_daily):
    df = pd.concat([normal_daily, normal_daily.iloc[[-1]]], ignore_index=True)
    result = validator.validate_daily(df, "510300")
    assert result.ok
    assert len(result.df) == len(normal_daily)  # 去重后行数恢复
    assert any("重复日期" in w for w in result.warnings)


def test_price_jump_fails(validator, normal_daily):
    df = normal_daily.copy()
    df.loc[50, "close"] = df.loc[49, "close"] * 1.5  # 单日 +50% 异常跳变
    # 同步抬高高点，保持 OHLC 关系合法，确保触发的是跳变检测
    df.loc[50, "high"] = max(df.loc[50, "high"], df.loc[50, "close"])
    result = validator.validate_daily(df, "510300")
    assert not result.ok
    assert any("异常跳变" in e for e in result.errors)


def test_insufficient_history_fails(validator):
    df = make_daily(n=10)
    result = validator.validate_daily(df, "510300")
    assert not result.ok
    assert any("不足" in e for e in result.errors)


def test_missing_values_filled(validator, normal_daily):
    df = normal_daily.copy()
    df.loc[40, "volume"] = None
    result = validator.validate_daily(df, "510300")
    assert result.ok
    assert not result.df["volume"].isna().any()
    assert any("填充" in w for w in result.warnings)


def test_realtime_validation(validator):
    assert validator.validate_realtime(None, "510300").ok is False
    assert validator.validate_realtime({"price": -1.0}, "510300").ok is False
    assert validator.validate_realtime({"price": 3.5}, "510300").ok is True
