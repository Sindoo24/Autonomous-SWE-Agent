"""Exception types. Nodes translate these into typed AgentError / TerminationReason values."""

from __future__ import annotations


class SweAgentError(Exception):
    """Base class."""


class BudgetExceeded(SweAgentError):
    def __init__(self, resource: str, used: float, limit: float) -> None:
        super().__init__(f"budget exceeded: {resource} used={used} limit={limit}")
        self.resource = resource
        self.used = used
        self.limit = limit


class ModelError(SweAgentError):
    """The model endpoint failed, or returned output we could not use after repair."""


class ProviderError(ModelError):
    """Transport/HTTP-level failure talking to a provider."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class PathPolicyError(SweAgentError):
    """A path escaped the workspace or hit a forbidden location."""


class RepoError(SweAgentError):
    """Cloning / preparing the repository failed."""


class ToolPolicyError(SweAgentError):
    """A tool was called in a node that is not allowed to use it, or with forbidden args."""


# Service-layer errors. The HTTP layer maps them to status codes (see swe_agent.api.router).


class NotFoundError(SweAgentError):
    """A requested task, run, experiment or artifact does not exist (HTTP 404)."""


class ConflictError(SweAgentError):
    """The request conflicts with the resource's current state (HTTP 409)."""


class InvalidRequestError(SweAgentError):
    """The request is well-formed but not acceptable (HTTP 422)."""
