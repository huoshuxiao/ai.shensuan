# -*- coding: utf-8 -*-
"""日线回测专用入口"""

import os
import json
import pandas as pd


def main():
    # 强制切到日线
    os.environ["ETF_FREQ"] = "daily"

    # 重新导入（让 config 读到 daily）
    import importlib
    import config
    config.FREQ = "daily"
    importlib.reload(config)

    # 覆盖到全局
    from config import FREQ_MAP
    config.FREQ_MAP = FREQ_MAP

    # 重新加载各模块
    import data_loader
    importlib.reload(data_loader)

    from main import main as run_main
    run_main()


if __name__ == "__main__":
    main()