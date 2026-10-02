from __future__ import annotations

from pathlib import Path

import pytest

from swe_agent.core.errors import BudgetExceeded
from swe_agent.repository.pathjail import WritePolicy
from swe_agent.tools.base import ToolContext
from swe_agent.tools.executor import ToolExecutor


@pytest.fixture
def impl_ctx(workspace, index) -> ToolContext:  # type: ignore[no-untyped-def]
    return ToolContext(
        workspace=workspace,
        index=index,
        node="implement",
        write_policy=WritePolicy(allow_source=True),
    )


# --------------------------------------------------------------------------- read tools


async def test_list_files(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke("list_files", {"depth": 1}, explore_ctx)
    assert r.ok
    assert "users_service/ (4 files)" in r.output
    assert "tests/ (1 files)" in r.output and ".gitignore" in r.output
    r = await executor.invoke("list_files", {"path": "users_service", "glob": "*.py"}, explore_ctx)
    assert "users_service/api.py" in r.output


async def test_read_file_ranges(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "read_file", {"path": "users_service/api.py", "start_line": 20, "end_line": 24}, explore_ctx
    )
    assert r.ok
    assert "(lines 20-24 of" in r.output
    assert "20| " in r.output and "25| " not in r.output
    assert "more lines; call read_file with start_line=25" in r.output


async def test_read_file_caps_lines(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    explore_ctx.max_read_lines = 5
    r = await executor.invoke("read_file", {"path": "users_service/api.py"}, explore_ctx)
    assert "(lines 1-5 of" in r.output


async def test_search_code(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "search_code", {"pattern": 'payload["email"]', "fixed_string": True}, explore_ctx
    )
    assert r.ok
    assert "users_service/api.py:26: " in r.output
    r = await executor.invoke("search_code", {"pattern": "zzz_nothing_zzz"}, explore_ctx)
    assert r.ok and "no matches" in r.output


async def test_search_pattern_cannot_inject_flags(
    executor: ToolExecutor, explore_ctx: ToolContext
) -> None:
    r = await executor.invoke("search_code", {"pattern": "--files"}, explore_ctx)
    assert r.ok and "no matches" in r.output


async def test_find_symbol_and_references(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke("find_symbol", {"name": "normalize_email"}, explore_ctx)
    assert "users_service/validation.py:" in r.output and "function normalize_email" in r.output
    r = await executor.invoke("find_references", {"name": "normalize_email"}, explore_ctx)
    assert "[definition]" in r.output
    assert "(in _create_user)" in r.output


async def test_outline_file(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke("outline_file", {"path": "users_service/api.py"}, explore_ctx)
    assert r.ok
    assert "function def post_users(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]" in (
        r.output
    )
    assert "from users_service.store import UserStore" in r.output


async def test_git_tools(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke("git_log", {"path": "users_service"}, explore_ctx)
    assert r.ok and "initial" in r.output
    r = await executor.invoke(
        "git_show", {"rev": "HEAD", "path": "users_service/api.py"}, explore_ctx
    )
    assert r.ok and "+def post_users" in r.output
    r = await executor.invoke("git_show", {"rev": "--output=/tmp/pwn"}, explore_ctx)
    assert not r.ok and r.error and r.error.type == "tool_error"


# --------------------------------------------------------------------------- executor behaviour


async def test_structured_errors_not_exceptions(
    executor: ToolExecutor, explore_ctx: ToolContext
) -> None:
    r = await executor.invoke("read_file", {"path": "../../etc/passwd"}, explore_ctx)
    assert not r.ok and r.error and r.error.type == "policy_violation"
    r = await executor.invoke("read_file", {"path": "x.py", "bogus": 1}, explore_ctx)
    assert r.error and r.error.type == "invalid_arguments" and "bogus" in r.error.message
    r = await executor.invoke("read_file", {"start_line": 1}, explore_ctx)
    assert r.error and r.error.type == "invalid_arguments"
    r = await executor.invoke("no_such_tool", {}, explore_ctx)
    assert r.error and r.error.type == "not_allowed"


async def test_node_permissions(executor: ToolExecutor, explore_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file", {"path": "users_service/api.py", "search": "a", "replace": "b"}, explore_ctx
    )
    assert r.error and r.error.type == "not_allowed"


async def test_truncation_and_repeats(
    executor: ToolExecutor, explore_ctx: ToolContext, recorder
) -> None:  # type: ignore[no-untyped-def]
    executor.max_output_chars = 100
    r1 = await executor.invoke("read_file", {"path": "users_service/api.py"}, explore_ctx)
    assert r1.truncated and r1.output.endswith("[... output truncated]")
    r2 = await executor.invoke("read_file", {"path": "users_service/api.py"}, explore_ctx)
    assert r2.repeated and not r1.repeated
    events = [e for e in recorder.events if e.event == "tool_call"]
    assert events[-1].data["repeated"] is True
    assert events[0].data["output_artifact"]


async def test_budget_enforced(executor: ToolExecutor, explore_ctx: ToolContext, meter) -> None:  # type: ignore[no-untyped-def]
    meter.budget.max_tool_calls = 2
    await executor.invoke("list_files", {}, explore_ctx)
    await executor.invoke("list_files", {"depth": 3}, explore_ctx)
    with pytest.raises(BudgetExceeded):
        await executor.invoke("list_files", {"depth": 4}, explore_ctx)


# --------------------------------------------------------------------------- edit tools


async def test_edit_file_success(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/api.py",
            "search": '    email = normalize_email(payload["email"])',
            "replace": '    email = normalize_email(payload.get("email") or "")',
        },
        impl_ctx,
    )
    assert r.ok, r.output
    assert '+    email = normalize_email(payload.get("email") or "")' in r.output
    assert impl_ctx.edits[-1].path == "users_service/api.py"
    text = (impl_ctx.workspace.root / "users_service/api.py").read_text()
    assert 'payload.get("email")' in text


async def test_edit_file_no_match_gives_hint(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/api.py",
            "search": 'email = normalize_email(payload["mail"])',
            "replace": "x",
        },
        impl_ctx,
    )
    assert not r.ok and r.error
    assert "not found" in r.error.message and "Closest match near line" in r.error.message


async def test_edit_file_ambiguous(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file",
        {"path": "users_service/api.py", "search": "return", "replace": "yield"},
        impl_ctx,
    )
    assert r.error and "matches" in r.error.message and "times" in r.error.message


async def test_edit_file_trailing_whitespace_fallback(
    executor: ToolExecutor, impl_ctx: ToolContext
) -> None:
    r = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/store.py",
            "search": "    next_id: int = 1   \n",
            "replace": "    next_id: int = 100\n",
        },
        impl_ctx,
    )
    assert r.ok, r.output
    assert "ignoring trailing whitespace" in r.output
    assert (
        "next_id: int = 100\n" in (impl_ctx.workspace.root / "users_service/store.py").read_text()
    )


async def test_edit_protected_files(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file",
        {"path": "tests/test_api.py", "search": "assert status == 422", "replace": "assert True"},
        impl_ctx,
    )
    assert r.error and r.error.type == "policy_violation"
    r = await executor.invoke(
        "edit_file", {"path": "pyproject.toml", "search": "users", "replace": "x"}, impl_ctx
    )
    assert r.error and r.error.type == "policy_violation"


async def test_edit_reports_syntax_warning(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/store.py",
            "search": "    def get(self, user_id: int)",
            "replace": "    def get(self, user_id: int",
        },
        impl_ctx,
    )
    assert r.ok and "WARNING: file now has a syntax error" in r.output


