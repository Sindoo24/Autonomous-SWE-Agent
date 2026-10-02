"""Benchmark task definitions and repository materialisation.

A task = a clean repository under benchmarks/repos/<repo> + `bug.patch` that injects the bug +
held-out tests the agent never sees. Materialising a task creates a fresh git repository:

- injection = "squashed": the bug is part of the single initial commit (no history to diff)
- injection = "commit":   clean initial commit, then the bug as its own commit (regressions)

The reference fix is the reverse of `bug.patch`; `validate` checks every task with it.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


def default_root() -> Path:
    """The benchmark dataset directory from configuration (SWE_BENCHMARKS_DIR)."""
    from swe_agent.config import Settings

    return Settings().benchmarks_dir.resolve()


class BenchmarkTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    repo: str
    category: str
    difficulty: Literal["easy", "medium", "hard"]
    injection: Literal["squashed", "commit"] = "squashed"
    commit_message: str = "Update"
    issue: str
    expected_behavior: str
    visible_failing: list[str] = []
    # filled by the loader
    dir: Path = Path()

    @property
    def bug_patch(self) -> Path:
        return self.dir / "bug.patch"

    def heldout_files(self) -> dict[str, Path]:
        return {
            f"tests_heldout/{p.name}": p for p in sorted((self.dir / "tests_heldout").glob("*.py"))
        }

    def heldout_test_count(self) -> int:
        """Number of held-out test functions (parametrised tests count once per function)."""
        n = 0
        for path in self.heldout_files().values():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ) and node.name.startswith("test"):
                    n += 1
        return n


def load_tasks(root: Path | None = None, only: list[str] | None = None) -> list[BenchmarkTask]:
    root = root or default_root()
    tasks = []
    for spec in sorted((root / "tasks").glob("*/task.toml")):
        data = tomllib.loads(spec.read_text(encoding="utf-8"))
        task = BenchmarkTask(**data, dir=spec.parent)
        if task.id != spec.parent.name:
            raise ValueError(f"{spec}: id {task.id!r} does not match directory")
        if only and task.id not in only:
            continue
        tasks.append(task)
    return tasks


def _git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        [  # noqa: S607 - git from PATH; argv only; hooks disabled
            "git",
            "-c",
            "user.name=bench",
            "-c",
            "user.email=bench@localhost",
            "-c",
            "init.defaultBranch=main",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        input=stdin,
    ).stdout


def materialize(task: BenchmarkTask, dest: Path, root: Path | None = None) -> Path:
    """Create the buggy repository for `task` at `dest` (a git repo)."""
    root = root or default_root()
    shutil.copytree(
        root / "repos" / task.repo, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    _git(dest, "init", "-q")
    patch = task.bug_patch.read_text(encoding="utf-8")
    if task.injection == "squashed":
        _git(dest, "apply", "--whitespace=nowarn", "-", stdin=patch)
        _git(dest, "add", "-A")
        _git(dest, "commit", "-qm", "Initial import")
    else:
        _git(dest, "add", "-A")
        _git(dest, "commit", "-qm", "Initial import")
        _git(dest, "apply", "--whitespace=nowarn", "-", stdin=patch)
        _git(dest, "commit", "-qam", task.commit_message)
    return dest


def apply_patch(repo: Path, patch_text: str, *, reverse: bool = False) -> None:
    args = ["apply", "--whitespace=nowarn", *(["-R"] if reverse else []), "-"]
    _git(repo, *args, stdin=patch_text)
