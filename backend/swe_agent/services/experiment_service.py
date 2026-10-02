"""Experiments and their metric tables (read-only; experiments are run from the CLI)."""

from __future__ import annotations

from typing import Any, cast

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from swe_agent.core.errors import NotFoundError
from swe_agent.db import store


class ExperimentService:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def list_experiments(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], jsonable_encoder(await store.list_experiments(self.s)))

    async def metrics(self, exp_id: str) -> dict[str, Any]:
        from swe_agent.evaluation.metrics import experiment_tables

        tables = await experiment_tables(self.s, exp_id)
        if tables is None:
            raise NotFoundError(f"no experiment {exp_id}")
        return cast(dict[str, Any], jsonable_encoder(tables))
