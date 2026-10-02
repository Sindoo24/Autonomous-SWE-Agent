"""The Streamlit UI against the real API (PostgreSQL), via Streamlit's AppTest."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from frontend.api.client import ApiClient, ApiError

from swe_agent.config import Settings
from swe_agent.llm.scripted import ScriptedProvider
from swe_agent.main import create_app
from swe_agent.worker.service import Worker
from tests.conftest import REPO_ROOT
from tests.integration.test_graph_e2e import (
    HYPOTHESES,
    ISSUE,
    PLAN,
    explore_script,
    implement_script,
)

pytestmark = pytest.mark.postgres

pytest.importorskip("streamlit", reason="UI extra not installed: pip install -e '.[ui]'")

APP = str(REPO_ROOT / "frontend" / "app.py")


@pytest.fixture
def api(db_settings: Settings):  # type: ignore[no-untyped-def]
    with TestClient(create_app(db_settings)) as http:
        yield ApiClient(http=http)


async def _drain(settings: Settings, script: list) -> None:  # type: ignore[type-arg]
    worker = Worker(settings, provider_factory=lambda _m: ScriptedProvider(list(script)))
    try:
        while await worker.run_once():
            pass
    finally:
        await worker.aclose()


def _app(api: ApiClient):  # type: ignore[no-untyped-def]
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["client"] = api
    return at


def test_client_errors(api: ApiClient) -> None:
    with pytest.raises(ApiError) as e:
        api.run("r_missing")
    assert e.value.status == 404
    assert api.report("r_missing") is None  # 404 on optional resources -> None
    assert api.health() == {"status": "ok"}


async def test_ui_submit_review_approve(
    api: ApiClient, db_settings: Settings, users_repo: Path
) -> None:
    at = _app(api).run()
    assert not at.exception
    assert at.header[0].value == "New task"
    at.text_input[0].set_value(str(users_repo))
    at.text_area[0].set_value(ISSUE)
    at.button[0].click().run()  # "Start run" (form submit)
    assert not at.exception, at.exception
    runs = api.runs()
    assert len(runs) == 1 and runs[0]["status"] == "queued"
    run_id = runs[0]["id"]
    assert at.session_state["run_id"] == run_id

    await _drain(db_settings, [*explore_script(), HYPOTHESES, PLAN, *implement_script()])
    assert api.run(run_id)["status"] == "awaiting_approval"

    at = _app(api)
    at.session_state["page"] = "Run"
    at.session_state["run_id"] = run_id
    at.run()
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Review the patch" in text and "awaiting_approval" in text
    approve = next(b for b in at.button if b.label == "Approve and export")
    approve.click().run()
    assert not at.exception, at.exception
    await _drain(db_settings, [])
    assert api.run(run_id)["status"] == "finished"

    for page in ("Runs", "Run", "Experiments", "New task"):
        at = _app(api)
        at.session_state["page"] = page
        at.session_state["run_id"] = run_id
        at.run()
        assert not at.exception, (page, at.exception)
