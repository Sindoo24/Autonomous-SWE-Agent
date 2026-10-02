from __future__ import annotations

from swe_agent.repository.summary import build_summary, extract_issue_terms
from swe_agent.repository.symbols import SymbolIndex, index_source

SRC = b'''
import os
from typing import Any

class Service:
    """doc"""

    @staticmethod
    def create(payload: dict[str, Any]) -> int:
        return 1

    def _helper(self):
        def inner():
            pass
        return inner

@decorator(arg=1)
async def handler(request, *, timeout: float = 1.0):
    return None

if True:
    def conditional():
        pass
'''


def test_index_source_extracts_symbols() -> None:
    fi = index_source("pkg/svc.py", SRC)
    by_qual = {s.qualname: s for s in fi.symbols}
    assert set(by_qual) == {
        "Service",
        "Service.create",
        "Service._helper",
        "Service._helper.inner",
        "handler",
        "conditional",
    }
    assert by_qual["Service"].kind == "class"
    assert by_qual["Service.create"].kind == "method"
    assert by_qual["Service.create"].decorators == ["@staticmethod"]
    assert by_qual["Service.create"].start_line == 8  # decorator line
    assert by_qual["Service._helper.inner"].kind == "function"
    assert by_qual["handler"].signature.startswith("async def handler(request")
    assert [i.text for i in fi.imports] == ["import os", "from typing import Any"]
    assert not fi.has_errors


def test_index_tolerates_syntax_errors() -> None:
    fi = index_source("broken.py", b"def ok():\n    return 1\n\ndef broken(:\n    pass\n")
    assert fi.has_errors
    assert any(s.name == "ok" for s in fi.symbols)


def test_symbol_index_queries(workspace, index: SymbolIndex) -> None:  # type: ignore[no-untyped-def]
    assert [s.path for s in index.find("post_users")] == ["users_service/api.py"]
    assert index.find("UserStore.add")[0].kind == "method"
    assert index.find("add", kind="class") == []
    enc = index.enclosing("users_service/api.py", 27)
    assert enc is not None and enc.name == "_create_user"


def test_extract_issue_terms() -> None:
    terms, strong = extract_issue_terms(
        "POST /users returns HTTP 500 when the `email` field is missing; see create_user()"
        " raising KeyError."
    )
    assert "users" in terms and "email" in terms and "create_user" in terms
    assert "KeyError" in strong
    assert "returns" not in terms and "when" not in terms


def test_summary_ranks_buggy_file_first(workspace, index: SymbolIndex) -> None:  # type: ignore[no-untyped-def]
    s = build_summary(
        workspace.root,
        workspace.ls_files(),
        index,
        "POST /users returns HTTP 500 when the email field is missing.",
    )
    assert s.test_framework == "pytest"
    assert "users_service" in s.packages
    assert s.candidates[0].path == "users_service/api.py"
    assert s.candidates[0].reasons
    assert "pyproject.toml" in s.manifests
