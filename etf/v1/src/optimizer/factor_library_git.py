# -*- coding: utf-8 -*-
"""因子库 Git 版本控制"""

import os
import subprocess
from datetime import datetime
from config import FACTOR_LIBRARY_GIT


class GitManager:
    def __init__(self, params=None):
        self.p = {**FACTOR_LIBRARY_GIT, **(params or {})}
        self.repo_dir = self.p["git_dir"]
        self._available = self._check()
        if self._available and self.p["enabled"]:
            self._ensure_repo()

    @staticmethod
    def _check():
        try:
            subprocess.run(["git", "--version"],
                           capture_output=True, check=True)
            return True
        except Exception:
            return False

    def _run(self, args, check=False):
        try:
            r = subprocess.run(["git"] + args, cwd=self.repo_dir,
                                capture_output=True, text=True, check=check)
            return True, r.stdout.strip(), r.stderr.strip()
        except subprocess.CalledProcessError as e:
            return False, e.stdout or "", e.stderr or ""
        except Exception as e:
            return False, "", str(e)

    def _ensure_repo(self):
        ok, out, _ = self._run(["rev-parse", "--is-inside-work-tree"])
        if not ok or out != "true":
            self._run(["init"])
            gitignore = os.path.join(self.repo_dir, ".gitignore")
            if not os.path.exists(gitignore):
                with open(gitignore, "w") as f:
                    f.write("data_cache/\n__pycache__/\n*.pyc\n"
                            "trial_counter.json\nrdagent_output/\n")
            self._run(["config", "user.name", self.p["commit_author"]])
            self._run(["config", "user.email", self.p["commit_email"]])

    def commit(self, message=None):
        if not self._available or not self.p["enabled"]:
            return False
        files = [f for f in self.p["tracked_files"]
                 if os.path.exists(os.path.join(self.repo_dir, f))]
        if not files:
            return False
        ok, out, _ = self._run(["status", "--porcelain"] + files)
        if not ok or not out:
            return False
        self._run(["add"] + files)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = message or f"因子库更新 @ {ts}"
        ok, _, err = self._run(["commit", "-m",
                                 f"{self.p['commit_prefix']} {msg}"])
        if ok:
            print(f"  📝 git commit: {msg}")
        return ok


_git_manager = None

def get_git_manager():
    global _git_manager
    if _git_manager is None:
        _git_manager = GitManager()
    return _git_manager