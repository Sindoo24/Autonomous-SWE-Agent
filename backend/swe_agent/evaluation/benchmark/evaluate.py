"""Benchmark evaluation: held-out tests in the sandbox.

`evaluate_patch` applies a candidate patch to a *fresh* materialisation of the task (never the
agent's workspace), then runs the held-out tests and the visible suite in the sandbox.
Success (L5) = patch applies, every held-out test passes, and no visible test that passed on
the buggy base fails afterwards. `validate_task` checks the benchmark itself with the reference
fix (reverse of bug.patch).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import BaseModel

from swe_agent.config import SandboxSettings
from swe_agent.evaluation.benchmark.tasks import BenchmarkTask, apply_patch, materialize
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import TrajectoryRecorder
from swe_agent.repository.workspace import Workspace, prepare_workspace
from swe_agent.sandbox.service import SandboxService
from swe_agent.schemas.state import TestRun, TestRunKind


class EvalResult(BaseModel):
    task_id: str
    patch_applied: bool
    heldout_total: int
    heldout_passed: int
    heldout_failed: list[str]
    visible_regressions: list[str]
    visible_failing_after: list[str]
    success: bool
    error: str | None = None


class TaskValidation(BaseModel):
    task_id: str
    visible_failing_at_base: list[str]
    visible_failing_matches_spec: bool
    heldout_failing_at_base: list[str]
    heldout_total: int
    reference_heldout_passed: int
    reference_visible_failing: list[str]
    valid: bool
    problems: list[str]


def effective_total(static_count: int, run: TestRun) -> int:
    """Held-out test count: test functions found statically, or the collected test cases when
    parametrisation makes that larger (a parametrised test is several cases)."""
    collected = len(set(run.passed) | run.not_passing | set(run.collection_errors))
    return max(static_count, collected)


def _service(settings: SandboxSettings, work: Path, label: str) -> SandboxService:
    return SandboxService(
        settings,
        ArtifactStore(work / "artifacts"),
        TrajectoryRecorder(label, label, work / "artifacts" / "eval.jsonl"),
        scratch_root=work / "scratch",
        run_label=label,
    )


def _prepare(task: BenchmarkTask, work: Path, root: Path | None) -> Workspace:
    repo = materialize(task, work / "repo", root)
    return prepare_workspace(str(repo), work / "ws", task_id=f"eval-{task.id}")


def _runs(
    svc: SandboxService, ws: Workspace, image: str, task: BenchmarkTask
) -> tuple[TestRun, TestRun]:
    visible = svc.run_tests(ws, image, TestRunKind.TARGET)
    heldout = svc.run_tests(ws, image, TestRunKind.HELDOUT, extra_files=task.heldout_files())
    return visible, heldout


def evaluate_patch(
    task: BenchmarkTask,
    patch_text: str | None,
    settings: SandboxSettings,
    root: Path | None = None,
) -> EvalResult:
    total = task.heldout_test_count()
    with tempfile.TemporaryDirectory(prefix=f"swe-eval-{task.id}-") as tmp:
        work = Path(tmp)
        ws = _prepare(task, work, root)
        svc = _service(settings, work, f"eval-{task.id}")
        image, _ = svc.prepare_image(ws)
        base_visible = svc.run_tests(ws, image, TestRunKind.BASELINE)
        if not patch_text or not patch_text.strip():
            return EvalResult(
                task_id=task.id,
                patch_applied=False,
                heldout_total=total,
                heldout_passed=0,
                heldout_failed=[],
                visible_regressions=[],
                visible_failing_after=[],
                success=False,
                error="no patch",
            )
        try:
            apply_patch(ws.root, patch_text)
        except Exception as exc:
            return EvalResult(
                task_id=task.id,
                patch_applied=False,
                heldout_total=total,
                heldout_passed=0,
                heldout_failed=[],
                visible_regressions=[],
                visible_failing_after=[],
                success=False,
                error=f"patch does not apply: {exc}",
            )
        visible, heldout = _runs(svc, ws, image, task)
        regressions = sorted(t for t in base_visible.passed if t not in visible.passed)
        failed = sorted(heldout.not_passing | set(heldout.collection_errors))
        passed = len(heldout.passed)
        total = effective_total(total, heldout)
        success = passed == total and not failed and not regressions
        return EvalResult(
            task_id=task.id,
            patch_applied=True,
            heldout_total=total,
            heldout_passed=passed,
            heldout_failed=failed,
            visible_regressions=regressions,
            visible_failing_after=sorted(visible.not_passing),
            success=success,
        )


def validate_task(
    task: BenchmarkTask, settings: SandboxSettings, root: Path | None = None
) -> TaskValidation:
    problems: list[str] = []
    total = task.heldout_test_count()
    with tempfile.TemporaryDirectory(prefix=f"swe-validate-{task.id}-") as tmp:
        work = Path(tmp)
        ws = _prepare(task, work, root)
        svc = _service(settings, work, f"validate-{task.id}")
        image, _ = svc.prepare_image(ws)
        base_visible, base_heldout = _runs(svc, ws, image, task)
        vis_fail = sorted(base_visible.not_passing | set(base_visible.collection_errors))
        held_fail = sorted(base_heldout.not_passing | set(base_heldout.collection_errors))
        if vis_fail != sorted(task.visible_failing):
            problems.append(f"visible failing at base {vis_fail} != spec {task.visible_failing}")
        if not held_fail:
            problems.append("no held-out test fails on the buggy base")
        apply_patch(ws.root, task.bug_patch.read_text(), reverse=True)
        ref_visible, ref_heldout = _runs(svc, ws, image, task)
        ref_vis_fail = sorted(ref_visible.not_passing | set(ref_visible.collection_errors))
        total = effective_total(total, ref_heldout)
        if len(ref_heldout.passed) != total or ref_heldout.not_passing:
            problems.append(f"reference fix: held-out {len(ref_heldout.passed)}/{total} pass")
        if ref_vis_fail:
            problems.append(f"reference fix: visible tests failing {ref_vis_fail}")
        return TaskValidation(
            task_id=task.id,
            visible_failing_at_base=vis_fail,
            visible_failing_matches_spec=vis_fail == sorted(task.visible_failing),
            heldout_failing_at_base=held_fail,
            heldout_total=total,
            reference_heldout_passed=len(ref_heldout.passed),
            reference_visible_failing=ref_vis_fail,
            valid=not problems,
            problems=problems,
        )
