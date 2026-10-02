"""Request dependencies: settings, database session, API-key auth, services, input policy.

Auth: one static key (`SWE_API_KEY`, header `X-API-Key`). Without a key, bind to localhost.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from swe_agent.config import Settings
from swe_agent.services.experiment_service import ExperimentService
from swe_agent.services.run_service import RunService
from swe_agent.services.task_service import TaskService


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as s:
        yield s


async def require_key(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    key = request.app.state.settings.api_key
    if key and not (x_api_key and secrets.compare_digest(x_api_key, key)):
        raise HTTPException(401, "missing or invalid X-API-Key")


# Module level: FastAPI resolves these annotations from the module namespace.
SettingsDep = Annotated[Settings, Depends(get_settings)]
DB = Annotated[AsyncSession, Depends(get_session)]
Auth = Depends(require_key)


def task_service(s: DB) -> TaskService:
    return TaskService(s)


def run_service(s: DB, settings: SettingsDep) -> RunService:
    return RunService(s, settings)


def experiment_service(s: DB) -> ExperimentService:
    return ExperimentService(s)


Tasks = Annotated[TaskService, Depends(task_service)]
Runs = Annotated[RunService, Depends(run_service)]
Experiments = Annotated[ExperimentService, Depends(experiment_service)]


def validate_source(source: str, settings: Settings) -> str:
    """Repository sources accepted over HTTP: https git URLs, or local directories under the
    configured roots (SWE_API_REPO_ROOTS). Other git transports (ssh, ext::, file:) and option
    injection (`-o...`) are refused."""
    source = source.strip()
    if re.fullmatch(r"https://[^\s]+", source):
        return source
    if "://" in source or "::" in source or source.startswith(("git@", "ssh:", "file:", "-")):
        raise HTTPException(422, "repo must be an https git URL or a local directory")
    path = Path(source).expanduser().resolve()
    roots = settings.repo_roots()
    if not any(path == r or path.is_relative_to(r) for r in roots):
        allowed = ", ".join(map(str, roots))
        raise HTTPException(403, f"local repositories must be under SWE_API_REPO_ROOTS ({allowed})")
    if not path.is_dir():
        raise HTTPException(422, f"not a directory: {source}")
    return str(path)
