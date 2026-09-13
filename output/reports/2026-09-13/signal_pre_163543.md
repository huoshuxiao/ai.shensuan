# 场内ETF交易策略 - 盘前（生成当日交易计划）

- 运行时间: 2026-09-13 16:35:43
- 数据来源: akshare/腾讯行情（仅供个人研究学习使用）

## 交易信号

| 项目 | 内容 |
| --- | --- |
| 信号 | 买入 (BUY) |
| 标的 | 易方达沪深300医药ETF(512010) |
| 参考价格 | - |
| 综合评分 | 71.0 |
| 理由 | 512010 综合评分 71.0 >= 阈值 60.0，优于第二名 512690(65.75) |

## 因子明细

| 指标 | 数值 |
| --- | --- |
| position | `None` |
| factors.sentiment | `{"score": 50.0, "latest_change": -0.0108, "vol_ratio": 1.2, "index_change": -0.0118}` |
| factors.oversold | `{"score": 94.0, "rsi6": 11.11, "deviation_ma20": -0.0461, "down_streak": 3}` |
| factors.timing | `{"score": 85.0, "boll_pos": 0.066, "macd_golden_cross": false, "macd_death_cross": false, "kdj_j": 7.63}` |
| factors.position | `{"score": 50, "note": "无持仓，中性"}` |
| factors.downtrend_guard | `{"score": 65.0, "ma20": 0.3827, "ma60": 0.3735, "bearish_align": false, "ma60_falling": false, "new_low_60d": false}` |

## 全池评分排名

<details>
<summary>排名明细 (JSON)</summary>

```json
[
  {
    "code": "512010",
    "name": "易方达沪深300医药ETF",
    "category": "行业",
    "total": 71.0,
    "factors": {
      "sentiment": 50.0,
      "oversold": 94.0,
      "timing": 85.0,
      "position": 50,
      "downtrend_guard": 65.0
    }
  },
  {
    "code": "512690",
    "name": "酒ETF",
    "category": "行业",
    "total": 65.75,
    "factors": {
      "sentiment": 50.0,
      "oversold": 85.0,
      "timing": 85.0,
      "position": 50,
      "downtrend_guard": 50.0
    }
  },
  {
    "code": "510050",
    "name": "华夏上证50ETF",
    "category": "宽基",
    "total": 64.75,
    "factors": {
      "sentiment": 50.0,
      "oversold": 81.0,
      "timing": 85.0,
      "position": 50,
      "downtrend_guard": 50.0
    }
  },
  {
    "code": "512480",
    "name": "国联安半导体ETF",
    "category": "行业",
    "total": 62.5,
    "factors": {
      "sentiment": 50.0,
      "oversold": 100.0,
      "timing": 85.0,
      "position": 50,
      "downtrend_guard": 15.0
    }
  },
  {
    "code": "159928",
    "name": "汇添富中证主要消费ETF",
    "category": "行业",
    "total": 60.65,
    "factors": {
      "sentiment": 65.0,
      "oversold": 59.0,
      "timing": 62.0,
      "position": 50,
      "downtrend_guard": 65.0
    }
  },
  {
    "code": "588000",
    "name": "华夏上证科创板50成份ETF",
    "category": "宽基",
    "total": 59.9,
    "factors": {
      "sentiment": 50.0,
      "oversold": 100.0,
      "timing": 72.0,
      "position": 50,
      "downtrend_guard": 15.0
    }
  },
  {
    "code": "515030",
    "name": "华夏中证新能源汽车ETF",
    "category": "行业",
    "total": 59.5,
    "factors": {
      "sentiment": 50.0,
      "oversold": 100.0,
      "timing": 85.0,
      "position": 50,
      "downtrend_guard": 0.0
    }
  },
  {
    "code": "512880",
    "name": "国泰中证全指证券公司ETF",
    "category": "行业",
    "total": 57.5,
    "factors": {
      "sentiment": 50.0,
      "oversold": 88.0,
      "timing": 75.0,
      "position": 50,
      "downtrend_guard": 15.0
    }
  },
  {
    "code": "159819",
    "name": "易方达中证人工智能主题ETF",
    "category": "行业",
    "total": 57.4,
    "factors": {
      "sentiment": 50.0,
      "oversold": 82.0,
      "timing": 82.0,
      "position": 50,
      "downtrend_guard": 15.0
    }
  },
  {
    "code": "510500",
    "name": "南方中证500ETF",
    "category": "宽基",
    "total": 56.75,
    "factors": {
      "sentiment": 50.0,
      "oversold": 89.0,
      "timing": 70.0,
      "position": 50,
      "downtrend_guard": 15.0
    }
  }
]
```

</details>

## 合规声明

> 【合规声明】本系统输出仅供个人研究学习使用，不构成任何投资建议。数据来源：akshare/腾讯行情（仅供个人研究学习使用）。投资有风险，入市需谨慎。
