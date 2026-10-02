"""Request payload validation and normalisation."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from inventory_service.errors import ValidationError

#: Three to five letter family code, a dash and a four digit item number, e.g. ``TLS-0042``.
SKU_RE = re.compile(r"^[A-Z]{3,5}-\d{4}$")
MAX_QUANTITY = 10_000
MAX_PRICE = Decimal("100000.00")
PRODUCT_FIELDS = {"sku", "name", "category", "price", "on_hand", "tags"}
PATCHABLE_FIELDS = {"name", "category", "price", "on_hand", "tags"}


def validate_sku(value: Any) -> str:
    """Return the canonical (upper-case, trimmed) SKU or raise ValidationError."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("sku", "sku is required")
    sku = value.strip().upper()
    if not SKU_RE.match(sku):
        raise ValidationError("sku", f"invalid sku {value!r}; expected e.g. TLS-0042")
    return sku


def validate_quantity(value: Any, field: str = "quantity", *, allow_zero: bool = False) -> int:
    """Quantities are whole numbers between 1 (or 0) and MAX_QUANTITY."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, f"{field} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum or value > MAX_QUANTITY:
        raise ValidationError(field, f"{field} must be between {minimum} and {MAX_QUANTITY}")
    return value


def parse_price(value: Any, field: str = "price") -> Decimal:
    """Parse a price given as a string or int into a Decimal with two places.

    Floats are rejected because they cannot represent most cent amounts exactly.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError(field, f"{field} must be a string or integer, not {type(value).__name__}")
    try:
        price = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(field, f"{field} is not a number: {value!r}") from None
    if not price.is_finite():
        raise ValidationError(field, f"{field} must be a finite amount")
    if price < 0 or price > MAX_PRICE:
        raise ValidationError(field, f"{field} must be between 0 and {MAX_PRICE}")
    if price != price.quantize(Decimal("0.01")):
        raise ValidationError(field, f"{field} has more than two decimal places")
    return price.quantize(Decimal("0.01"))


def validate_name(value: Any, field: str = "name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(field, f"{field} is required")
    name = " ".join(value.split())
    if len(name) > 120:
        raise ValidationError(field, f"{field} must be at most 120 characters")
    return name


def validate_tags(value: Any) -> list[str]:
    """Tags are lower-cased, de-duplicated and kept in first-seen order."""
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValidationError("tags", "tags must be a list of strings")
    seen: list[str] = []
    for tag in value:
        tag = tag.strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def validate_new_product(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a POST /products body and return the normalised fields."""
    unknown = set(payload) - PRODUCT_FIELDS
    if unknown:
        raise ValidationError(sorted(unknown)[0], "unknown field")
    if "price" not in payload:
        raise ValidationError("price", "price is required")
    return {
        "sku": validate_sku(payload.get("sku")),
        "name": validate_name(payload.get("name")),
        "category": validate_name(payload.get("category", "general"), "category").lower(),
        "price": parse_price(payload["price"]),
        "on_hand": validate_quantity(payload.get("on_hand", 0), "on_hand", allow_zero=True),
        "tags": validate_tags(payload.get("tags", [])),
    }


def validate_product_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a PATCH /products/{sku} body; only the supplied fields are returned."""
    if not payload:
        raise ValidationError("body", "at least one field is required")
    unknown = set(payload) - PATCHABLE_FIELDS
    if unknown:
        raise ValidationError(sorted(unknown)[0], "field cannot be changed")
    changes: dict[str, Any] = {}
    if "name" in payload:
        changes["name"] = validate_name(payload["name"])
    if "category" in payload:
        changes["category"] = validate_name(payload["category"], "category").lower()
    if "price" in payload:
        changes["price"] = parse_price(payload["price"])
    if "on_hand" in payload:
        changes["on_hand"] = validate_quantity(payload["on_hand"], "on_hand", allow_zero=True)
    if "tags" in payload:
        changes["tags"] = validate_tags(payload["tags"])
    return changes