async def test_create_file(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "create_file", {"path": "users_service/errors.py", "content": "X = 1\n"}, impl_ctx
    )
    assert r.ok
    r = await executor.invoke(
        "create_file", {"path": "users_service/errors.py", "content": "X = 2\n"}, impl_ctx
    )
    assert r.error and "already exists" in r.error.message
    r = await executor.invoke(
        "create_file", {"path": "tests/test_new.py", "content": "def test(): pass\n"}, impl_ctx
    )
    assert r.error and r.error.type == "policy_violation"
    r = await executor.invoke("create_file", {"path": "../evil.py", "content": "x"}, impl_ctx)
    assert r.error and r.error.type == "policy_violation"
    assert not (Path(impl_ctx.workspace.root).parent / "evil.py").exists()


# --------------------------------------------------------------------------- error recovery
# Regression tests for the live qwen2.5-coder:7b run: implement called
# edit_file(search="def create_user(request):") (text that exists nowhere) ~60 times unchanged.

HALLUCINATED = {
    "path": "users_service/api.py",
    "search": "def create_user(request):\n    # Existing code continues here",
    "replace": "def create_user(request):\n    pass",
}


async def test_nonexistent_search_returns_file_text(
    executor: ToolExecutor, impl_ctx: ToolContext
) -> None:
    api = (impl_ctx.workspace.root / "users_service/api.py").read_text()
    r = await executor.invoke("edit_file", HALLUCINATED, impl_ctx)
    assert not r.ok and r.error and r.error.type == "tool_error"
    msg = r.error.message
    assert "Nothing in users_service/api.py resembles the first line of `search`" in msg
    assert "copied verbatim from the file as it is now" in msg
    # the real text, unnumbered, so the next `search` can be copied from it verbatim
    body = msg.split("----- begin users_service/api.py -----\n", 1)[1].split("\n----- end", 1)[0]
    assert body == api.rstrip()
    assert '    email = normalize_email(payload["email"])' in body
    assert (impl_ctx.workspace.root / "users_service/api.py").read_text() == api  # untouched


