from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from swe_agent.core.errors import RepoError
from swe_agent.repository.workspace import prepare_workspace, validate_rev
from tests.conftest import FIXTURES, git


def test_prepare_from_git_repo_isolated(users_repo: Path, tmp_path: Path) -> None:
    ws = prepare_workspace(str(users_repo), tmp_path / "w" / "repo", task_id="t1")
    assert ws.branch == "agent/t1"
    assert len(ws.base_commit) == 40
    assert "users_service/api.py" in ws.ls_files()

    (ws.root / "users_service" / "api.py").write_text("# changed\n")
    (ws.root / "users_service" / "new.py").write_text("x = 1\n")
    diff = ws.diff()
    assert "+# changed" in diff
    assert "b/users_service/new.py" in diff
    stats = {p: (a, r) for a, r, p in ws.numstat()}
    assert stats["users_service/new.py"] == (1, 0)

    # original repository untouched: clean tree, still on main, no agent branch
    assert git(users_repo, "status", "--porcelain") == ""
    assert "agent/" not in git(users_repo, "branch")
    assert "# changed" not in (users_repo / "users_service" / "api.py").read_text()


def test_prepare_from_plain_directory(tmp_path: Path) -> None:
    src = tmp_path / "plain"
    shutil.copytree(FIXTURES / "users_service", src)
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    ws = prepare_workspace(str(src), tmp_path / "w" / "repo", task_id="t2")
    files = ws.ls_files()
    assert "users_service/api.py" in files
    assert not any("__pycache__" in f for f in files)
    assert not (src / ".git").exists()  # never initialises git in the user's directory


def test_workspace_must_not_exist(users_repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "w" / "repo"
    prepare_workspace(str(users_repo), dest, task_id="t3")
    with pytest.raises(RepoError, match="already exists"):
        prepare_workspace(str(users_repo), dest, task_id="t3")


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c touch% /tmp/pwned",
        "file:///etc",
        "git://example.com/x.git",
        "ssh://git@example.com/x.git",
        "git@github.com:x/y.git",
    ],
)
def test_rejects_unsupported_sources(url: str, tmp_path: Path) -> None:
    with pytest.raises(RepoError):
        prepare_workspace(url, tmp_path / "w", task_id="t4")


def test_exists_at_base(workspace) -> None:  # type: ignore[no-untyped-def]
    assert workspace.exists_at_base("users_service/api.py")
    assert not workspace.exists_at_base("users_service/nope.py")


@pytest.mark.parametrize("rev", ["HEAD", "HEAD~1", "a1b2c3d", "main", "v1.2.0", "HEAD^"])
def test_validate_rev_ok(rev: str) -> None:
    assert validate_rev(rev) == rev


@pytest.mark.parametrize("rev", ["--output=/tmp/x", "-p", "a..b", "HEAD; rm -rf /", "", "a b"])
def test_validate_rev_rejects(rev: str) -> None:
    with pytest.raises(ValueError):
        validate_rev(rev)
