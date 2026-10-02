"""Framework-free HTTP-style handlers: each returns (status_code, body)."""

from __future__ import annotations

from typing import Any

from users_service.store import UserStore
from users_service.validation import ValidationError, normalize_email

store = UserStore()


def _handle(fn: Any, *args: Any) -> tuple[int, dict[str, Any]]:
    try:
        return fn(*args)
    except ValidationError as exc:
        return 422, {"error": "validation_error", "field": exc.field, "detail": exc.message}
    except Exception:  # noqa: BLE001 - mimic a web framework's catch-all
        return 500, {"error": "internal_server_error"}


def _create_user(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    name = payload.get("name")
    if not name:
        raise ValidationError("name", "name is required")
    email = normalize_email(payload["email"])
    user = store.add(name=name, email=email)
    return 201, {"id": user.id, "name": user.name, "email": user.email}


def post_users(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /users"""
    return _handle(_create_user, payload)


def get_user(user_id: int) -> tuple[int, dict[str, Any]]:
    """GET /users/{id}"""
    user = store.get(user_id)
    if user is None:
        return 404, {"error": "not_found"}
    return 200, {"id": user.id, "name": user.name, "email": user.email}
