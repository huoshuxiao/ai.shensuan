# -*- coding: utf-8 -*-
"""因子库（MD + JSON + Git）"""

import os
import json
import shutil
import numpy as np
import pandas as pd
from datetime import datetime
from collections import OrderedDict
from config import FACTOR_LIBRARY, LIBRARY_DIR
from factor_naming import cn_name, METRIC_GLOSSARY


class FactorLibrary:
    def __init__(self, params=None):
        self.p = {**FACTOR_LIBRARY, **(params or {})}
        self.md_path = self.p["md_path"]
        self.index_path = self.p["index_path"]
        self.factors = OrderedDict()
        self._load_index()

    def _load_index(self):
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.factors = OrderedDict(sorted(
                    data.items(),
                    key=lambda x: x[1].get("first_seen", "")))
            except Exception:
                self.factors = OrderedDict()

    def _save_index(self):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(dict(self.factors), f,
                      ensure_ascii=False, indent=2)

    def upsert(self, name, expr, ic, icir, source,
               status="active", extra=None):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if name in self.factors:
            f = self.factors[name]
            f["ic_history"].append({"time": now, "ic": float(ic),
                                     "icir": float(icir)})
            f["ic"] = float(ic)
            f["icir"] = float(icir)
            f["last_seen"] = now
            f["status"] = status
            f["update_count"] = f.get("update_count", 0) + 1
            if extra:
                f.update(extra)
        else:
            self.factors[name] = {
                "name": name, "expr": expr, "ic": float(ic),
                "icir": float(icir), "source": source, "status": status,
                "first_seen": now, "last_seen": now, "update_count": 1,
                "ic_history": [{"time": now, "ic": float(ic),
                                 "icir": float(icir)}],
                **(extra or {})}

    def batch_upsert(self, factors, source, status="active"):
        for f in factors:
            self.upsert(f.get("name", "unknown"),
                        f.get("expr", ""),
                        f.get("mean_ic", f.get("ic", 0.0)),
                        f.get("icir", 0.0), source, status)
        print(f"  📚 因子库更新: +{len(factors)} (来源={source})")

    def mark_status(self, name, status, reason=""):
        if name in self.factors:
            self.factors[name]["status"] = status
            self.factors[name]["status_reason"] = reason
            self.factors[name]["status_time"] = \
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def get_active(self):
        return [n for n, f in self.factors.items()
                if f.get("status") == "active"]

    def get_top(self, n=20, by="ic"):
        items = list(self.factors.items())
        items.sort(key=lambda x: abs(x[1].get(by, 0)), reverse=True)
        return [name for name, _ in items[:n]]

    def get_by_source(self, source):
        return [n for n, f in self.factors.items()
                if f.get("source") == source]

    def to_dataframe(self):
        rows = []
        for name, f in self.factors.items():
            rows.append({"name": name,
                         "cn_name": cn_name(name, f.get("expr", "")),
                         "expr": f.get("expr", ""),
                         "ic": round(f.get("ic", 0), 4),
                         "icir": round(f.get("icir", 0), 4),
                         "source": f.get("source", ""),
                         "status": f.get("status", ""),
                         "first_seen": f.get("first_seen", ""),
                         "last_seen": f.get("last_seen", ""),
                         "updates": f.get("update_count", 0)})
        return pd.DataFrame(rows)

    def to_markdown(self, top_n=None):
        top_n = top_n or self.p["top_n_in_summary"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = ["# 因子库", "",
                 f"> 自动生成于 `{now}` | 共 **{len(self.factors)}** 个因子",
                 ""]
        lines.append("## 📊 元信息")
        lines.append("")
        active = sum(1 for f in self.factors.values()
                     if f.get("status") == "active")
        lines.append(f"- 总因子数: {len(self.factors)}")
        lines.append(f"- 活跃: {active}")
        sources = {}
        for f in self.factors.values():
            s = f.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1
        lines.append(f"- 来源分布: {sources}")
        lines.append("")
        lines.append(f"## 🏆 Top {top_n} 因子")
        lines.append("")
        lines.append("| 排名 | 因子名 | 中文名 | IC | ICIR | 来源 | 状态 |")
        lines.append("|------|--------|--------|-----|------|------|------|")
        sorted_f = sorted(self.factors.items(),
                          key=lambda x: abs(x[1].get("ic", 0)),
                          reverse=True)[:top_n]
        for i, (name, f) in enumerate(sorted_f, 1):
            lines.append(f"| {i} | `{name}` | {cn_name(name, f.get('expr', ''))} | "
                         f"{f.get('ic', 0):+.4f} | "
                         f"{f.get('icir', 0):+.3f} | "
                         f"{f.get('source', '')} | "
                         f"{f.get('status', '')} |")
        lines.append("")
        lines.append("## 📈 指标说明")
        lines.append("")
        lines.append("| 指标 | 中文名 | 含义与参考口径 |")
        lines.append("|------|--------|----------------|")
        for key, label, desc in METRIC_GLOSSARY:
            safe_desc = desc.replace("|", "\\|")
            lines.append(f"| `{key}` | {label} | {safe_desc} |")
        lines.append("")
        lines.append("## 📖 因子详情")
        lines.append("")
        for name, f in self.factors.items():
            cn = cn_name(name, f.get("expr", ""))
            lines.append(f"### `{name}` · {cn}")
            lines.append("")
            lines.append(f"- **中文名**: {cn}")
            lines.append(f"- **状态**: `{f.get('status', '')}`")
            lines.append(f"- **来源**: `{f.get('source', '')}`")
            lines.append(f"- **IC**: {f.get('ic', 0):+.4f}")
            lines.append(f"- **ICIR**: {f.get('icir', 0):+.3f}")
            lines.append(f"- **首次发现**: {f.get('first_seen', '')}")
            if self.p["include_code"] and f.get("expr"):
                lines.append("")
                lines.append("**表达式**:")
                lines.append("")
                lines.append("```python")
                lines.append(f["expr"])
                lines.append("```")
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    # factor_library.py save_markdown 内
    def save_markdown(self):
        if not self.p["enabled"]:
            return
        md = self.to_markdown()
        with open(self.md_path, "w", encoding="utf-8") as f:
            f.write(md)
        self._save_index()
        df = self.to_dataframe()
        if not df.empty:
            df.to_csv(f"{LIBRARY_DIR}/factor_library.csv",
                      index=False, encoding="utf-8-sig")
        print(f"  💾 因子库已保存: {self.md_path} "
            f"({len(self.factors)} 因子)")
        try:
            from factor_library_git import get_git_manager
            gm = get_git_manager()
            active = sum(1 for f in self.factors.values()
                        if f.get("status") == "active")
            gm.commit(f"因子库更新: {len(self.factors)} 总 / {active} 活跃")
        except Exception as e:
            print(f"  ⚠️ git commit 失败: {e}")

    def to_llm_context(self, max_factors=15):
        top = sorted(self.factors.items(),
                     key=lambda x: abs(x[1].get("ic", 0)),
                     reverse=True)[:max_factors]
        lines = ["已知因子（避免重复）："]
        for name, f in top:
            lines.append(f"  - {name}（{cn_name(name, f.get('expr', ''))}）"
                         f": IC={f.get('ic', 0):+.4f}")
        return "\n".join(lines)


_lib_instance = None

def get_library():
    global _lib_instance
    if _lib_instance is None:
        _lib_instance = FactorLibrary()
    return _lib_instance