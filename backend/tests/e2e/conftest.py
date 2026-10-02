"""Docker-backed sandbox fixtures. Tests marked `docker` are skipped when no Docker daemon is
reachable (see the root conftest).

The base image comes from SANDBOX_BASE_IMAGE (default python:3.12-slim). In environments without
Docker Hub access, build a local base image and point SANDBOX_BASE_IMAGE at it (see README).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from swe_agent.config import SandboxSettings
from swe_agent.observability.artifacts import ArtifactStore
from swe_agent.observability.trajectory import TrajectoryRecorder
from swe_agent.sandbox.docker import EnvSetupError
from swe_agent.sandbox.image import ImageBuilder
from swe_agent.sandbox.service import SandboxService


@pytest.fixture(scope="session")
def sandbox_settings() -> SandboxSettings:
    s = SandboxSettings(memory="512m", test_timeout_s=60)
    try:
        ImageBuilder(s).ensure_base()
    except EnvSetupError as exc:  # e.g. base image cannot be pulled
        pytest.skip(f"sandbox base image unavailable ({s.base_image}): {exc}")
    return s


@pytest.fixture
def svc(sandbox_settings: SandboxSettings, tmp_path: Path) -> Iterator[SandboxService]:
    service = SandboxService(
        sandbox_settings,
        ArtifactStore(tmp_path / "sbx-art"),
        TrajectoryRecorder("t_sbx", "r_sbx", tmp_path / "sbx-art" / "trajectory.jsonl"),
        scratch_root=tmp_path / "sbx-scratch",
        run_label=f"test-{tmp_path.name}",
    )
    yield service
    service.cleanup()


@pytest.fixture
def image(svc: SandboxService, workspace) -> str:  # type: ignore[no-untyped-def]
    tag, _ = svc.prepare_image(workspace)
    return tag
