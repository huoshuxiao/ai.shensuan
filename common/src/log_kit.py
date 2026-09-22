# -*- coding: utf-8 -*-
"""统一日志：控制台 + etf/v1/log/ 文件双写（行级时间戳）

入口脚本在 import _bootstrap 之后调用:
    from log_kit import setup_logging
    setup_logging("daily_backtest")
之后所有 print / 异常栈 / warning 自动镜像到日志文件。
"""

import os
import sys
import datetime


class _Tee:
    def __init__(self, console, logfile):
        self.console = console
        self.logfile = logfile
        self._buf = ""
        self.closed = False

    def _stamp(self):
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def write(self, text):
        try:
            self.console.write(text)
        except Exception:
            pass
        try:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self.logfile.write(f"{self._stamp()} | {line}\n")
            self.logfile.flush()
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            self.console.flush()
        except Exception:
            pass
        try:
            self.logfile.flush()
        except Exception:
            pass

    def isatty(self):
        return self.console.isatty()

    @property
    def encoding(self):
        return getattr(self.console, "encoding", "utf-8")


def setup_logging(tag, keep_days=30):
    """初始化日志：镜像 stdout/stderr 到 {LOG_DIR}/{tag}_{YYYYMMDD}.log

    返回日志文件路径；LOG_DIR 不可写时静默降级为仅控制台。
    重复调用只生效一次（多入口嵌套时防止双重 Tee）。
    """
    if isinstance(sys.stdout, _Tee):
        return ""
    try:
        from config import LOG_DIR
    except Exception:
        LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "log")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d")
        path = os.path.join(LOG_DIR, f"{tag}_{ts}.log")
        fp = open(path, "a", encoding="utf-8", buffering=1)
        fp.write(f"\n===== {tag} 启动 @ "
                 f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                 f"=====\n")
        sys.stdout = _Tee(sys.__stdout__, fp)
        sys.stderr = _Tee(sys.__stderr__, fp)
        _cleanup(LOG_DIR, tag, keep_days)
        print(f"  📝 日志: {path}")
        return path
    except Exception as e:
        print(f"  ⚠️ 日志初始化失败（仅控制台）: {e}")
        return ""


def _cleanup(log_dir, tag, keep_days):
    """按天滚动，仅保留最近 keep_days 天"""
    try:
        import glob
        cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
        for f in glob.glob(os.path.join(log_dir, "*.log")):
            name = os.path.basename(f)
            date_part = name.rsplit("_", 1)[-1].split(".")[0]
            try:
                d = datetime.datetime.strptime(date_part, "%Y%m%d")
            except ValueError:
                continue
            if d < cutoff:
                try:
                    os.remove(f)
                except OSError:
                    pass
    except Exception:
        pass
