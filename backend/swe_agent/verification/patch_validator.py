"""Deterministic patch validation. Reads code and diffs, never model prose.

Checks
1. something changed
2. every changed path passes the path jail and the write policy (defence in depth: the edit tools
   already enforce this, but a bug there must not let a bad patch through)
3. every changed Python file parses (`ast`)
4. total changed lines under a cap (rejects rewrites of whole files)
5. no test-tampering constructs added (skip/xfail markers, early exits)
6. plan deviation: files outside the plan and patch size relative to the plan
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.pathjail import WritePolicy
from swe_agent.repository.workspace import Workspace
from swe_agent.schemas.state import DeviationSeverity, PatchValidation, Plan, PlanDeviation

TAMPER_PATTERNS = [
    (re.compile(r"pytest\.(mark\.)?(skip|xfail|skipif)\b"), "adds a pytest skip/xfail"),
    (re.compile(r"unittest\.(skip|expectedFailure)|@skip\w*\("), "adds a unittest skip"),
    (re.compile(r"\b(sys\.exit|os\._exit)\s*\("), "adds a process exit"),
    (
        re.compile(r"PYTEST_CURRENT_TEST|\"pytest\" in sys\.modules|'pytest' in sys\.modules"),
        "special-cases the test environment",
    ),
]

_SUSPICIOUS_PATTERNS = [
    (
        re.compile(
            r"\bsubprocess\.\w+\s*\(|\bos\.(system|popen|exec\w*)\s*\(|(?<![\w.])(eval|exec)\s*\("
        ),
        "adds process/eval call",
    ),
    (re.compile(r"\b(socket|urllib\.request|requests\.|httpx\.)"), "adds network access"),
]


@dataclass
class DiffStats:
    files: list[str]
    added: int
    removed: int
    added_lines: list[tuple[str, str]]  # (file, line text)


def parse_diff(diff: str) -> DiffStats:
    files: list[str] = []
    added_lines: list[tuple[str, str]] = []
    added = removed = 0
    current = ""
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != "/dev/null":
                current = path[2:] if path.startswith("b/") else path
                if current not in files:
                    files.append(current)
            continue
        if line.startswith("--- "):
            path = line[4:].strip()
            if path != "/dev/null":
                p = path[2:] if path.startswith("a/") else path
                current = p
                if p not in files:
                    files.append(p)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
            added_lines.append((current, line[1:]))
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return DiffStats(files=files, added=added, removed=removed, added_lines=added_lines)


_SEV_ORDER = [DeviationSeverity.NONE, DeviationSeverity.MINOR, DeviationSeverity.MAJOR]


def _max_sev(a: DeviationSeverity, b: DeviationSeverity) -> DeviationSeverity:
    return a if _SEV_ORDER.index(a) >= _SEV_ORDER.index(b) else b


def compute_deviation(plan: Plan, files_changed: list[str], changed_lines: int) -> PlanDeviation:
    planned = set(plan.files_to_modify) | {c.file for c in plan.changes}
    outside = sorted(set(files_changed) - planned)
    untouched = sorted(planned - set(files_changed))
    size_limit = max(30, 15 * len(plan.changes))
    reasons: list[str] = []
    severity = DeviationSeverity.NONE
    if outside:
        reasons.append(f"changed files not in plan: {outside}")
        severity = DeviationSeverity.MINOR if len(outside) == 1 else DeviationSeverity.MAJOR
    if changed_lines > size_limit:
        reasons.append(f"patch changes {changed_lines} lines; plan implies <= {size_limit}")
        bump = (
            DeviationSeverity.MAJOR if changed_lines > 3 * size_limit else (DeviationSeverity.MINOR)
        )
        severity = _max_sev(severity, bump)
    if untouched:
        reasons.append(f"planned files not changed: {untouched}")
        if severity is DeviationSeverity.NONE:
            severity = DeviationSeverity.MINOR
    return PlanDeviation(
        severity=severity,
        files_outside_plan=outside,
        planned_files_untouched=untouched,
        reasons=reasons,
    )


def validate_patch(
    ws: Workspace,
    diff: str,
    plan: Plan,
    policy: WritePolicy,
    *,
    max_changed_lines: int,
    repro_path: str | None = None,
) -> tuple[PatchValidation, PlanDeviation, DiffStats]:
    """`repro_path`: the agent-written reproduction test. It is part of the patch but is not
    the fix: it does not count as "a change", and does not count against the plan."""
    stats = parse_diff(diff)
    errors: list[str] = []
    warnings: list[str] = []
    fix_files = [f for f in stats.files if f != repro_path]

    if not fix_files:
        errors.append("no changes were made")

    for rel in stats.files:
        try:
            p = ws.jail.resolve(rel)
        except PathPolicyError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        exists = p.exists()
        try:
            policy.check(rel, exists=ws.exists_at_base(rel))
        except PathPolicyError as exc:
            errors.append(f"{rel}: {exc}")
        if rel.endswith(".py") and exists:
            try:
                ast.parse(p.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError as exc:
                errors.append(f"{rel}: syntax error at line {exc.lineno}: {exc.msg}")
        if not exists:
            warnings.append(f"{rel}: file deleted")

    changed = stats.added + stats.removed
    fix_changed = changed - sum(1 for f, _ in stats.added_lines if f == repro_path)
    if fix_changed > max_changed_lines:
        errors.append(
            f"patch changes {fix_changed} lines (limit {max_changed_lines}); keep it minimal"
        )

    for rel, text in stats.added_lines:
        for pattern, label in TAMPER_PATTERNS:
            if pattern.search(text):
                errors.append(f"{rel}: {label}: {text.strip()[:120]}")
        for pattern, label in _SUSPICIOUS_PATTERNS:
            if pattern.search(text):
                warnings.append(f"{rel}: {label}: {text.strip()[:120]}")

    repro_lines = sum(1 for f, _ in stats.added_lines if f == repro_path)
    deviation = compute_deviation(plan, fix_files, changed - repro_lines)
    if deviation.severity is DeviationSeverity.MAJOR:
        errors.append("major deviation from plan: " + "; ".join(deviation.reasons))
    elif deviation.severity is DeviationSeverity.MINOR:
        warnings.append("minor deviation from plan: " + "; ".join(deviation.reasons))

    return PatchValidation(accepted=not errors, errors=errors, warnings=warnings), deviation, stats
