# -*- coding: utf-8 -*-
"""配置更新"""

import os
import re
import json
import shutil
from datetime import datetime


class ConfigUpdater:
    def __init__(self, config_path="config.py"):
        self.path = config_path
        self.backup_dir = "config_backups"
        self.versions = []
        os.makedirs(self.backup_dir, exist_ok=True)

    def backup(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"config_{ts}.py")
        if os.path.exists(self.path):
            shutil.copy(self.path, backup_path)
            print(f"  💾 备份: {backup_path}")
        return backup_path

    def update_param(self, param_name, new_value, reason=""):
        if not os.path.exists(self.path):
            return False
        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = rf"^({param_name}\s*=\s*)([0-9._eE+-]+)(.*)$"
        match = re.search(pattern, content, re.MULTILINE)
        if not match:
            return False
        old_value = match.group(2)
        new_line = f"{match.group(1)}{new_value:.6f}  # {reason}"
        content = re.sub(pattern, new_line, content, count=1,
                         flags=re.MULTILINE)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  ✏️ {param_name}: {old_value} → {new_value:.6f}")
        self.versions.append({"time": datetime.now().isoformat(),
                              "param": param_name, "old": old_value,
                              "new": f"{new_value:.6f}",
                              "reason": reason})
        return True

    def apply_feedback(self, feedback):
        applied = []
        self.backup()
        if "slippage" in feedback:
            if self.update_param("SLIPPAGE", feedback["slippage"],
                                  "实盘反馈"):
                applied.append(("SLIPPAGE", feedback["slippage"]))
        if "commission" in feedback:
            if self.update_param("COMMISSION_RATE",
                                  feedback["commission"], "实盘反馈"):
                applied.append(("COMMISSION_RATE",
                                feedback["commission"]))
        with open(os.path.join(self.backup_dir, "versions.json"),
                  "w", encoding="utf-8") as f:
            json.dump(self.versions, f, ensure_ascii=False, indent=2)
        return {"applied": applied, "n_changes": len(applied)}