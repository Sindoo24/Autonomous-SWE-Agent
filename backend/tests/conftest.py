"""Shared fixtures and marker handling.

Layout: unit/ (no external services), integration/ (in-process graph, CLI over HTTP, API +
worker), e2e/ (Docker sandbox, Streamlit UI, live model), security/ (prompt injection, repo
hardening). Requirements are declared with markers, not directories:

- `docker`: skipped when no Docker daemon is reachable
- `postgres`: needs SWE_TEST_DATABASE_URL (the `pg_url` fixture skips when it is unreachable)
- `live_model`: skipped unless SWE_LIVE_MODEL=1
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from swe_agent.config import (
    AgentSettings,
    BudgetSettings,
    ModelSettings,
    SandboxSettings,
    Settings,
)
from swe_agent.core.budget import BudgetMeter
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import TrajectoryRecorder
from swe_agent.repository.symbols import SymbolIndex
from swe_agent.repository.workspace import Workspace, prepare_workspace
from swe_agent.schemas.state import Budget
from swe_agent.tools.base import ToolContext
from swe_agent.tools.executor import ToolExecutor

FIXTURES = Path(__file__).parent / "fixtures" / "repos"
# Repository root (backend/tests/conftest.py -> repo root): benchmark data, configs, frontend.
REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("SWE_BENCHMARKS_DIR", str(REPO_ROOT / "benchmarks"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip `docker`-marked tests when no Docker daemon is reachable (checked once, lazily)."""
    docker_items = [i for i in items if i.get_closest_marker("docker")]
    if not docker_items:
        return
    from swe_agent.sandbox.docker import docker_available

    ok, why = docker_available()
    if not ok:
        for item in docker_items:
            item.add_marker(pytest.mark.skip(reason=f"docker unavailable: {why}"))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()


@pytest.fixture
def users_repo(tmp_path: Path) -> Path:
    """A git repository copy of the users_service fixture (the user's 'original' repo)."""
    dest = tmp_path / "origin" / "users_service"
    shutil.copytree(FIXTURES / "users_service", dest)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "initial")
    return dest


@pytest.fixture
def workspace(users_repo: Path, tmp_path: Path) -> Workspace:
    return prepare_workspace(str(users_repo), tmp_path / "ws" / "repo", task_id="t_test")


@pytest.fixture
def index(workspace: Workspace) -> SymbolIndex:
    return SymbolIndex.build(workspace.root, workspace.ls_files())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        workspace_root=tmp_path / "workspaces",
        artifacts_root=tmp_path / "artifacts",
        model=ModelSettings(reasoning="scripted-reasoner", coder="scripted-coder"),
        budget=BudgetSettings(),
        # Graph tests run without execution; Docker tests use `sandbox_settings`.
        sandbox=SandboxSettings(enabled=False),
        # Graph tests approve automatically; approval tests switch to "manual" explicitly.
        agent=AgentSettings(approval="auto"),
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        # Hermetic: never inherit SWE_DATABASE_URL / SWE_API_KEY from the developer's shell
        # (the README has you export them). Tests that need PostgreSQL use
        # `db_settings`, which sets the URL explicitly.
        database_url=None,
        api_key=None,
    )


@pytest.fixture
def recorder(tmp_path: Path) -> TrajectoryRecorder:
    return TrajectoryRecorder("t_test", "r_test", tmp_path / "art" / "trajectory.jsonl")


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "art")


@pytest.fixture
def meter() -> BudgetMeter:
    return BudgetMeter(
        Budget(max_iterations=5, max_tool_calls=50, max_tokens=100_000, max_wall_seconds=600)
    )


@pytest.fixture
def executor(
    meter: BudgetMeter, recorder: TrajectoryRecorder, artifacts: ArtifactStore
) -> ToolExecutor:
    return ToolExecutor(meter, recorder, artifacts)


@pytest.fixture
def explore_ctx(workspace: Workspace, index: SymbolIndex) -> ToolContext:
    return ToolContext(workspace=workspace, index=index, node="explore")


# --------------------------------------------------------------------------- PostgreSQL

TEST_DB_URL = __import__("os").environ.get(
    "SWE_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/swe_agent_test"
)


def _pg_available(url: str) -> str | None:
    import psycopg

    from swe_agent.db.engine import libpq_url

    try:
        with psycopg.connect(libpq_url(url), connect_timeout=3):
            return None
    except Exception as exc:
        return str(exc).splitlines()[0][:200]


@pytest.fixture(scope="session")
def pg_url() -> str:
    """A migrated, empty test database. Skips when PostgreSQL is unreachable
    (set SWE_TEST_DATABASE_URL; the database is wiped)."""
    why = _pg_available(TEST_DB_URL)
    if why:
        pytest.skip(f"PostgreSQL unavailable ({TEST_DB_URL}): {why}")
    import psycopg

    from swe_agent.db.engine import libpq_url, upgrade

    with psycopg.connect(libpq_url(TEST_DB_URL), autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    upgrade(TEST_DB_URL)
    return TEST_DB_URL


@pytest.fixture
def db_url(pg_url: str) -> str:
    """Per test: every table emptied (ours and LangGraph's checkpoint tables)."""
    import psycopg

    from swe_agent.db.engine import libpq_url

    with psycopg.connect(libpq_url(pg_url), autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename NOT IN ('alembic_version', 'checkpoint_migrations')")]  # fmt: skip
        if names:
            conn.execute(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE")
    return pg_url


@pytest.fixture
def db_settings(settings: Settings, db_url: str, tmp_path: Path) -> Settings:
    settings.database_url = db_url
    settings.api_repo_roots = str(tmp_path)
    settings.worker_heartbeat_seconds = 0.2
    return settings
