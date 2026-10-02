"""End-to-end graph runs (execution disabled) driven by a scripted provider.

These tests prove the orchestration, tools, validation and reporting work together. They do
NOT measure model quality: every model response is scripted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swe_agent.agents.runner import run_task
from swe_agent.config import Settings
from swe_agent.llm.scripted import ScriptedCall, ScriptedProvider, action

ISSUE = "POST /users returns HTTP 500 when the email field is missing."
API = "users_service/api.py"

FINDINGS = {
    "summary": "_create_user indexes payload['email'] directly; a missing key raises KeyError, "
    "which the _handle catch-all turns into a 500.",
    "relevant_files": [API, "users_service/validation.py"],
    "relevant_symbols": ["_create_user", "_handle", "normalize_email"],
    "evidence": [
        {"path": API, "start_line": 26, "end_line": 26, "why": "KeyError on missing email"},
        {"path": API, "start_line": 18, "end_line": 19, "why": "catch-all returns 500"},
    ],
}
HYPOTHESES = {
    "hypotheses": [
        {
            "id": "whatever",
            "statement": "Missing 'email' raises KeyError -> 500 instead of 422",
            "evidence": [{"path": API, "start_line": 26, "end_line": 26, "why": "direct indexing"}],
            "confidence": "high",
        }
    ]
}
PLAN = {
    "problem": "missing email yields 500",
    "hypothesis_id": "H1",
    "files_to_modify": [API],
    "changes": [
        {
            "file": API,
            "kind": "modify",
            "symbol": "_create_user",
            "description": "raise ValidationError('email', ...) when email is missing",
        }
    ],
    "reproduction": {
        "test_path": "tests/test_email_required.py",
        "test_name": "test_missing_email_is_422",
        "description": "POST without email returns 422 naming 'email'",
    },
    "tests_to_run": ["tests/test_api.py"],
    "risks": ["empty-string email handling"],
    "rollback_strategy": "git restore users_service/api.py",
}
FIX_SEARCH = '    email = normalize_email(payload["email"])\n'
FIX_REPLACE = (
    '    email = payload.get("email")\n'
    "    if not email:\n"
    '        raise ValidationError("email", "email is required")\n'
    "    email = normalize_email(email)\n"
)
IMPL = {
    "summary": "Validate presence of email before normalizing.",
    "root_cause": "payload['email'] raised KeyError, converted to HTTP 500.",
}


def explore_script() -> list[Any]:
    return [
        action("outline_file", path=API),
        action("read_file", path=API, start_line=13, end_line=30),
        action("finish", **FINDINGS),
    ]


def implement_script(search: str = FIX_SEARCH, replace: str = FIX_REPLACE) -> list[Any]:
    return [
        action("read_file", path=API, start_line=22, end_line=28),
        action("edit_file", path=API, search=search, replace=replace),
        action("git_diff"),
        action("finish", **IMPL),
    ]


async def test_happy_path(users_repo: Path, settings: Settings) -> None:
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, *implement_script()])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    r = out.report

    assert r["termination_reason"] == "patch_proposed"
    assert r["status"] == "NOT VERIFIED" and r["verification"]["level"] == 0
    assert r["files_changed"] == [API]
    assert r["root_cause"].startswith("payload['email']")
    assert r["hypotheses"][0]["id"] == "H1"
    assert r["iterations"] == 1 and len(r["patch_attempts"]) == 1
    assert r["patch_attempts"][0]["accepted"] is True
    assert r["tests_executed"] == []  # execution disabled: no code runs

    patch = (out.artifacts_dir / "final.patch").read_text()
    assert '+        raise ValidationError("email", "email is required")' in patch
    assert '-    email = normalize_email(payload["email"])' in patch

    # the user's repository is untouched
    assert 'payload["email"]' in (users_repo / API).read_text()

    # trajectory: node order, tool calls, model calls with roles
    events = [
        json.loads(line)
        for line in (out.artifacts_dir / "trajectory.jsonl").read_text().splitlines()
    ]
    nodes = [e["node"] for e in events if e["event"] == "node_started"]
    assert nodes == [
        "intake",
        "prepare_repo",
        "explore",
        "hypothesize",
        "plan",
        "implement",
        "validate_patch",
        "human_approval",
        "finalize",
    ]
    tools = [
        e["data"]["tool"]
        for e in events
        if e["event"] == "tool_call" and not e["data"].get("system")
    ]
    assert tools == ["outline_file", "read_file", "read_file", "edit_file", "git_diff"]
    roles = [e["data"]["role"] for e in events if e["event"] == "model_call"]
    assert roles == ["reasoning"] * 5 + ["coder"] * 4
    assert r["trace"]["tool_calls"] == 5 and r["trace"]["model_calls"] == 9
    assert r["trace"]["tokens_in"] is None  # scripted provider reports no usage
    assert all(e["task_id"] == out.task_id for e in events)

    # the explorer was seeded with the deterministic candidate ranking
    explore_prompt = provider_calls_text(provider, 0)
    assert "candidate files" in explore_prompt and API in explore_prompt
    # models used per role come from settings
    assert {c.model for c in provider.calls} == {"scripted-reasoner", "scripted-coder"}


def provider_calls_text(p: ScriptedProvider, i: int) -> str:
    return "\n".join(m.content for m in p.calls[i].messages)


async def test_validator_rejection_loops_back_to_implement(
    users_repo: Path, settings: Settings
) -> None:
    broken = implement_script(replace="    email = normalize_email(payload.get('email')\n")
    fixed = [
        # second attempt receives validator feedback and fixes the syntax error
        lambda call: (
            _assert_feedback(call)
            or action(
                "edit_file",
                path=API,
                search="    email = normalize_email(payload.get('email')\n",
                replace=FIX_REPLACE,
            )
        ),
        action("finish", **IMPL),
    ]
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, *broken, *fixed])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "patch_proposed"
    attempts = r["patch_attempts"]
    assert [a["accepted"] for a in attempts] == [False, True]
    assert any("syntax error" in e for e in attempts[0]["errors"])
    assert r["iterations"] == 1  # validator retries are not new iterations


def _assert_feedback(call: ScriptedCall) -> None:
    task = call.messages[1].content
    assert "REJECTED by the validator" in task and "syntax error" in task


async def test_validator_attempts_exhausted(users_repo: Path, settings: Settings) -> None:
    settings.budget.max_validation_attempts = 2
    no_edit = [action("finish", **IMPL)]
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, PLAN, *no_edit, *no_edit])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    assert out.report["termination_reason"] == "no_changes"
    assert out.report["status"] == "NOT VERIFIED"
    assert not (out.artifacts_dir / "final.patch").exists()


async def test_budget_exhaustion_produces_failure_report(
    users_repo: Path, settings: Settings
) -> None:
    settings.budget.max_tool_calls = 1
    provider = ScriptedProvider(explore_script())
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "budget_exhausted"
    assert r["errors"][0]["node"] == "explore" and "tool_calls" in r["errors"][0]["message"]


async def test_model_garbage_produces_failure_report(users_repo: Path, settings: Settings) -> None:
    provider = ScriptedProvider(["I think the bug is somewhere", "still not json"])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    assert out.report["termination_reason"] == "model_error"
    assert out.report["errors"][0]["kind"] == "model_error"


async def test_hallucinated_evidence_is_rejected_then_corrected(
    users_repo: Path, settings: Settings
) -> None:
    bad = {
        **FINDINGS,
        "evidence": [
            {"path": "users_service/ghost.py", "start_line": 1, "end_line": 2, "why": "made up"}
        ],
    }
    provider = ScriptedProvider(
        [
            action("finish", **bad),
            lambda call: _assert_contains(call, "ghost.py") or action("finish", **FINDINGS),
            HYPOTHESES,
            PLAN,
            *implement_script(),
        ]
    )
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    assert out.report["termination_reason"] == "patch_proposed"


def _assert_contains(call: ScriptedCall, needle: str) -> None:
    last = call.messages[-1].content
    assert "finish rejected" in last and needle in last


async def test_plan_must_not_target_tests(users_repo: Path, settings: Settings) -> None:
    bad_plan = {
        **PLAN,
        "files_to_modify": ["tests/test_api.py"],
        "changes": [{"file": "tests/test_api.py", "description": "relax assertion"}],
    }
    provider = ScriptedProvider([*explore_script(), HYPOTHESES, bad_plan, bad_plan])
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    assert out.report["termination_reason"] == "model_error"
    assert "read-only" in out.report["errors"][0]["message"]


async def test_empty_issue_rejected(users_repo: Path, settings: Settings) -> None:
    out = await run_task(str(users_repo), "  \x00 ", settings, provider=ScriptedProvider([]))
    assert out.report["termination_reason"] == "invalid_input"


async def test_step_limit_forces_finish(users_repo: Path, settings: Settings) -> None:
    settings.budget.explore_max_steps = 2

    def forced(call: ScriptedCall) -> dict[str, Any]:
        assert call.json_schema is not None
        assert call.json_schema["properties"]["tool"]["enum"] == ["finish"]
        last = call.messages[-1]
        assert last.role == "user" and "Tool budget for this stage is used up" in last.content
        assert call.messages[-2].role == "assistant"  # alternation preserved
        return action("finish", **FINDINGS)

    provider = ScriptedProvider(
        [
            action("outline_file", path=API),
            action("read_file", path=API, start_line=13, end_line=30),
            forced,
            HYPOTHESES,
            PLAN,
            *implement_script(),
        ]
    )
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    assert out.report["termination_reason"] == "patch_proposed"


async def test_recovers_from_hallucinated_edit_like_live_run(
    users_repo: Path, settings: Settings
) -> None:
    """Replays the live qwen2.5-coder:7b failure: an edit whose `search` exists nowhere, repeated
    verbatim. The model must be shown the real file and told the repeat already failed; a model
    that then copies from the shown text succeeds."""
    bad = action(
        "edit_file",
        path=API,
        search="def create_user(request):",
        replace="def create_user(request):\n    # Existing code continues here",
    )

    def after_first(call: ScriptedCall) -> dict[str, Any]:
        last = call.messages[-1].content
        assert "Nothing in users_service/api.py resembles" in last
        assert 'email = normalize_email(payload["email"])' in last  # real text is in context
        return bad  # the model repeats itself, as in the live run

    def after_repeat(call: ScriptedCall) -> dict[str, Any]:
        last = call.messages[-1].content
        assert "REPEATED FAILED CALL" in last
        return action("edit_file", path=API, search=FIX_SEARCH, replace=FIX_REPLACE)

    provider = ScriptedProvider([
        *explore_script(), HYPOTHESES, PLAN,
        bad, after_first, after_repeat, action("finish", **IMPL),
    ])  # fmt: skip
    out = await run_task(str(users_repo), ISSUE, settings, provider=provider)
    r = out.report
    assert r["termination_reason"] == "patch_proposed"
    assert r["trace"]["tool_errors"] == 2
    patch = (out.artifacts_dir / "final.patch").read_text()
    assert '+        raise ValidationError("email", "email is required")' in patch
    events = [
        json.loads(x) for x in (out.artifacts_dir / "trajectory.jsonl").read_text().splitlines()
    ]
    flags = [
        e["data"].get("repeated_failure")
        for e in events
        if e["event"] == "tool_call" and e["data"].get("tool") == "edit_file"
    ]
    assert flags == [False, True, False]
