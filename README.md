# ai.shensuan

## 使用方式

```bash
python3.10 main.py --mode pre        # 盘前生成交易计划
python3.10 main.py --mode intra      # 盘中实时监控（止盈止损/评分复核）
python3.10 main.py --mode post       # 盘后复盘（更新持有天数+日报）
python3.10 main.py --mode pre --action buy --code 510300 --price 3.50   # 记录买入
python3.10 main.py --mode post --action sell --price 3.60               # 记录卖出
python3.10 -m pytest tests/ -v       # 运行测试
```
