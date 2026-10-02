"""A repository is untrusted input: git must never execute code from it, and tools must never
leak host files or secrets through it."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from swe_agent.repository.workspace import _git_env, prepare_workspace, run_git
from swe_agent.tools.base import ToolContext
from swe_agent.tools.executor import ToolExecutor
from tests.conftest import git


def _marker_script(path: Path, marker: Path) -> Path:
    path.write_text(f"#!/bin/sh\necho pwned > {marker}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_source_repo_hooks_are_not_copied_or_run(users_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hook_ran"
    hooks = users_repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("post-checkout", "post-merge", "pre-commit", "reference-transaction"):
        _marker_script(hooks / name, marker)
    ws = prepare_workspace(str(users_repo), tmp_path / "w" / "repo", task_id="h1")
    assert not marker.exists()
    assert (
        not any((ws.root / ".git" / "hooks").glob("*"))
        if (ws.root / ".git" / "hooks").exists()
        else True
    )


def test_committed_hooks_dir_is_ignored_even_if_configured(workspace, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Even if a workspace's local config pointed hooksPath at a repo-controlled directory
    (it never should), our -c core.hooksPath=/dev/null override wins."""
    marker = tmp_path / "hook_ran"
    evil = workspace.root / "evil_hooks"
    evil.mkdir()
    _marker_script(evil / "post-checkout", marker)
    git(workspace.root, "config", "core.hooksPath", str(evil))
    workspace.git("checkout", "-q", "-b", "other")
    assert not marker.exists()


@pytest.mark.parametrize(
    ("key", "trigger"),
    [("core.fsmonitor", ("status",)), ("diff.external", ("status",))],
)
def test_config_based_execution_is_disabled(
    workspace, tmp_path: Path, key: str, trigger: tuple[str, ...]
) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / f"{key}_ran"
    script = _marker_script(tmp_path / "evil.sh", marker)
    git(workspace.root, "config", key, str(script))
    (workspace.root / "users_service" / "api.py").write_text("# changed\n")
    workspace.git(*trigger)
    workspace.diff()
    assert not marker.exists()


def test_textconv_driver_is_not_run(workspace, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / "textconv_ran"
    script = _marker_script(tmp_path / "conv.sh", marker)
    (workspace.root / ".gitattributes").write_text("*.py diff=evil\n")
    git(workspace.root, "config", "diff.evil.textconv", str(script))
    (workspace.root / "users_service" / "api.py").write_text("# changed\n")
    workspace.diff()
    assert not marker.exists()


def test_ext_transport_blocked(tmp_path: Path) -> None:
    marker = tmp_path / "ext_ran"
    proc = run_git(["ls-remote", f"ext::sh -c touch% {marker}"], cwd=tmp_path, check=False)
    assert proc.returncode != 0
    assert not marker.exists()


def test_git_env_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s3cr3t")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    local = _git_env("/tmp/h")
    net = _git_env("/tmp/h", network=True)
    for env in (local, net):
        assert "AWS_SECRET_ACCESS_KEY" not in env and "GITHUB_TOKEN" not in env
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert "HTTPS_PROXY" not in local and net["HTTPS_PROXY"] == "http://proxy:3128"


async def test_committed_symlink_to_host_file_is_unreadable(
    users_repo: Path, tmp_path: Path, executor: ToolExecutor
) -> None:
    secret = tmp_path / "host_secret.txt"
    secret.write_text("HOST-SECRET")
    os.symlink(secret, users_repo / "notes.txt")
    os.symlink("/etc", users_repo / "etc_link")
    git(users_repo, "add", "-A")
    git(users_repo, "commit", "-q", "-m", "add links")
    ws = prepare_workspace(str(users_repo), tmp_path / "w2" / "repo", task_id="s1")
    from swe_agent.repository.symbols import SymbolIndex

    ctx = ToolContext(workspace=ws, index=SymbolIndex.build(ws.root, ws.ls_files()), node="explore")
    for args in ({"path": "notes.txt"}, {"path": "etc_link/passwd"}):
        r = await executor.invoke("read_file", args, ctx)
        assert not r.ok and r.error and r.error.type == "policy_violation"
        assert "HOST-SECRET" not in r.output and "root:" not in r.output
    r = await executor.invoke("search_code", {"pattern": "HOST-SECRET"}, ctx)
    assert "HOST-SECRET" not in r.output.replace("'HOST-SECRET'", "")  # only the echoed pattern
    r = await executor.invoke("list_files", {"path": "etc_link"}, ctx)
    assert not r.ok


async def test_git_internals_unreachable_through_tools(
    workspace, index, executor: ToolExecutor
) -> None:  # type: ignore[no-untyped-def]
    from swe_agent.repository.pathjail import WritePolicy

    ctx = ToolContext(
        workspace=workspace,
        index=index,
        node="implement",
        write_policy=WritePolicy(allow_source=True),
    )
    r = await executor.invoke("read_file", {"path": ".git/config"}, ctx)
    assert r.error and r.error.type == "policy_violation"
    r = await executor.invoke(
        "create_file", {"path": ".git/hooks/post-checkout", "content": "#!/bin/sh\n"}, ctx
    )
    assert r.error and r.error.type == "policy_violation"
    r = await executor.invoke("create_file", {"path": "sub/.git/config", "content": "x"}, ctx)
    assert r.error and r.error.type == "policy_violation"
