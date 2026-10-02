"""Sandbox logic that does not need Docker: argv hardening, selection validation, JUnit parsing,
status mapping, output capping, dependency discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_agent.config import SandboxSettings
from swe_agent.repository.workspace import prepare_workspace
from swe_agent.sandbox.deps import discover_dependencies
from swe_agent.sandbox.image import _platform_args
from swe_agent.sandbox.junit import parse_junit
from swe_agent.sandbox.runner import (
    SANDBOX_ENV,
    ExecOutcome,
    InvalidSelectionError,
    _CappedReader,
    build_run_argv,
    pytest_command,
    validate_modules,
    validate_selection,
)
from swe_agent.sandbox.service import status_from
from swe_agent.schemas.state import TestRunStatus
from tests.conftest import git

SAMPLE = (Path(__file__).parent / "junit_sample.xml").read_text()


def test_run_argv_has_every_hardening_flag() -> None:
    s = SandboxSettings(memory="512m", cpus=0.5, pids_limit=64, runtime="runsc")
    argv = build_run_argv(
        s,
        name="c1",
        image="img:1",
        workspace=Path("/snap"),
        run_label="r1",
        command=["python", "-V"],
    )
    joined = " ".join(argv)
    for flag in [
        "--network none",
        "--user 10001:10001",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 64",
        "--memory 512m",
        "--memory-swap 512m",
        "--cpus 0.5",
        "--ulimit nofile=1024:1024",
        "--init",
        "--runtime runsc",
        "--volume /snap:/workspace:rw",
    ]:
        assert flag in joined, flag
    assert "--privileged" not in joined and "docker.sock" not in joined
    assert "--tmpfs /tmp:rw,nosuid,nodev,size=256m" in joined
    # only the explicit allow-list of env vars
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert {e.split("=", 1)[0] for e in envs} == set(SANDBOX_ENV)
    # the image is followed by exactly our command
    assert argv[argv.index("img:1") + 1 :] == ["python", "-V"]
    assert "--volume" in argv and argv.count("--volume") == 1


def test_pytest_command_is_fixed_and_ends_options() -> None:
    cmd = pytest_command(["tests/test_a.py::test_x"])
    assert cmd[:3] == ["python", "-m", "pytest"]
    assert cmd[cmd.index("--") + 1 :] == ["tests/test_a.py::test_x"]
    assert "--continue-on-collection-errors" in cmd and "-p" in cmd
    assert pytest_command([])[-1] == "."


def test_validate_selection(workspace) -> None:  # type: ignore[no-untyped-def]
    ok = [
        "tests/test_api.py",
        "tests/test_api.py::test_create_user",
        "tests",
        "tests/test_api.py::TestX::test_y[a-1]",
    ]
    assert validate_selection(ok, workspace.jail) == ok
    for bad in [
        "--collect-only",
        "-x",
        "tests/test_api.py; rm -rf /",
        "../etc/passwd",
        "/etc/passwd",
        "tests/missing.py",
        "$(id)",
        "tests/test_api.py::a`id`",
        "",
    ]:
        with pytest.raises(InvalidSelectionError):
            validate_selection([bad], workspace.jail)


def test_validate_modules() -> None:
    assert validate_modules(["a.b", "pkg"]) == ["a.b", "pkg"]
    for bad in ["os; import x", "a-b", "__import__('os')", ""]:
        with pytest.raises(InvalidSelectionError):
            validate_modules([bad])


def test_parse_junit_real_sample() -> None:
    r = parse_junit(SAMPLE)
    assert r.collection_errors == ["tests/test_b.py"]
    assert r.failed == ["tests/test_a.py::TestK::test_a", "tests/test_a.py::test_err"]
    assert r.passed == ["tests/test_a.py::TestK::test_p[1]", "tests/test_a.py::TestK::test_p[2]"]
    assert r.skipped == 1 and r.errors == []
    msgs = {f.nodeid: f.message for f in r.failures}
    assert msgs["tests/test_a.py::test_err"] == "ValueError: boom"
    assert "nosuchmod" in next(f.excerpt for f in r.failures if f.nodeid == "tests/test_b.py")


def test_parse_junit_rejects_oversized() -> None:
    with pytest.raises(ValueError):
        parse_junit("<a>" + "x" * 5_000_001 + "</a>")


def _o(code: int | None, *, timed_out: bool = False, oom: bool = False) -> ExecOutcome:
    return ExecOutcome("c", [], code, timed_out, oom, 1, "", False)


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (_o(0), TestRunStatus.PASSED),
        (_o(1), TestRunStatus.FAILED),
        (_o(2), TestRunStatus.COLLECTION_ERROR),
        (_o(3), TestRunStatus.INFRA_ERROR),
        (_o(4), TestRunStatus.USAGE_ERROR),
        (_o(5), TestRunStatus.NO_TESTS),
        (_o(137), TestRunStatus.RESOURCE_LIMIT),
        (_o(137, oom=True), TestRunStatus.RESOURCE_LIMIT),
        (_o(None, timed_out=True), TestRunStatus.TIMEOUT),
        (_o(125), TestRunStatus.INFRA_ERROR),
    ],
)
def test_status_from(outcome: ExecOutcome, status: TestRunStatus) -> None:
    assert status_from(outcome, None) is status


def test_capped_reader_keeps_head_and_tail() -> None:
    r = _CappedReader(100)
    r.feed(b"H" * 30)
    for _ in range(50):
        r.feed(b"m" * 10)
    r.feed(b"END")
    text, truncated = r.text()
    assert truncated
    assert text.startswith("H" * 30) and text.endswith("END")
    assert "bytes of output omitted" in text
    assert len(text.encode()) < 200


def test_platform_args() -> None:
    args = _platform_args(SandboxSettings(python_version="3.12", manylinux_max="2_36"))
    assert "manylinux_2_36_x86_64" in args and "manylinux_2_37_x86_64" not in args
    assert args[args.index("--abi") + 1] == "cp312"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "src_repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def test_discover_dependencies(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo-pkg"\ndependencies = ["httpx>=0.27", '
            '"demo_pkg", "attrs ; python_version >= \'3.8\'"]\n'
            '[project.optional-dependencies]\ntest = ["pytest-asyncio"]\n'
            'docs = ["sphinx"]\n',
            "requirements.txt": "# comment\nrequests==2.32.3  # pinned\n-r requirements-extra.txt\n"
            "-e .\ngit+https://example.com/x.git\n--index-url https://evil\n",
            "requirements-extra.txt": "six\n",
            "demo_pkg/__init__.py": "",
        },
    )
    ws = prepare_workspace(str(root), tmp_path / "ws", task_id="d")
    spec = discover_dependencies(ws)
    assert spec.project_name == "demo-pkg"
    assert set(spec.requirements) == {
        "httpx>=0.27",
        'attrs; python_version >= "3.8"',
        "pytest-asyncio",
        "requests==2.32.3",
        "six",
    }
    assert "sphinx" not in spec.requirements
    joined = " ".join(spec.unsupported)
    assert "-e ." in joined and "git+https" in joined and "--index-url" in joined


def test_discover_dependencies_setup_py_not_executed(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    root = _repo(tmp_path, {"setup.py": f"open({str(marker)!r}, 'w').write('x')\n"})
    ws = prepare_workspace(str(root), tmp_path / "ws", task_id="d2")
    spec = discover_dependencies(ws)
    assert spec.uses_setup_py and spec.requirements == []
    assert any("setup.py" in u for u in spec.unsupported)
    assert not marker.exists()
