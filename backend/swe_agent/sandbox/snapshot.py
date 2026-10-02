"""Per-execution workspace snapshots.

Tests never run against the agent's live workspace. Each execution gets a fresh copy containing
only regular files that resolve inside the workspace (symlinks are materialised only if their
target is inside; others are skipped), without `.git`. The copy is disposable: whatever the code
under test writes there is thrown away, and it can never affect the diff the agent produces.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.workspace import Workspace

OUT_DIR = ".swe_out"
MAX_SNAPSHOT_BYTES = 200_000_000


def make_snapshot(
    ws: Workspace, dest: Path, extra_files: dict[str, Path] | None = None
) -> tuple[Path, list[str]]:
    """Copy the workspace to `dest`. Returns (dest, skipped paths)."""
    dest.mkdir(parents=True, exist_ok=False)
    skipped: list[str] = []
    total = 0
    for rel in ws.ls_files():
        try:
            src = ws.jail.resolve(rel)
        except PathPolicyError:
            skipped.append(rel)
            continue
        if not src.is_file():
            continue
        total += src.stat().st_size
        if total > MAX_SNAPSHOT_BYTES:
            raise PathPolicyError("workspace too large to snapshot")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)  # copies content; never copies a symlink
    for rel, src in (extra_files or {}).items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    (dest / OUT_DIR).mkdir()
    _open_permissions(dest)
    return dest, skipped


def _open_permissions(root: Path) -> None:
    """The container runs as an unprivileged uid that differs from the host user; the copy is
    disposable, so make it writable for that uid (pytest may write tmp files next to tests)."""
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o777)
        for f in filenames:
            p = os.path.join(dirpath, f)
            os.chmod(
                p,
                stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IRGRP
                | stat.S_IWGRP
                | stat.S_IROTH
                | stat.S_IWOTH,
            )
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]


def remove_snapshot(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
