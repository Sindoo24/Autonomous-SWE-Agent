"""Thin HTTP client for the agent API. The UI never touches the database or the filesystem:
everything it shows comes through these calls."""

from __future__ import annotations

from typing import Any

import httpx

# Versioned API prefix. /health and /ready stay unversioned (probes).
API = "/api/v1"


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class ApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        *,
        http: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self.http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self.http.headers.update(headers)

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(r.status_code, detail)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    # tasks and runs
    def health(self) -> dict[str, Any]:
        return self._req("GET", "/health")

    def ready(self) -> dict[str, Any]:
        try:
            return self._req("GET", "/ready")
        except ApiError as exc:
            return {
                "ready": False,
                "checks": (exc.detail or {}).get("checks", {})
                if isinstance(exc.detail, dict)
                else {"api": str(exc.detail)},
            }

    def tasks(self, limit: int = 500) -> list[dict[str, Any]]:
        return self._req("GET", f"{API}/tasks", params={"limit": limit})

    def task(self, task_id: str) -> dict[str, Any] | None:
        return self._optional(f"{API}/tasks/{task_id}")

    def create_task(self, repo: str, issue: str) -> str:
        return str(
            self._req("POST", f"{API}/tasks", json={"repo": repo, "issue": issue})["task_id"]
        )

    def create_run(
        self,
        task_id: str,
        *,
        system: str = "agent",
        approval: str = "manual",
        overrides: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        body = {"system": system, "approval": approval, "overrides": overrides or {}}
        return str(self._req("POST", f"{API}/tasks/{task_id}/runs", json=body)["run_id"])

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._req("GET", f"{API}/runs", params={"limit": limit})

    def run(self, run_id: str) -> dict[str, Any]:
        return self._req("GET", f"{API}/runs/{run_id}")

    def status(self, run_id: str) -> dict[str, Any]:
        return self._req("GET", f"{API}/runs/{run_id}/status")

    def trajectory(self, run_id: str, after_seq: int = 0, limit: int = 2000) -> dict[str, Any]:
        return self._req(
            "GET",
            f"{API}/runs/{run_id}/trajectory",
            params={"after_seq": after_seq, "limit": limit},
        )

    def _optional(self, path: str) -> Any:
        try:
            return self._req("GET", path)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def plan(self, run_id: str) -> dict[str, Any] | None:
        return self._optional(f"{API}/runs/{run_id}/plan")

    def patches(self, run_id: str) -> list[dict[str, Any]] | None:
        return self._optional(f"{API}/runs/{run_id}/patches")

    def tests(self, run_id: str) -> dict[str, Any] | None:
        return self._optional(f"{API}/runs/{run_id}/tests")

    def diff(self, run_id: str) -> str:
        return str(self._req("GET", f"{API}/runs/{run_id}/diff"))

    def report(self, run_id: str) -> dict[str, Any] | None:
        return self._optional(f"{API}/runs/{run_id}/report")

    def approval(self, run_id: str) -> dict[str, Any] | None:
        return self._optional(f"{API}/runs/{run_id}/approval")

    def artifact(self, run_id: str, name: str) -> str | None:
        """A text artifact of the run (e.g. a test log), or None when it does not exist."""
        text = self._optional(f"{API}/runs/{run_id}/artifacts/{name}")
        return None if text is None else str(text)

    def metrics(self, run_id: str) -> dict[str, Any] | None:
        return self._optional(f"{API}/runs/{run_id}/metrics")

    def approve(self, run_id: str, by: str | None = None) -> dict[str, Any]:
        return self._req("POST", f"{API}/runs/{run_id}/approve", json={"decided_by": by})

    def reject(
        self, run_id: str, feedback: str | None, retry: bool, by: str | None = None
    ) -> dict[str, Any]:
        return self._req(
            "POST",
            f"{API}/runs/{run_id}/reject",
            json={"feedback": feedback, "retry": retry, "decided_by": by},
        )

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._req("POST", f"{API}/runs/{run_id}/cancel")

    # experiments
    def experiments(self) -> list[dict[str, Any]]:
        return self._req("GET", f"{API}/experiments")

    def experiment_metrics(self, exp_id: str) -> dict[str, Any]:
        return self._req("GET", f"{API}/experiments/{exp_id}/metrics")
