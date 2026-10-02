"""Domain errors. Each error knows the HTTP status and error code it maps to."""

from __future__ import annotations

from typing import Any


class InventoryError(Exception):
    """Base class for errors that the API layer turns into a client-facing response."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_body(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.message}


class ValidationError(InventoryError):
    """A request field is missing or malformed."""

    status = 422
    code = "validation_error"

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field

    def to_body(self) -> dict[str, Any]:
        return {"error": self.code, "field": self.field, "detail": self.message}


class NotFoundError(InventoryError):
    status = 404
    code = "not_found"


class ConflictError(InventoryError):
    """The request is well-formed but conflicts with the current state."""

    status = 409
    code = "conflict"


class InsufficientStockError(ConflictError):
    code = "insufficient_stock"

    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"{sku}: requested {requested}, only {available} available")
        self.sku = sku
        self.requested = requested
        self.available = available

    def to_body(self) -> dict[str, Any]:
        body = super().to_body()
        body.update({"sku": self.sku, "requested": self.requested, "available": self.available})
        return body
