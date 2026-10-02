"""Real Docker executions: results, failure modes, limits, isolation, cleanup."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from swe_agent.repository.workspace import Workspace
from swe_agent.sandbox.docker import EnvSetupError
from swe_agent.sandbox.runner import LABEL, InvalidSelectionError
from swe_agent.sandbox.service import SandboxService
from swe_agent.schemas.state import BaselineResult, FailureCategory, TestRunKind, TestRunStatus
from swe_agent.verification.classifier import classify

pytestmark = pytest.mark.docker

PKGS = {"users_service", "tests"}


def _write(ws: Workspace, rel: str, text: str) -> None:
    p = ws.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _containers(label_value: str | None = None) -> list[str]:
    flt = f"label={LABEL}.run={label_value}" if label_value else f"label={LABEL}=1"
    out = subprocess.run(["docker", "ps", "-aq", "--filter", flt], capture_output=True, text=True)
    return out.stdout.split()


def _baseline(svc: SandboxService, ws: Workspace, image: str) -> BaselineResult:
    r = svc.run_tests(ws, image, TestRunKind.BASELINE)
    return BaselineResult(
        image=image,
        run_id=r.id,
        passing=r.passed,
        failing=sorted(r.not_passing),
        collection_errors=r.collection_errors,
        status=r.status,
    )


# --------------------------------------------------------------------------- results


def test_successful_run(svc: SandboxService, workspace: Workspace, image: str) -> None:
    r = svc.run_tests(workspace, image, TestRunKind.BASELINE)
    assert r.status is TestRunStatus.PASSED and r.exit_code == 0
    assert sorted(r.passed) == [
        "tests/test_api.py::test_create_user",
        "tests/test_api.py::test_get_missing_user_is_404",
        "tests/test_api.py::test_missing_name_is_422",
    ]
    assert r.failed == [] and r.junit_artifact_id and r.log_artifact_id
    assert r.command[:3] == ["python", "-m", "pytest"]


def test_failing_run(svc: SandboxService, workspace: Workspace, image: str) -> None:
    base = _baseline(svc, workspace, image)
    _write(
        workspace,
        "users_service/validation.py",
        (workspace.root / "users_service/validation.py")
        .read_text()
        .replace("email.strip().lower()", "email.strip()"),
    )
    r = svc.run_tests(workspace, image, TestRunKind.TARGET)
    assert r.status is TestRunStatus.FAILED and r.exit_code == 1
    assert r.failed == ["tests/test_api.py::test_create_user"]
    assert "assert" in r.failures[0].message
    a = classify(r, base, [], PKGS)
    assert a is not None and a.category is FailureCategory.REGRESSION and a.route == "plan"
    assert a.regressions == ["tests/test_api.py::test_create_user"]


def test_syntax_failure(svc: SandboxService, workspace: Workspace, image: str) -> None:
    base = _baseline(svc, workspace, image)
    _write(workspace, "users_service/store.py", "def broken(:\n    pass\n")
    r = svc.run_tests(workspace, image, TestRunKind.TARGET)
    assert r.collection_errors == ["tests/test_api.py"]
    a = classify(r, base, [], PKGS)
    assert a is not None and a.category is FailureCategory.SYNTAX_ERROR and a.route == "implement"
    imp = svc.import_check(workspace, image, ["users_service.store", "users_service.validation"])
    assert imp.status is TestRunStatus.FAILED
    assert imp.errors == ["users_service.store"] and imp.passed == ["users_service.validation"]
    assert "SyntaxError" in imp.failures[0].excerpt


def test_missing_dependency(svc: SandboxService, workspace: Workspace, image: str) -> None:
    base = _baseline(svc, workspace, image)
    _write(workspace, "users_service/extra.py", "import definitely_not_installed_pkg\n")
    _write(
        workspace, "tests/test_extra.py", "import users_service.extra\n\ndef test_x():\n    pass\n"
    )
    r = svc.run_tests(workspace, image, TestRunKind.TARGET)
    assert "tests/test_extra.py" in r.collection_errors
    assert len(r.passed) == 3  # other modules still ran (--continue-on-collection-errors)
    a = classify(r, base, [], PKGS)
    assert a is not None and a.category is FailureCategory.MISSING_DEPENDENCY
    assert a.route == "abort"


def test_unresolvable_requirement_is_env_setup_error(
    svc: SandboxService, workspace: Workspace
) -> None:
    _write(workspace, "requirements.txt", "this-package-does-not-exist-swe-agent==0.0.1\n")
    with pytest.raises(EnvSetupError, match="dependency download failed"):
        svc.prepare_image(workspace)


def test_invalid_command_and_selection(
    svc: SandboxService, workspace: Workspace, image: str
) -> None:
    for bad in (["--collect-only"], ["tests/test_api.py; id"], ["../../etc/passwd"]):
        with pytest.raises(InvalidSelectionError):
            svc.run_tests(workspace, image, TestRunKind.TARGET, selection=bad)
    # syntactically valid but nonexistent test id: pytest usage error, classified, not a crash
    r = svc.run_tests(
        workspace, image, TestRunKind.TARGET, selection=["tests/test_api.py::test_nope"]
    )
    assert r.status is TestRunStatus.USAGE_ERROR and r.exit_code == 4
    a = classify(r, _baseline(svc, workspace, image), [], PKGS)
    assert a is not None and a.category is FailureCategory.INVALID_SELECTION


# --------------------------------------------------------------------------- limits


def test_timeout_kills_and_cleans_up(svc: SandboxService, workspace: Workspace, image: str) -> None:
    _write(workspace, "tests/test_hang.py", "def test_hang():\n    while True:\n        pass\n")
    r = svc.run_tests(
        workspace, image, TestRunKind.TARGET, selection=["tests/test_hang.py"], timeout=4
    )
    assert r.status is TestRunStatus.TIMEOUT and r.timed_out and r.exit_code is None
    assert r.duration_ms < 40_000
    assert _containers(svc.sandbox.run_label) == []


def test_memory_limit(svc: SandboxService, workspace: Workspace, image: str) -> None:
    _write(
        workspace,
        "tests/test_mem.py",
        "def test_mem():\n    blocks = []\n    for _ in range(64):\n"
        "        blocks.append(bytearray(64 * 1024 * 1024))\n",
    )
    r = svc.run_tests(workspace, image, TestRunKind.TARGET, selection=["tests/test_mem.py"])
    # 4 GiB requested vs 512m limit: the kernel OOM-kills the process
    assert r.status is TestRunStatus.RESOURCE_LIMIT, (r.status, r.exit_code, r.output_tail[-300:])


def test_pid_limit(svc: SandboxService, workspace: Workspace, image: str) -> None:
    _write(
        workspace,
        "tests/test_fork.py",
        """