async def test_nonexistent_search_in_large_file_returns_outline(
    executor: ToolExecutor, impl_ctx: ToolContext
) -> None:
    big = "".join(f"def func_{i}(x):\n    return x + {i}\n\n\n" for i in range(40))
    (impl_ctx.workspace.root / "users_service/big.py").write_text(big)
    impl_ctx.index.update_file(impl_ctx.workspace.root, "users_service/big.py")
    r = await executor.invoke(
        "edit_file",
        {"path": "users_service/big.py", "search": "class Nope:\n    pass", "replace": "x"},
        impl_ctx,
    )
    msg = r.error.message if r.error else ""
    assert "has 160 lines; its definitions:" in msg
    assert "L1-2 function func_0" in msg and "Call read_file with start_line/end_line" in msg
    assert "return x + 39" not in msg  # large files are never dumped
    assert len(msg) < 3500


async def test_near_miss_hint_unchanged(executor: ToolExecutor, impl_ctx: ToolContext) -> None:
    r = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/api.py",
            "search": "    email = normalize_email(payload['email'])",
            "replace": "x",
        },
        impl_ctx,
    )
    msg = r.error.message if r.error else ""
    assert "Closest match near line 26" in msg and "26|" in msg
    assert "----- begin" not in msg and "REPEATED" not in r.output


async def test_repeated_failing_call_is_flagged(
    executor: ToolExecutor, impl_ctx: ToolContext, recorder
) -> None:  # type: ignore[no-untyped-def]
    first = await executor.invoke("edit_file", HALLUCINATED, impl_ctx)
    second = await executor.invoke("edit_file", HALLUCINATED, impl_ctx)
    assert not first.ok and not second.ok
    assert "REPEATED FAILED CALL" not in first.output
    assert second.output.startswith("REPEATED FAILED CALL: this exact call")
    assert "read_file the target file and copy `search` verbatim" in second.output
    events = [e.data for e in recorder.events if e.event == "tool_call"]
    assert [e["repeated_failure"] for e in events] == [False, True]
    # a different failing call is not flagged as a repeat
    other = await executor.invoke("edit_file", {**HALLUCINATED, "replace": "y"}, impl_ctx)
    assert "REPEATED FAILED CALL" not in other.output


async def test_repeat_memory_resets_after_successful_write(
    executor: ToolExecutor, impl_ctx: ToolContext
) -> None:
    await executor.invoke("edit_file", HALLUCINATED, impl_ctx)
    ok = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/store.py",
            "search": "    next_id: int = 1\n",
            "replace": "    next_id: int = 2\n",
        },
        impl_ctx,
    )
    assert ok.ok
    again = await executor.invoke("edit_file", HALLUCINATED, impl_ctx)
    assert not again.ok and "REPEATED FAILED CALL" not in again.output


async def test_successful_edit_output_unchanged(
    executor: ToolExecutor, impl_ctx: ToolContext
) -> None:
    r = await executor.invoke(
        "edit_file",
        {
            "path": "users_service/store.py",
            "search": "    next_id: int = 1\n",
            "replace": "    next_id: int = 2\n",
        },
        impl_ctx,
    )
    assert r.ok and r.error is None and not r.repeated
    assert r.output.splitlines()[0] == "edited users_service/store.py"
    assert "-    next_id: int = 1" in r.output and "+    next_id: int = 2" in r.output
    assert "REPEATED" not in r.output and "Current content" not in r.output
