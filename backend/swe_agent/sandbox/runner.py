"""Hardened container execution.

Every execution:
- runs a fixed argv we construct (pytest or an import check); model text never becomes a command
- has no network (`--network none`), no capabilities, no privilege escalation, a read-only root
  filesystem, an unprivileged uid, memory/CPU/pid/file-descriptor limits and a wall-clock timeout
- sees only a disposable snapshot of the workspace mounted at /workspace, plus a size-limited
  tmpfs at /tmp; no host paths, no docker socket, and only the env vars listed in SANDBOX_ENV
- is removed afterwards (`docker rm -f` in a finally block); labelled containers left behind by
  a crashed worker are removed by `cleanup_orphans`
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from swe_agent.config import SandboxSettings
from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.pathjail import PathJail
from swe_agent.sandbox.docker import SandboxError, docker, docker_env

LABEL = "swe-agent.sandbox"
SANDBOX_ENV = {
    "HOME": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONUNBUFFERED": "1",
    "PYTHONPATH": "/workspace:/workspace/src",
    "TMPDIR": "/tmp",
}

# pytest selection items: path[::name[::name]] with conservative characters; never an option.
_SELECTION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*(::[A-Za-z0-9_\[\]\-.,=:+ ]+)?$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

IMPORT_CHECK_SCRIPT = (
    "import importlib, json, sys, traceback\n"
    "res = {}\n"
    "for m in sys.argv[1:]:\n"
    "    try:\n"
    "        importlib.import_module(m); res[m] = 'ok'\n"
    "    except BaseException:\n"
    "        res[m] = traceback.format_exc(limit=3)[-1500:]\n"
    "print('SWE_IMPORT_RESULT=' + json.dumps(res))\n"
)


class InvalidSelectionError(ValueError):
    pass


def validate_selection(items: list[str], jail: PathJail) -> list[str]:
    """Accept only pytest node ids / paths that exist inside the workspace."""
    out: list[str] = []
    for item in items:
        item = item.strip()
        if not item or item.startswith("-") or not _SELECTION_RE.match(item):
            raise InvalidSelectionError(f"invalid test selection: {item!r}")
        path = item.split("::", 1)[0]
        try:
            jail.resolve(path, must_exist=True)
        except PathPolicyError as exc:
            raise InvalidSelectionError(f"invalid test selection {item!r}: {exc}") from exc
        out.append(item)
    return out


def validate_modules(mods: list[str]) -> list[str]:
    bad = [m for m in mods if not _MODULE_RE.match(m)]
    if bad:
        raise InvalidSelectionError(f"invalid module names: {bad}")
    return mods


@dataclass
class ExecOutcome:
    container: str
    argv: list[str]
    exit_code: int | None
    timed_out: bool
    oom_killed: bool
    duration_ms: int
    output: str
    output_truncated: bool


def build_run_argv(
    s: SandboxSettings,
    *,
    name: str,
    image: str,
    workspace: Path,
    run_label: str,
    command: list[str],
) -> list[str]:
    """The complete `docker run` argv. Pure function: unit-tested for every hardening flag."""
    argv = [
        "run",
        "--name",
        name,
        "--label",
        f"{LABEL}=1",
        "--label",
        f"{LABEL}.run={run_label}",
        "--network",
        "none",
        "--user",
        s.user,
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,size={s.tmpfs_size}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(s.pids_limit),
        "--memory",
        s.memory,
        "--memory-swap",
        s.memory,
        "--cpus",
        str(s.cpus),
        "--ulimit",
        f"nofile={s.nofile}:{s.nofile}",
        "--init",
        "--volume",
        f"{workspace}:/workspace:rw",
        "--workdir",
        "/workspace",
    ]
    if s.runtime:
        argv += ["--runtime", s.runtime]
    for k, v in SANDBOX_ENV.items():
        argv += ["--env", f"{k}={v}"]
    return [*argv, image, *command]


def pytest_command(selection: list[str]) -> list[str]:
    return [
        "python",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--continue-on-collection-errors",  # one broken module must not hide the other results
        "-o",
        "junit_family=xunit1",
        "--junitxml=/workspace/.swe_out/junit.xml",
        "-q",
        "-rfE",
        "--color=no",
        "--tb=short",
        "--",
        *(selection or ["."]),
    ]


class _CappedReader:
    """Keep the head and the tail of a stream up to a byte budget (pytest summaries are at the
    end; the first error is often at the start)."""

    def __init__(self, cap: int) -> None:
        self.head_cap = cap // 2
        self.head = bytearray()
        self.tail: deque[bytes] = deque()
        self.tail_bytes = 0
        self.tail_cap = cap - self.head_cap
        self.dropped = 0

    def feed(self, chunk: bytes) -> None:
        room = self.head_cap - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if not chunk:
            return
        self.tail.append(chunk)
        self.tail_bytes += len(chunk)
        while self.tail_bytes > self.tail_cap and self.tail:
            first = self.tail.popleft()
            over = self.tail_bytes - self.tail_cap
            if len(first) > over:
                self.tail.appendleft(first[over:])
                self.tail_bytes -= over
                self.dropped += over
            else:
                self.tail_bytes -= len(first)
                self.dropped += len(first)

    def text(self) -> tuple[str, bool]:
        body = bytes(self.head)
        if self.dropped:
            body += f"\n[... {self.dropped} bytes of output omitted ...]\n".encode()
        body += b"".join(self.tail)
        return body.decode("utf-8", errors="replace"), self.dropped > 0


class DockerSandbox:
    def __init__(self, settings: SandboxSettings, run_label: str = "adhoc") -> None:
        self.s = settings
        self.run_label = re.sub(r"[^A-Za-z0-9_.-]", "_", run_label)[:60]

    def run(
        self, image: str, workspace: Path, command: list[str], *, timeout: float
    ) -> ExecOutcome:
        name = f"swe-sbx-{uuid.uuid4().hex[:12]}"
        argv = build_run_argv(
            self.s,
            name=name,
            image=image,
            workspace=workspace,
            run_label=self.run_label,
            command=command,
        )
        reader = _CappedReader(self.s.max_output_bytes)
        t0 = time.monotonic()
        timed_out = False
        try:
            try:
                proc = subprocess.Popen(
                    [self.s.docker_bin, *argv],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=docker_env(),
                )
            except FileNotFoundError as exc:
                raise SandboxError(f"docker CLI not found: {self.s.docker_bin}") from exc
            assert proc.stdout is not None
            stream = proc.stdout
            pump = threading.Thread(target=_pump, args=(stream, reader), daemon=True)
            pump.start()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                docker(self.s.docker_bin, ["kill", name], check=False, timeout=30)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            pump.join(timeout=10)
            duration_ms = int((time.monotonic() - t0) * 1000)
            client_rc = proc.returncode
            state = docker(
                self.s.docker_bin,
                ["inspect", "--format", "{{.State.ExitCode}} {{.State.OOMKilled}}", name],
                check=False,
            )
            if state.returncode != 0:
                # Container was never created: docker itself failed (daemon, image, flags).
                text, _ = reader.text()
                raise SandboxError(f"docker run failed ({client_rc}): {text.strip()[-800:]}")
            code_s, oom_s = state.stdout.split()
            output, truncated = reader.text()
            return ExecOutcome(
                container=name,
                argv=[self.s.docker_bin, *argv],
                exit_code=None if timed_out else int(code_s),
                timed_out=timed_out,
                oom_killed=oom_s.lower() == "true",
                duration_ms=duration_ms,
                output=output,
                output_truncated=truncated,
            )
        finally:
            docker(self.s.docker_bin, ["rm", "-f", "--volumes", name], check=False, timeout=60)

    def run_pytest(
        self, image: str, snapshot: Path, selection: list[str], *, timeout: float | None = None
    ) -> tuple[ExecOutcome, str | None]:
        outcome = self.run(
            image, snapshot, pytest_command(selection), timeout=timeout or self.s.test_timeout_s
        )
        junit_path = snapshot / ".swe_out" / "junit.xml"
        junit = None
        if junit_path.is_file() and not junit_path.is_symlink():
            junit = junit_path.read_text(encoding="utf-8", errors="replace")
        return outcome, junit

    def import_check(self, image: str, snapshot: Path, modules: list[str]) -> ExecOutcome:
        mods = validate_modules(modules)
        return self.run(
            image,
            snapshot,
            ["python", "-c", IMPORT_CHECK_SCRIPT, *mods],
            timeout=self.s.import_timeout_s,
        )

    def list_containers(self, *, run_label: str | None = None) -> list[str]:
        flt = f"label={LABEL}.run={run_label}" if run_label else f"label={LABEL}=1"
        proc = docker(self.s.docker_bin, ["ps", "-a", "-q", "--filter", flt], check=False)
        return proc.stdout.split()

    def cleanup_orphans(self) -> int:
        """Remove every container we labelled (crashed workers leave these behind)."""
        ids = self.list_containers()
        if ids:
            docker(self.s.docker_bin, ["rm", "-f", *ids], check=False, timeout=120)
        return len(ids)


def _pump(stream: object, reader: _CappedReader) -> None:
    read = stream.read1 if hasattr(stream, "read1") else stream.read  # type: ignore[attr-defined]
    while True:
        chunk = read(65536)
        if not chunk:
            break
        reader.feed(chunk)