import os, signal, time
def test_fork_bomb_is_bounded():
    children, blocked = [], None
    try:
        for _ in range(2000):
            pid = os.fork()
            if pid == 0:  # child: cheap copy-on-write sleeper
                time.sleep(60)
                os._exit(0)
            children.append(pid)
    except OSError as exc:  # EAGAIN from the pids cgroup
        blocked = exc
    finally:
        for pid in children:
            os.kill(pid, signal.SIGKILL)
    assert blocked is not None, f"forked {len(children)} processes without hitting the limit"
    assert len(children) < 256
""",
    )
    r = svc.run_tests(
        workspace, image, TestRunKind.TARGET, selection=["tests/test_fork.py"], timeout=120
    )
    assert r.status is TestRunStatus.PASSED, r.output_tail[-800:]


def test_output_is_capped(svc: SandboxService, workspace: Workspace, image: str) -> None:
    svc.sandbox.s = svc.sandbox.s.model_copy(update={"max_output_bytes": 20_000})
    _write(
        workspace,
        "tests/test_spam.py",
        "def test_spam():\n    for _ in range(200000):\n        print('x' * 50)\n"
        "    assert False\n",
    )
    r = svc.run_tests(workspace, image, TestRunKind.TARGET, selection=["tests/test_spam.py"])
    log = Path(svc.artifacts.index[r.log_artifact_id].path).read_text()
    assert len(log) < 25_000 and "bytes of output omitted" in log
    assert r.failed == ["tests/test_spam.py::test_spam"]


# --------------------------------------------------------------------------- isolation

ISOLATION_TEST = """
import os, socket, subprocess, pathlib

def test_isolation():
    report = {}
    report["uid"] = os.getuid()
    report["cap_eff"] = next(l for l in open("/proc/self/status") if l.startswith("CapEff")).split()[1]
    report["secrets_visible"] = [k for k in ("SWE_SECRET_TOKEN", "AWS_SECRET_ACCESS_KEY") if k in os.environ]
    report["env_count"] = len(os.environ)
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3); report["network"] = "open"
    except OSError as exc:
        report["network"] = f"blocked: {type(exc).__name__}"
    for target in ("/usr/pwned", "/etc/pwned", "/pwned"):
        try:
            open(target, "w").write("x"); report[target] = "writable"
        except OSError:
            report[target] = "blocked"
    report["docker_sock"] = os.path.exists("/var/run/docker.sock")
    report["host_path_visible"] = os.path.exists(os.environ.get("SWE_HOST_PATH_PROBE", "/nonexistent-probe"))
    pathlib.Path("users_service/api.py").write_text("# overwritten from inside the sandbox\\n")
    pathlib.Path("tests/test_api.py").write_text("# tests tampered from inside the sandbox\\n")
    # fail on purpose so the report travels back in the JUnit failure message
    assert False, "ISOLATION_REPORT=" + __import__("json").dumps(report)
