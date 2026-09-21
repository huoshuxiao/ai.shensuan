# -*- coding: utf-8 -*-
"""ETF 池管理：从 akshare 动态获取 + 上市日期（东财现货→新浪现货→内置兜底）

选取方式为"指数成分"：ETF 产品名内嵌其跟踪指数名（如
`华夏国证半导体芯片ETF` → 指数键 `国证半导体芯片`），按该键分组，
每个指数取**上市最早且流动性达标**的一只代表 ETF（同日期按成交额大者），
再按流动性截断。取最早上市者是为了让每只代表尽量承载最长历史，缓解
"整池 2022 年后才上市 → 早年回测空仓"的组成偏差；成立不满 1 年
（min_list_days）的 ETF 不入池。兜底仅在全部行情源不可用时使用。"""

import os
import json
import re
import time
import pandas as pd
import akshare as ak
from config import (ETF_FILTER, UNIVERSE_CACHE, ETF_LIST_DATE_CACHE,
                    FALLBACK_UNIVERSE)


_MODIFIER_WORDS = ["中证", "国证", "上证", "深证", "沪深", "内地", "中国",
                   "指数", "基金", "Ａ", "A股", "ETF"]


def index_group(name: str) -> str:
    """从 ETF 名称提取"跟踪指数键"：
    1) 截断到最后一个 "ETF"（其后为基金公司品牌后缀，如 `房地产ETF银华`）；
    2) 去掉 中证/国证 等前缀词与括号备注；
    3) 空结果回退用原名，保证不丢标的。"""
    s = re.sub(r"[\(（].*?[\)）]", "", str(name)).strip()
    m = re.match(r"^(.*ETF)", s)
    if m:
        s = m.group(1)
    for w in _MODIFIER_WORDS:
        s = s.replace(w, "")
    s = s.strip()
    return s if len(s) >= 2 else str(name)


def _dates_worker(codes):
    """子进程内执行：逐只取上市日期（东财日线首行→新浪备源），
    返回 [(code, 'YYYY-MM-DD'|None)]。akshare 请求不带 socket 超时，
    单只挂起会把串行版卡死（实测 30+ 分钟无进展），故放到子进程里由
    父进程按轮次 deadline 整批 terminate。"""
    import time as _t
    import akshare as ak
    import pandas as _pd
    out = []
    for code in codes:
        d = None
        try:
            df = ak.fund_etf_hist_em(symbol=code, period="daily",
                                      start_date="20040101",
                                      end_date="20991231", adjust="")
            if df is not None and not df.empty:
                d = str(_pd.to_datetime(df["日期"].iloc[0]).date())
        except Exception:
            pass
        if d is None:
            sym = ("sh" if code.startswith(("5", "6", "9")) else "sz") + code
            try:
                df = ak.fund_etf_hist_sina(symbol=sym)
                if df is not None and not df.empty:
                    d = str(_pd.to_datetime(df["date"].iloc[0]).date())
            except Exception:
                pass
        out.append((code, d))
        _t.sleep(0.15)
    return out




