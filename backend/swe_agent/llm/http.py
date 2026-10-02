"""Shared HTTP helper with bounded retries for transient provider failures."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from swe_agent.core.errors import ProviderError
from swe_agent.observability.logging import get_logger

_log = get_logger("llm.http")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    max_retries: int = 2,
    backoff_s: float = 1.0,
) -> dict[str, Any]:
    attempt = 0
    while True:
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            err = ProviderError(f"{type(exc).__name__}: {exc}", retryable=True)
        else:
            if resp.status_code < 400:
                try:
                    body = resp.json()
                except ValueError as exc:
                    raise ProviderError(f"non-JSON response: {exc}", retryable=False) from exc
                if not isinstance(body, dict):
                    raise ProviderError("response body is not a JSON object", retryable=False)
                return body
            err = ProviderError(
                f"HTTP {resp.status_code}: {resp.text[:500]}",
                retryable=resp.status_code in RETRYABLE_STATUS,
            )
        if not err.retryable or attempt >= max_retries:
            raise err
        attempt += 1
        delay = backoff_s * (2 ** (attempt - 1))
        _log.warning("provider_retry", url=url, attempt=attempt, delay_s=delay, error=str(err))
        await asyncio.sleep(delay)
