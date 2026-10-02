from __future__ import annotations

import os
from pathlib import Path

import pytest

from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.pathjail import PathJail, WritePolicy, is_test_path


@pytest.fixture
def jail(tmp_path: Path) -> PathJail:
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    return PathJail(root)


def test_resolves_normal_paths(jail: PathJail) -> None:
    assert jail.resolve("pkg/a.py", must_exist=True) == jail.root / "pkg" / "a.py"
    assert jail.resolve("./pkg/a.py") == jail.root / "pkg" / "a.py"
    assert jail.resolve(".") == jail.root
    assert jail.relative(jail.root / "pkg" / "a.py") == "pkg/a.py"


@pytest.mark.parametrize(
    "bad",
    [
        "../outside.py",
        "pkg/../../x",
        "/etc/passwd",
        "pkg/\x00a.py",
        "",
        "   ",
        ".git/config",
        "pkg/.git/x",
        "..\\..\\windows",
    ],
)
def test_rejects_escapes_and_forbidden(jail: PathJail, bad: str) -> None:
    with pytest.raises(PathPolicyError):
        jail.resolve(bad)


def test_rejects_symlink_escape(jail: PathJail, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN")
    os.symlink(secret, jail.root / "link.txt")
    os.symlink(tmp_path, jail.root / "linkdir")
    with pytest.raises(PathPolicyError, match="escapes"):
        jail.resolve("link.txt")
    with pytest.raises(PathPolicyError, match="escapes"):
        jail.resolve("linkdir/secret.txt")


def test_allows_symlink_inside(jail: PathJail) -> None:
    os.symlink(jail.root / "pkg" / "a.py", jail.root / "alias.py")
    assert jail.resolve("alias.py") == jail.root / "pkg" / "a.py"


def test_missing_file_with_must_exist(jail: PathJail) -> None:
    with pytest.raises(PathPolicyError, match="no such file"):
        jail.resolve("pkg/missing.py", must_exist=True)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_a.py", True),
        ("pkg/test_x.py", True),
        ("pkg/x_test.py", True),
        ("test/helpers.py", True),
        ("pkg/testing.py", False),
        ("pkg/contest.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


def test_write_policy_implement() -> None:
    policy = WritePolicy(allow_source=True)
    policy.check("pkg/a.py", exists=True)
    policy.check("pkg/new.py", exists=False)
    for bad in [
        "tests/test_a.py",
        "conftest.py",
        "pkg/conftest.py",
        "pyproject.toml",
        "setup.py",
        "requirements-dev.txt",
        ".github/workflows/ci.yml",
    ]:
        with pytest.raises(PathPolicyError):
            policy.check(bad, exists=True)
    with pytest.raises(PathPolicyError):
        policy.check("tests/test_new.py", exists=False)


def test_write_policy_reproduce_allows_only_new_tests() -> None:
    policy = WritePolicy(allow_source=False, allow_new_tests=True)
    policy.check("tests/test_repro.py", exists=False)
    with pytest.raises(PathPolicyError):
        policy.check("tests/test_existing.py", exists=True)
    with pytest.raises(PathPolicyError):
        policy.check("pkg/a.py", exists=True)