"""


def test_filesystem_network_env_isolation(
    svc: SandboxService, workspace: Workspace, image: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWE_SECRET_TOKEN", "super-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    _write(workspace, "tests/test_isolation.py", ISOLATION_TEST)
    api_before = (workspace.root / "users_service/api.py").read_text()
    tests_before = (workspace.root / "tests/test_api.py").read_text()
    r = svc.run_tests(workspace, image, TestRunKind.TARGET, selection=["tests/test_isolation.py"])
    assert r.failed == ["tests/test_isolation.py::test_isolation"], r.output_tail[-1000:]
    log = Path(svc.artifacts.index[r.log_artifact_id].path).read_text()
    msg = r.failures[0].message
    rep = json.loads(msg[msg.index("ISOLATION_REPORT=") + 17 :].split("\n")[0].rstrip("'\""))

    assert rep["uid"] == 10001
    assert int(rep["cap_eff"], 16) == 0  # no capabilities
    assert rep["network"].startswith("blocked")
    assert rep["/usr/pwned"] == rep["/etc/pwned"] == rep["/pwned"] == "blocked"  # read-only rootfs
    assert rep["docker_sock"] is False
    assert rep["secrets_visible"] == []
    assert rep["env_count"] < 15  # only the explicit allow-list (+ image PATH etc.)
    assert "super-secret" not in log
    assert str(workspace.root) not in log
    # writes inside the sandbox hit a disposable snapshot, never the agent's workspace
    assert (workspace.root / "users_service/api.py").read_text() == api_before
    assert (workspace.root / "tests/test_api.py").read_text() == tests_before


def test_host_paths_not_mounted(svc: SandboxService, workspace: Workspace, image: str) -> None:
    probe = workspace.root.parent / "host_only_marker.txt"
    probe.write_text("HOST")
    _write(
        workspace,
        "tests/test_probe.py",
        f"import os\ndef test_probe():\n    assert not os.path.exists({str(probe)!r})\n"
        "    assert not os.path.exists('/home/claude')\n",
    )
    r = svc.run_tests(workspace, image, TestRunKind.TARGET, selection=["tests/test_probe.py"])
    assert r.status is TestRunStatus.PASSED, r.output_tail[-500:]


def test_symlinks_escaping_workspace_are_not_copied(
    svc: SandboxService, workspace: Workspace, image: str, tmp_path: Path
) -> None:
    secret = tmp_path / "host_secret.txt"
    secret.write_text("HOST-SECRET")
    os.symlink(secret, workspace.root / "leak.txt")
    _write(
        workspace,
        "tests/test_leak.py",
        "import os\ndef test_leak():\n    assert not os.path.exists('leak.txt')\n",
    )
    r = svc.run_tests(workspace, image, TestRunKind.TARGET, selection=["tests/test_leak.py"])
    assert r.status is TestRunStatus.PASSED, r.output_tail[-500:]


# --------------------------------------------------------------------------- cleanup


def test_cleanup(svc: SandboxService, workspace: Workspace, image: str) -> None:
    svc.run_tests(workspace, image, TestRunKind.BASELINE)
    svc.import_check(workspace, image, ["users_service.api"])
    assert _containers(svc.sandbox.run_label) == []
    assert not any(svc.scratch_root.iterdir())  # snapshots deleted
    # an orphan from a "crashed worker" is reaped
    subprocess.run(
        [
            "docker",
            "create",
            "--label",
            f"{LABEL}=1",
            "--label",
            f"{LABEL}.run={svc.sandbox.run_label}",
            image,
            "true",
        ],
        check=True,
        capture_output=True,
    )
    assert len(_containers(svc.sandbox.run_label)) == 1
    assert svc.cleanup() == 1
    assert _containers(svc.sandbox.run_label) == []
    events = [e for e in svc.recorder.events if e.event == "sandbox_exec"]
    assert {e.data["op"] for e in events} >= {"build_image", "baseline", "import_check"}
