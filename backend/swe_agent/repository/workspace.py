"""Hardened git execution and workspace preparation.

Threat: a repository is untrusted input. Git can execute code from a repository via hooks,
`core.fsmonitor`, `core.sshCommand`, filter/diff/textconv drivers, or `ext::` transports.
Every git invocation here therefore:
- runs with an argv list (never a shell) and a minimal environment,
- ignores system/global config (GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null),
- disables hooks, fsmonitor, external diff and textconv, pagers, credential helpers,
- blocks the `ext` and (by default) `file` transports, never recurses into submodules,
- clones with an empty template dir so no sample hooks are copied.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from swe_agent.core.errors import RepoError
from swe_agent.repository.pathjail import PathJail

_HARDENING = [
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.sshCommand=false",
    "-c",
    "core.pager=cat",
    "-c",
    "core.askPass=",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "submodule.recurse=false",
    "-c",
    "advice.detachedHead=false",
    "-c",
    "init.defaultBranch=main",
    "-c",
    "user.name=swe-agent",
    "-c",
    "user.email=swe-agent@localhost",
]

# Env vars passed through to *network* clones only (proxies / CA bundles), never secrets.
_NETWORK_ENV_PASSTHROUGH = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSL_CERT_FILE",
    "GIT_SSL_CAINFO",
    "REQUESTS_CA_BUNDLE",
)

_SAFE_REV = re.compile(r"^[0-9A-Za-z_][0-9A-Za-z_./~^@{}-]{0,99}$")
_ALLOWED_URL = re.compile(r"^https?://[^\s]+$")


def _git_env(home: str, *, network: bool = False) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    if network:
        for key in _NETWORK_ENV_PASSTHROUGH:
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def run_git(
    args: list[str],
    *,
    cwd: Path | None,
    timeout: float = 60.0,
    check: bool = True,
    network: bool = False,
    extra_config: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="swe-git-home-") as home:
        argv = ["git", *_HARDENING, *(extra_config or []), *args]
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env=_git_env(home, network=network),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepoError(f"git {args[0]} timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise RepoError(f"git {args[0]} failed ({proc.returncode}): {proc.stderr.strip()[:800]}")
    return proc


def validate_rev(rev: str) -> str:
    if not _SAFE_REV.match(rev) or ".." in rev:
        raise ValueError(f"invalid revision: {rev!r}")
    return rev


_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "node_modules",
    ".ruff_cache",
)


@dataclass
class Workspace:
    root: Path
    branch: str
    base_commit: str

    @property
    def jail(self) -> PathJail:
        return PathJail(self.root)

    def git(self, *args: str, timeout: float = 30.0, check: bool = True) -> str:
        return run_git(list(args), cwd=self.root, timeout=timeout, check=check).stdout

    def ls_files(self) -> list[str]:
        out = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return sorted({p for p in out.split("\x00") if p})

    def stage_all(self) -> None:
        self.git("add", "-A", "--", ".")

    def diff(self) -> str:
        """Unified diff of everything changed since the base commit (incl. new files)."""
        self.stage_all()
        return self.git(
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            self.base_commit,
            "--",
        )

    def exists_at_base(self, rel: str) -> bool:
        proc = run_git(["cat-file", "-e", f"{self.base_commit}:{rel}"], cwd=self.root, check=False)
        return proc.returncode == 0

    def reset_to_base(self, keep: dict[str, str] | None = None) -> None:
        """Discard every change since the base commit (tracked, staged and untracked files),
        then re-create `keep` files (e.g. the reproduction test). Used to roll back a failed
        direction before re-exploring."""
        self.git("reset", "-q", "--hard", self.base_commit)
        self.git("clean", "-q", "-f", "-d")
        for rel, content in (keep or {}).items():
            path = self.jail.resolve(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def numstat(self) -> list[tuple[int, int, str]]:
        self.stage_all()
        out = self.git(
            "diff",
            "--cached",
            "--numstat",
            "--no-ext-diff",
            "--no-textconv",
            self.base_commit,
            "--",
        )
        rows: list[tuple[int, int, str]] = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            added = int(parts[0]) if parts[0].isdigit() else 0
            removed = int(parts[1]) if parts[1].isdigit() else 0
            rows.append((added, removed, parts[2]))
        return rows


def patch_applies_at_base(
    ws: Workspace, patch_text: str, *, timeout: float = 120.0
) -> tuple[bool, str]:
    """Check the exported patch applies cleanly to a pristine checkout of the base commit (what
    `git apply` on the user's side will see). Uses a throwaway local clone; hooks disabled."""
    with tempfile.TemporaryDirectory(prefix="swe-applycheck-") as tmp:
        dest = Path(tmp) / "repo"
        run_git(
            ["clone", "-q", "--no-hardlinks", "--template=", "--no-checkout", "--",
             str(ws.root), str(dest)],
            cwd=None, timeout=timeout, extra_config=["-c", "protocol.file.allow=always"],
        )  # fmt: skip
        run_git(["checkout", "-q", "--detach", ws.base_commit], cwd=dest, timeout=timeout)
        patch_file = Path(tmp) / "final.patch"
        patch_file.write_text(patch_text, encoding="utf-8")
        proc = run_git(["apply", "--check", "--", str(patch_file)], cwd=dest, check=False)
        return proc.returncode == 0, proc.stderr.strip()[:800]


def _is_local_source(source: str) -> bool:
    return not source.startswith(("http://", "https://", "git@", "ssh://", "git://", "ext::"))


def prepare_workspace(
    source: str, dest: Path, *, task_id: str, timeout: float = 300.0
) -> Workspace:
    """Create an isolated working copy of `source` on branch `agent/<task_id>`.

    The user's original repository is never modified.
    """
    if dest.exists():
        raise RepoError(f"workspace already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    if _is_local_source(source):
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise RepoError(f"local source is not a directory: {source}")
        if (src / ".git").exists():
            # Local clone: needs the file transport for this one command only.
            run_git(
                [
                    "clone",
                    "--no-hardlinks",
                    "--template=",
                    "--no-recurse-submodules",
                    "--",
                    str(src),
                    str(dest),
                ],
                cwd=None,
                timeout=timeout,
                extra_config=["-c", "protocol.file.allow=always"],
            )
        else:
            shutil.copytree(src, dest, ignore=_COPY_IGNORE, symlinks=True)
            run_git(["init", "--template=", "-q"], cwd=dest)
            run_git(["add", "-A", "--", "."], cwd=dest)
            run_git(["commit", "-q", "--no-verify", "-m", "swe-agent: base snapshot"], cwd=dest)
    else:
        if not _ALLOWED_URL.match(source):
            raise RepoError("only http(s) repository URLs or local paths are supported")
        run_git(
            ["clone", "--template=", "--no-recurse-submodules", "--", source, str(dest)],
            cwd=None,
            timeout=timeout,
            network=True,
            extra_config=["-c", "protocol.file.allow=never"],
        )

    branch = f"agent/{task_id}"
    run_git(["checkout", "-q", "-b", branch], cwd=dest)
    base = run_git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
    return Workspace(root=dest.resolve(), branch=branch, base_commit=base)
