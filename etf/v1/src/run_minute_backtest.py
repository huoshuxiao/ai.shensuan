# -*- coding: utf-8 -*-
"""分钟线回测专用入口"""

import os


def main():
    os.environ["ETF_FREQ"] = "1min"
    import importlib
    import config
    config.FREQ = "1min"
    importlib.reload(config)

    import data_loader
    importlib.reload(data_loader)

    from main import main as run_main
    run_main()


if __name__ == "__main__":
    main()