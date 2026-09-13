"""ETF 标的池：数据源为 data/base/etf_base.csv（第一列代码，第二列名称）

候选池由 config/settings.py 的 etf_candidates 配置控制，
名称自动从 CSV 基础表解析，分类按 wide_base_codes 判定。
"""
import logging
from dataclasses import dataclass
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Etf:
    code: str       # 6位基金代码
    name: str       # 名称
    category: str   # 分类：宽基 / 行业


def load_base_table(csv_path: str | Path | None = None) -> dict[str, str]:
    """加载 ETF 基础表：代码 -> 名称（第一列代码，第二列名称）"""
    path = Path(csv_path or settings.etf_base_csv)
    if not path.exists():
        raise FileNotFoundError(f"ETF基础数据文件不存在: {path}")
    table: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2 and parts[0]:
                table[parts[0]] = parts[1]
    if not table:
        raise ValueError(f"ETF基础数据文件为空或格式错误: {path}")
    return table


def _category_of(code: str) -> str:
    """按配置的宽基代码集合判定分类，其余归为行业"""
    return "宽基" if code in settings.wide_base_codes else "行业"


def build_pool() -> list[Etf]:
    """按配置候选池从 CSV 基础表组装 Etf 列表（CSV 中不存在的代码自动跳过）"""
    table = load_base_table()
    pool: list[Etf] = []
    for code in settings.etf_candidates:
        name = table.get(code)
        if name is None:
            logger.warning("候选代码 %s 不在 ETF 基础表中，已跳过", code)
            continue
        pool.append(Etf(code=code, name=name, category=_category_of(code)))
    return pool


# 全部候选池（模块加载时按配置构建）
ETF_POOL = build_pool()

# 代码 -> ETF 映射
ETF_MAP = {etf.code: etf for etf in ETF_POOL}


def get_etf(code: str) -> Etf:
    """按代码获取 ETF 信息"""
    if code not in ETF_MAP:
        raise KeyError(f"ETF代码 {code} 不在标的池中")
    return ETF_MAP[code]