class ETFUniverse:
    def __init__(self):
        self.universe = None

    def _load_date_cache(self) -> dict:
        """全市场上市日期持久缓存 {code: "YYYY-MM-DD"}；
        双源均失败的代码记在 "_failed" 名单（含失败日期），30 天内不再重试。"""
        if os.path.exists(ETF_LIST_DATE_CACHE):
            try:
                with open(ETF_LIST_DATE_CACHE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"  ⚠️ 上市日期缓存损坏，忽略重建: {type(e).__name__}: {e}")
        return {}

    def _save_date_cache(self, cache: dict):
        os.makedirs(os.path.dirname(ETF_LIST_DATE_CACHE), exist_ok=True)
        tmp = ETF_LIST_DATE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, ETF_LIST_DATE_CACHE)

    @staticmethod
    def _failed_recent(failed: dict, code: str, days: int = 30) -> bool:
        ts = failed.get(code)
        if not ts:
            return False
        try:
            return (pd.Timestamp.now()
                    - pd.Timestamp(ts)).days < days
        except Exception:
            return False

    def fetch_list_dates(self, missing, cache, workers=8,
                         round_timeout=420, max_rounds=12):
        """并发+轮次超时地补齐上市日期缓存，返回仍取不到日期的代码。

        每轮把 pending 均匀切成 workers 份交给子进程；一轮整体最多
        round_timeout 秒，挂起的轮直接 terminate，已成功的结果作废但
        下一轮重切重试——单个代码挂起不再拖死全池构建。
        双源均失败者写入 "_failed" 名单，30 天内跳过。"""
        import multiprocessing as mp
        failed = cache.get("_failed", {})
        pending = [c for c in missing
                   if not self._failed_recent(failed, c)]
        n_skipped_failed = len(missing) - len(pending)
        if n_skipped_failed:
            print(f"    ℹ️ {n_skipped_failed} 只近期双源失败过，"
                  f"30 天冷却期内不再重试")
        rnd, empty_rounds = 0, 0
        while pending and rnd < max_rounds and empty_rounds < 2:
            rnd += 1
            chunks = [pending[i::workers] for i in range(workers)]
            chunks = [c for c in chunks if c]
            t0 = time.time()
            pool = mp.Pool(len(chunks))
            try:
                for got in pool.map_async(_dates_worker,
                                           chunks).get(
                                               timeout=round_timeout):
                    for code, d in got:
                        if d:
                            cache[code] = d
                            failed.pop(code, None)
                        else:
                            failed[code] = (
                                pd.Timestamp.now().strftime("%Y-%m-%d"))
                pool.close()
            except mp.TimeoutError:
                pool.terminate()
                print(f"    ⏳ 第 {rnd} 轮超过 {round_timeout}s，"
                      f"终止本轮未竟部分后重试")
            except Exception as e:
                pool.terminate()
                print(f"    ⚠️ 第 {rnd} 轮异常: "
                      f"{type(e).__name__}: {e}")
            finally:
                pool.join()
            cache["_failed"] = failed
            self._save_date_cache(cache)
            before = len(pending)
            pending = [c for c in pending
                       if c not in cache and c not in failed]
            print(f"    上市日期 第 {rnd} 轮: 补齐 {before - len(pending)}"
                  f"/{before}，剩余 {len(pending)}，"
                  f"本轮 {time.time() - t0:.0f}s", flush=True)
            empty_rounds = empty_rounds + 1 \
                if before == len(pending) else 0
        return pending

    def fetch_all(self) -> pd.DataFrame:
        print("  正在从 akshare 获取 ETF 列表...")
        try:
            df = ak.fund_etf_spot_em()
            df = df.rename(columns={
                "代码": "code", "名称": "name",
                "成交额": "amount"})[["code", "name", "amount"]]
            df["code"] = df["code"].astype(str).str.zfill(6)
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            print(f"  获取到 {len(df)} 只 ETF [东方财富现货]")
            return df
        except Exception as e:
            print(f"  ⚠️ 东财 ETF 列表失败: {type(e).__name__}: {e}")
        try:
            df = ak.fund_etf_category_sina(symbol="ETF基金")
            df = df.rename(columns={"代码": "code", "名称": "name",
                                    "成交额": "amount"})
            df["code"] = df["code"].astype(str).str[-6:].str.zfill(6)
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            df = df[["code", "name", "amount"]].dropna(subset=["amount"])
            print(f"  获取到 {len(df)} 只 ETF [新浪现货，降级源]")
            return df
        except Exception as e:
            print(f"  ⚠️ 新浪 ETF 列表也失败: {type(e).__name__}: {e}，"
                  f"使用内置兜底池")
            return pd.DataFrame(FALLBACK_UNIVERSE,
                                columns=["code", "name"]).assign(
                                    amount=float("inf"))

    def build(self, use_cache: bool = True) -> pd.DataFrame:
        if use_cache and os.path.exists(UNIVERSE_CACHE):
            df = pd.read_csv(UNIVERSE_CACHE, dtype={"code": str})
            has_marker = (len(df) > 0 and "selection" in df.columns
                          and (df["selection"] == "earliest_listed").all()
                          and "index_group" in df.columns)
            if has_marker:
                print(f"  从缓存加载: {UNIVERSE_CACHE}")
                df["code"] = df["code"].str.zfill(6)
                df["list_date"] = pd.to_datetime(df["list_date"])
                self.universe = df
                return df
            print("  ⚠️ 池缓存为旧版选取方式，忽略并按"
                  "指数成分+最早上市重建")

        df = self.fetch_all()
        for kw in ETF_FILTER["exclude_keywords"]:
            df = df[~df["name"].str.contains(kw, na=False)]
        # 代码前缀排除（如 511 债券/货币段）
        for pfx in ETF_FILTER.get("exclude_prefixes", []):
            df = df[~df["code"].str.startswith(pfx)]
        df = df[df["amount"] >= ETF_FILTER["min_avg_amount"]]

        # 指数成分化：先给全部候选分组，再逐只补上市日期
        df["index_group"] = df["name"].map(index_group)
        n_idx = df["index_group"].nunique()

        cache = self._load_date_cache()
        missing = [c for c in df["code"] if c not in cache]
        if missing:
            print(f"  查询 {len(missing)}/{len(df)} 只候选 ETF 上市日期"
                  f"（8 进程并发，持久缓存增量补拉）...")
            still = self.fetch_list_dates(missing, cache)
            if still:
                print(f"  ℹ️ {len(still)} 只双源均取不到上市日期"
                      f"（挂起超时/接口失败），按日期未知剔除")
        else:
            print(f"  上市日期全部命中持久缓存（{len(df)} 只候选）")

        df["list_date"] = pd.to_datetime(df["code"].map(cache),
                                         errors="coerce")
        n_nodate = int(df["list_date"].isna().sum())
        df = df.dropna(subset=["list_date"])
        # 成立满 1 年门槛：上市不满 min_list_days 自然日的 ETF 不入池
        cutoff = (pd.Timestamp.now().normalize()
                  - pd.Timedelta(days=ETF_FILTER["min_list_days"]))
        n_young = int((df["list_date"] > cutoff).sum())
        df = df[df["list_date"] <= cutoff]
        # 每指数取上市最早者（同日按成交额大者），再按流动性截断池规模
        df = (df.sort_values(["list_date", "amount"], ascending=[True, False])
                .drop_duplicates("index_group", keep="first"))
        df = (df.sort_values("amount", ascending=False)
                .head(ETF_FILTER["max_count"]).reset_index(drop=True))
        print(f"  指数分组: {n_idx} 个指数 → 代表 ETF {len(df)} 只"
              f"（每指数取最早上市且流动性达标者；剔除日期未知 "
              f"{n_nodate} 只、上市不满 {ETF_FILTER['min_list_days']} 天 "
              f"{n_young} 只；截断 max_count={ETF_FILTER['max_count']}）")

        df["selection"] = "earliest_listed"
        df["avg_amount"] = df["amount"]
        df.to_csv(UNIVERSE_CACHE, index=False, encoding="utf-8-sig")
        self.universe = df
        print(f"  ETF 池构建完成: {len(df)} 只")
        return df

    def get_tradable_at(self, date: pd.Timestamp) -> list:
        if self.universe is None:
            raise RuntimeError("请先调用 build()")
        min_days = ETF_FILTER["min_list_days"]
        cutoff = date - pd.Timedelta(days=min_days)
        return self.universe[
            self.universe["list_date"] <= cutoff]["code"].tolist()


_universe_instance = None

def get_universe() -> ETFUniverse:
    global _universe_instance
    if _universe_instance is None:
        _universe_instance = ETFUniverse()
        _universe_instance.build()
    return _universe_instance