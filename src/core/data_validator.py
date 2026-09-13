"""数据校验模块：字段完整性、异常值检测、缺失值处理、复权一致性

校验不通过时返回空 DataFrame，上层将该标的跳过评分，
确保"绝不输出脏信号"。
"""
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.config.settings import settings

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


@dataclass
class ValidationResult:
    ok: bool
    df: pd.DataFrame
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class DataValidator:
    """对统一列名的日线 DataFrame 做多级校验"""

    def __init__(self):
        self.max_jump = settings.max_daily_price_jump
        self.min_bars = settings.min_history_bars
        self.max_missing_pct = settings.max_missing_pct

    def validate_daily(self, df: pd.DataFrame, code: str = "") -> ValidationResult:
        """完整校验流程，返回干净的 DataFrame"""
        errors: list[str] = []
        warnings: list[str] = []

        if df is None or df.empty:
            return ValidationResult(False, pd.DataFrame(), [f"{code}: 空数据"])

        # 1. 字段完整性
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            errors.append(f"{code}: 缺少字段 {missing_cols}")
            return ValidationResult(False, pd.DataFrame(), errors)

        df = df.copy()

        # 2. 日期升序且无重复
        df["date"] = pd.to_datetime(df["date"])
        if df["date"].duplicated().any():
            df = df.drop_duplicates(subset="date", keep="last")
            warnings.append(f"{code}: 存在重复日期，已去重")
        df = df.sort_values("date").reset_index(drop=True)

        # 3. 数值化 + 非负检查（价格/成交量必须 >= 0）
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        neg = (df[["open", "high", "low", "close", "volume"]] < 0).any().any()
        if neg:
            errors.append(f"{code}: 存在负数价格/成交量")
            return ValidationResult(False, pd.DataFrame(), errors)

        # 4. OHLC 关系检查
        bad_ohlc = (
            (df["high"] < df["low"]) |
            (df["open"] > df["high"]) |
            (df["open"] < df["low"]) |
            (df["close"] > df["high"]) |
            (df["close"] < df["low"])
        )
        if bad_ohlc.any():
            bad_dates = df.loc[bad_ohlc, "date"].dt.strftime("%Y-%m-%d").tolist()
            errors.append(f"{code}: OHLC关系异常: {bad_dates[:3]}")
            return ValidationResult(False, pd.DataFrame(), errors)

        # 5. 缺失值比例（除停牌外不应有大量 NaN）
        missing_pct = df[REQUIRED_COLUMNS].isna().mean().max()
        if missing_pct > self.max_missing_pct:
            errors.append(f"{code}: 缺失值比例 {missing_pct:.1%} 超限")
            return ValidationResult(False, pd.DataFrame(), errors)
        if missing_pct > 0:
            df = df.ffill().bfill()  # 少量缺失前向填充
            warnings.append(f"{code}: 缺失值 {missing_pct:.1%} 已前向填充")

        # 6. 异常跳变检测（价格单日变化超阈值；首次上市/复权错误会触发）
        pct = df["close"].pct_change().abs()
        jump_dates = df.loc[pct > self.max_jump, "date"]
        if len(jump_dates) > 0:
            # 允许上市首日（首行无pct），其余跳变视为复权/数据错误
            real_jumps = jump_dates[jump_dates != df["date"].iloc[0]]
            if len(real_jumps) > 0:
                dates = real_jumps.dt.strftime("%Y-%m-%d").tolist()
                errors.append(f"{code}: 价格异常跳变(>{self.max_jump:.0%}): {dates[:3]}")
                return ValidationResult(False, pd.DataFrame(), errors)

        # 7. 数据条数不足降级
        if len(df) < self.min_bars:
            errors.append(f"{code}: 历史数据仅 {len(df)} 条，不足 {self.min_bars} 条")
            return ValidationResult(False, pd.DataFrame(), errors)

        # 8. 零成交/停牌识别（近期连续零成交提示）
        recent_zero = (df["volume"].tail(5) == 0).sum()
        if recent_zero >= 3:
            warnings.append(f"{code}: 近5日有{recent_zero}日零成交，疑似停牌/流动性差")

        if errors:
            return ValidationResult(False, pd.DataFrame(), errors, warnings)
        logger.debug("%s 数据校验通过: %d 条K线", code, len(df))
        return ValidationResult(True, df, errors, warnings)

    def validate_realtime(self, rt: dict | None, code: str = "") -> ValidationResult:
        """校验实时行情 dict"""
        if not rt:
            return ValidationResult(False, pd.DataFrame(), [f"{code}: 实时行情为空"])
        price = rt.get("price")
        if price is None or not np.isfinite(price) or price <= 0:
            return ValidationResult(False, pd.DataFrame(), [f"{code}: 实时价格非法: {price}"])
        return ValidationResult(True, pd.DataFrame([rt]))
