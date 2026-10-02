"""High-level sandbox operations used by graph nodes: build the image, run tests, check imports.

Returns typed `TestRun` records, stores full logs / JUnit XML as artifacts and writes a
`sandbox_exec` trajectory event per execution.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from swe_agent.config import SandboxSettings
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import TrajectoryRecorder
from swe_agent.repository.workspace import Workspace
from swe_agent.sandbox.deps import DependencySpec, discover_dependencies
from swe_agent.sandbox.docker import docker
from swe_agent.sandbox.image import ImageBuilder
from swe_agent.sandbox.junit import JUnitResult, parse_junit
from swe_agent.sandbox.runner import DockerSandbox, ExecOutcome, validate_selection
from swe_agent.sandbox.snapshot import make_snapshot, remove_snapshot
from swe_agent.schemas.state import TestCaseFailure, TestRun, TestRunKind, TestRunStatus

OUTPUT_TAIL_CHARS = 4000
# Syntax errors, undefined names, unused imports/variables, redefinitions: bugs, not style.
_RUFF_CMD = [
    "python", "-m", "ruff", "check", "--isolated", "--no-cache", "--exit-zero",
    "--output-format", "json", "--select", "E9,F",
]  # fmt: skip
_COLLECT_ERR_RE = re.compile(r"^ERROR (?:collecting )?(\S+\.py)", re.MULTILINE)


def status_from(outcome: ExecOutcome, junit: JUnitResult | None) -> TestRunStatus:
    """Map a container outcome + parsed JUnit to a run status. Pure; unit-tested."""
    if outcome.timed_out:
        return TestRunStatus.TIMEOUT
    if outcome.oom_killed:
        return TestRunStatus.RESOURCE_LIMIT
    code = outcome.exit_code
    if code == 0:
        return TestRunStatus.PASSED
    if code == 1:
        return TestRunStatus.FAILED
    if code == 2:
        # "interrupted": in practice collection errors (import/syntax errors in test modules)
        return TestRunStatus.COLLECTION_ERROR
    if code == 4:
        return TestRunStatus.USAGE_ERROR
    if code == 5:
        return TestRunStatus.NO_TESTS
    if code == 137:  # SIGKILL without OOM flag: pid limit / external kill
        return TestRunStatus.RESOURCE_LIMIT
    if junit is not None and (junit.failed or junit.errors):
        return TestRunStatus.FAILED
    return TestRunStatus.INFRA_ERROR


class SandboxService:
    def __init__(
        self,
        settings: SandboxSettings,
        artifacts: ArtifactStore,
        recorder: TrajectoryRecorder,
        *,
        scratch_root: Path,
        run_label: str,
    ) -> None:
        self.s = settings
        self.artifacts = artifacts
        self.recorder = recorder
        self.scratch_root = scratch_root
        self.sandbox = DockerSandbox(settings, run_label=run_label)
        self.builder = ImageBuilder(settings)

    # ------------------------------------------------------------------ image

    def prepare_image(self, ws: Workspace) -> tuple[str, DependencySpec]:
        spec = discover_dependencies(ws)
        built = self.builder.ensure_deps(spec.requirements)
        self.recorder.record(
            "sandbox_exec",
            op="build_image",
            image=built.tag,
            cached=built.cached,
            requirements=built.requirements,
            unsupported=spec.unsupported,
            status="ok",
        )
        return built.tag, spec

    # ------------------------------------------------------------------ tests

    def run_tests(
        self,
        ws: Workspace,
        image: str,
        kind: TestRunKind,
        *,
        selection: list[str] | None = None,
        extra_files: dict[str, Path] | None = None,
        timeout: float | None = None,
    ) -> TestRun:
        run_id = f"tr_{uuid.uuid4().hex[:10]}"
        sel = validate_selection(selection or [], ws.jail) if selection else []
        snap, _ = make_snapshot(ws, self.scratch_root / run_id, extra_files)
        if extra_files:  # held-out tests are selected by path inside the snapshot
            sel = sel or sorted({str(Path(p).parent) for p in extra_files})
        try:
            outcome, junit_xml = self.sandbox.run_pytest(image, snap, sel, timeout=timeout)
        finally:
            remove_snapshot(snap)
        parsed: JUnitResult | None = None
        junit_ref = None
        if junit_xml:
            junit_ref = self.artifacts.put_text("junit", junit_xml, suffix=".xml").id
            try:
                parsed = parse_junit(junit_xml)
            except (ValueError, SyntaxError):
                parsed = None
        status = status_from(outcome, parsed)
        log_ref = self.artifacts.put_text("test_log", outcome.output).id
        collection = list(parsed.collection_errors) if parsed else []
        if status is TestRunStatus.COLLECTION_ERROR and not collection:
            collection = sorted(set(_COLLECT_ERR_RE.findall(outcome.output)))
        failures = list(parsed.failures) if parsed else []
        if not failures and status not in (TestRunStatus.PASSED, TestRunStatus.NO_TESTS):
            failures = [
                TestCaseFailure(
                    nodeid="(run)",
                    kind="error",
                    message=status.value,
                    excerpt=outcome.output[-1500:],
                )
            ]
        run = TestRun(
            id=run_id,
            kind=kind,
            status=status,
            command=outcome.argv[outcome.argv.index(image) + 1 :],
            exit_code=outcome.exit_code,
            passed=parsed.passed if parsed else [],
            failed=parsed.failed if parsed else [],
            errors=parsed.errors if parsed else [],
            skipped=parsed.skipped if parsed else 0,
            collection_errors=collection,
            failures=failures,
            duration_ms=outcome.duration_ms,
            timed_out=outcome.timed_out,
            oom_killed=outcome.oom_killed,
            output_tail=outcome.output[-OUTPUT_TAIL_CHARS:],
            log_artifact_id=log_ref,
            junit_artifact_id=junit_ref,
        )
        self._record(run, outcome)
        return run

    def import_check(self, ws: Workspace, image: str, modules: list[str]) -> TestRun:
        run_id = f"tr_{uuid.uuid4().hex[:10]}"
        snap, _ = make_snapshot(ws, self.scratch_root / run_id)
        try:
            outcome = self.sandbox.import_check(image, snap, modules)
        finally:
            remove_snapshot(snap)
        results: dict[str, str] = {}
        for line in outcome.output.splitlines():
            if line.startswith("SWE_IMPORT_RESULT="):
                try:
                    results = json.loads(line.split("=", 1)[1])
                except json.JSONDecodeError:
                    results = {}
        ok = [m for m, r in results.items() if r == "ok"]
        bad = [m for m, r in results.items() if r != "ok"]
        if outcome.timed_out:
            status = TestRunStatus.TIMEOUT
        elif outcome.oom_killed:
            status = TestRunStatus.RESOURCE_LIMIT
        elif not results:
            status = TestRunStatus.INFRA_ERROR
        else:
            status = TestRunStatus.PASSED if not bad else TestRunStatus.FAILED
        run = TestRun(
            id=run_id,
            kind=TestRunKind.IMPORT_CHECK,
            status=status,
            command=["python", "-c", "<import check>", *modules],
            exit_code=outcome.exit_code,
            passed=ok,
            errors=bad,
            failures=[
                TestCaseFailure(
                    nodeid=m, kind="error", message="import failed", excerpt=results[m][-1500:]
                )
                for m in bad
            ],
            duration_ms=outcome.duration_ms,
            timed_out=outcome.timed_out,
            oom_killed=outcome.oom_killed,
            output_tail=outcome.output[-OUTPUT_TAIL_CHARS:],
            log_artifact_id=self.artifacts.put_text("import_log", outcome.output).id,
        )
        self._record(run, outcome)
        return run

    # ------------------------------------------------------------------ lint (L4)

    def lint(self, ws: Workspace, image: str, files: list[str]) -> tuple[TestRun, list[str]]:
        """ruff (pyflakes + syntax rules) on `files`, before vs after the patch. Returns the run
        and the violations the patch *introduced* (pre-existing ones are ignored)."""
        files = validate_selection(sorted(set(files)), ws.jail) if files else []
        run_id = f"tr_{uuid.uuid4().hex[:10]}"
        if not files:
            return TestRun(id=run_id, kind=TestRunKind.LINT, status=TestRunStatus.PASSED,
                           command=[], exit_code=0, duration_ms=0), []  # fmt: skip
        after, out_after, dur_a = self._ruff(ws, image, files, base=False)
        at_base = [f for f in files if ws.exists_at_base(f)]
        before, out_before, dur_b = (
            self._ruff(ws, image, at_base, base=True) if at_base else ([], "", 0)
        )
        ok = after is not None and before is not None
        new: list[str] = []
        if ok:
            remaining = list(before or [])
            for v in after or []:
                if v in remaining:
                    remaining.remove(v)
                else:
                    new.append(v)
        status = (
            TestRunStatus.INFRA_ERROR if not ok
            else TestRunStatus.PASSED if not new else TestRunStatus.FAILED
        )  # fmt: skip
        run = TestRun(
            id=run_id, kind=TestRunKind.LINT, status=status, command=_RUFF_CMD + files,
            exit_code=0 if ok else None, passed=[] if new else files, errors=new,
            duration_ms=dur_a + dur_b,
            output_tail=(out_after if ok else f"{out_after}\n{out_before}")[-OUTPUT_TAIL_CHARS:],
            log_artifact_id=self.artifacts.put_text("lint_log", out_after + "\n" + out_before).id,
        )  # fmt: skip
        self.recorder.record("sandbox_exec", op="lint", test_run=run_id, status=status.value,
                             new_violations=len(new), duration_ms=run.duration_ms)  # fmt: skip
        return run, new

    def _ruff(
        self, ws: Workspace, image: str, files: list[str], *, base: bool
    ) -> tuple[list[str] | None, str, int]:
        run_id = f"lint_{uuid.uuid4().hex[:8]}"
        extra: dict[str, Path] = {}
        tmp = self.scratch_root / f"{run_id}_base"
        if base:
            tmp.mkdir(parents=True, exist_ok=True)
            for i, rel in enumerate(files):
                src = tmp / f"{i}.py"
                src.write_text(ws.git("show", f"{ws.base_commit}:{rel}"), encoding="utf-8")
                extra[rel] = src
        snap, _ = make_snapshot(ws, self.scratch_root / run_id)
        for rel, src in extra.items():  # overwrite with the base version
            (snap / rel).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            outcome = self.sandbox.run(image, snap, [*_RUFF_CMD, "--", *files],
                                       timeout=self.s.import_timeout_s)  # fmt: skip
        finally:
            remove_snapshot(snap)
            remove_snapshot(tmp)
        text = outcome.output
        start, end = text.find("["), text.rfind("]")
        if outcome.timed_out or start == -1 or end < start:
            return None, text, outcome.duration_ms
        try:
            items = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None, text, outcome.duration_ms
        found = []
        for it in items:
            fname = str(it.get("filename", "")).removeprefix("/workspace/")
            found.append(f"{fname}: {it.get('code')} {it.get('message')}")
        return found, text, outcome.duration_ms

    def _record(self, run: TestRun, outcome: ExecOutcome) -> None:
        self.recorder.record(
            "sandbox_exec",
            op=run.kind.value,
            test_run=run.id,
            status=run.status.value,
            exit_code=run.exit_code,
            passed=len(run.passed),
            failed=len(run.failed),
            errors=len(run.errors),
            collection_errors=len(run.collection_errors),
            duration_ms=run.duration_ms,
            timed_out=run.timed_out,
            oom_killed=run.oom_killed,
            container=outcome.container,
            log_artifact=run.log_artifact_id,
        )

    def cleanup(self) -> int:
        """Remove any container left from this run (normally none: each run removes its own)."""
        ids = self.sandbox.list_containers(run_label=self.sandbox.run_label)
        for cid in ids:
            docker(self.s.docker_bin, ["rm", "-f", cid], check=False)
        return len(ids)
