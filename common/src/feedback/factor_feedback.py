# -*- coding: utf-8 -*-
"""因子级反馈"""

import os
import json
import pandas as pd
from config import (
    LIVE_DATA_DIR, RESULTS_DIR, FACTOR_LIBRARY,
)


class FactorFeedback:
    def __init__(self, live_data_dir=LIVE_DATA_DIR,
                 index_path=FACTOR_LIBRARY["index_path"]):
        self.dir = live_data_dir
        self.index_path = index_path
        self.results = {}

    def analyze(self):
        if not os.path.exists(self.index_path):
            return {}
        with open(self.index_path, "r", encoding="utf-8") as f:
            library = json.load(f)
        decay_path = f"{RESULTS_DIR}/factor_decay_predict.csv"
        decay = {}
        if os.path.exists(decay_path):
            df = pd.read_csv(decay_path)
            decay = dict(zip(df["factor"], df["predicted_ic"]))
        rows = []
        for name, f in library.items():
            bt_ic = f.get("ic", 0)
            live_ic = decay.get(name, bt_ic)
            ratio = live_ic / (bt_ic + 1e-9) if bt_ic != 0 else 0
            rows.append({"factor": name, "bt_ic": bt_ic,
                         "live_ic": live_ic, "ic_ratio": ratio,
                         "status": "keep" if ratio > 0.7 else
                                   "watch" if ratio > 0.4 else "retire"})
        df = pd.DataFrame(rows)
        df.to_csv(f"{self.dir}/factor_feedback.csv", index=False,
                  encoding="utf-8-sig")
        stats = {"n_factors": len(df),
                 "n_keep": int((df["status"] == "keep").sum()),
                 "n_watch": int((df["status"] == "watch").sum()),
                 "n_retire": int((df["status"] == "retire").sum()),
                 "retire_factors": df[df["status"] == "retire"][
                     "factor"].tolist()}
        self.results = stats
        return stats