# -*- coding: utf-8 -*-
"""`[factor-lib]` 自动提交的收窄面：只许签掉因子库那几个文件。

仓库是**一个**（`etf/v1` 里没有嵌套 `.git`，git 上溯到工作区根的 `.git`），而
`git commit` 不带路径时提交的是**整个暂存区**。跑批期间并行会话或用户自己
`git add` 的东西就会被这笔自动提交一起签掉（09-27 实测发生过一次）。
"""

import os
import subprocess

import pytest

from factor_library_git import GitManager

LIB_FILES = ["data/library/factor_library.md",
             "data/library/factor_library_index.json",
             "data/library/factor_library.csv"]
FOREIGN = "src/foreign_work_in_progress.py"          # 相对本线根目录
FOREIGN_AT_ROOT = "etf/v1/" + FOREIGN             # 同一个文件，相对工作区根


def _git(repo_dir, *args):
    return subprocess.run(["git"] + list(args), cwd=str(repo_dir),
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    """复刻真实形状：工作区根是仓库，本线根目录只是它的一个子目录。"""
    root = tmp_path / "worktree"
    line_root = root / "etf" / "v1"
    (line_root / "data" / "library").mkdir(parents=True)
    (line_root / "src").mkdir(parents=True)
    for f in LIB_FILES:
        (line_root / f).write_text("因子库 v0\n", encoding="utf-8")
    (line_root / FOREIGN).write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.name", "probe")
    _git(root, "config", "user.email", "probe@local")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    return root, line_root


def _params(line_root):
    return {"enabled": True, "auto_commit": True, "git_dir": str(line_root),
            "tracked_files": LIB_FILES, "commit_prefix": "[factor-lib]",
            "commit_author": "probe", "commit_email": "probe@local"}


def _touched(root, rev="HEAD"):
    return set(_git(root, "show", "--name-only", "--format=", rev).split())


def test_auto_commit_leaves_others_staged_files_alone(repo):
    root, line_root = repo
    # 因子库动了（该被自动提交）
    (line_root / LIB_FILES[0]).write_text("因子库 v1\n", encoding="utf-8")
    # 别人已经 stage 了一笔不相干的改动，正等着自己提交
    (line_root / FOREIGN).write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", FOREIGN_AT_ROOT)

    assert GitManager(_params(line_root)).commit("因子库更新: 1 总 / 1 活跃") is True

    assert _touched(root) == {os.path.join("etf/v1", LIB_FILES[0])}
    # 正对照：暂存区里那笔别人的活还在原地，没被吞、也没被清空
    assert FOREIGN_AT_ROOT in _git(root, "diff", "--cached", "--name-only").split()
    assert _git(root, "status", "--porcelain", FOREIGN_AT_ROOT).startswith("M ")


def test_pathspec_is_what_makes_the_difference(repo):
    """把老写法照抄一遍当反证：不带 `--` 路径就会把暂存区整个签掉。

    上一条断言之所以不是恒真，是因为这里能实测复现故障本身。
    """
    root, line_root = repo
    (line_root / LIB_FILES[0]).write_text("因子库 v1\n", encoding="utf-8")
    (line_root / FOREIGN).write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", FOREIGN_AT_ROOT)
    _git(line_root, "add", *LIB_FILES)

    _git(line_root, "commit", "-qm", "[factor-lib] 老写法（不带路径）")

    touched = _touched(root)
    assert os.path.join("etf/v1", FOREIGN) in touched
    assert FOREIGN_AT_ROOT not in _git(root, "diff", "--cached", "--name-only").split()
