"""Framework-free HTTP-style handlers: each returns ``(status_code, body)``.

Query parameters arrive exactly as a web framework would hand them over: a ``dict`` of
strings. Request bodies are already-decoded JSON objects.
"""

from __future__ import annotations

from typing import Any, Callable

from inventory_service import pricing
from inventory_service.errors import InventoryError, ValidationError
from inventory_service.models import Product
from inventory_service.store import InventoryStore
from inventory_service.validation import (
    parse_price,
    validate_name,
    validate_new_product,
    validate_product_patch,
    validate_quantity,
    validate_sku,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
SORT_FIELDS: dict[str, Callable[[Product], Any]] = {
    "sku": lambda p: p.sku,
    "name": lambda p: p.name.lower(),
    "price": lambda p: p.price,
    "available": lambda p: p.available,
}

store = InventoryStore()


def reset_store() -> InventoryStore:
    """Replace the module-level store with an empty one (used by tests and fixtures)."""
    global store
    store = InventoryStore()
    return store


def _handle(fn: Callable[..., Any], *args: Any) -> tuple[int, dict[str, Any]]:
    try:
        return fn(*args)
    except InventoryError as exc:
        return exc.status, exc.to_body()
    except Exception:  # noqa: BLE001 - mimic a web framework's catch-all
        return 500, {"error": "internal_server_error"}


# -- query parsing ------------------------------------------------------------------------


def _int_param(query: dict[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError(name, f"{name} must be an integer") from None
    if value < lo or value > hi:
        raise ValidationError(name, f"{name} must be between {lo} and {hi}")
    return value


def _bool_param(query: dict[str, str], name: str) -> bool | None:
    raw = query.get(name)
    if raw is None or raw == "":
        return None
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes"):
        return True
    if lowered in ("0", "false", "no"):
        return False
    raise ValidationError(name, f"{name} must be true or false")


def _sort_param(query: dict[str, str]) -> tuple[Callable[[Product], Any], bool]:
    raw = query.get("sort") or "sku"
    descending = raw.startswith("-")
    key = raw.lstrip("-")
    if key not in SORT_FIELDS:
        raise ValidationError("sort", f"cannot sort by {key!r}")
    return SORT_FIELDS[key], descending


def _product_filter(query: dict[str, str]) -> Callable[[Product], bool]:
    category = query.get("category", "").strip().lower() or None
    in_stock = _bool_param(query, "in_stock")
    min_price = parse_price(query["min_price"], "min_price") if query.get("min_price") else None
    max_price = parse_price(query["max_price"], "max_price") if query.get("max_price") else None
    tag = query.get("tag", "").strip().lower() or None

    def keep(product: Product) -> bool:
        if category is not None and product.category != category:
            return False
        if in_stock is not None and (product.available > 0) != in_stock:
            return False
        if min_price is not None and product.price < min_price:
            return False
        if max_price is not None and product.price > max_price:
            return False
        return tag is None or tag in product.tags

    return keep


# -- products -----------------------------------------------------------------------------


def _create_product(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    fields = validate_new_product(payload)
    product = store.add_product(Product(**fields))
    return 201, product.to_dict()


def post_products(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /products"""
    return _handle(_create_product, payload)


def _get_product(sku: str) -> tuple[int, dict[str, Any]]:
    return 200, store.get_product(validate_sku(sku)).to_dict()


def get_product(sku: str) -> tuple[int, dict[str, Any]]:
    """GET /products/{sku}"""
    return _handle(_get_product, sku)


def _list_products(query: dict[str, str]) -> tuple[int, dict[str, Any]]:
    page = _int_param(query, "page", 1, 1, 10_000)
    page_size = _int_param(query, "page_size", DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
    sort_key, descending = _sort_param(query)
    keep = _product_filter(query)

    matching = [p for p in store.list_products() if keep(p)]
    matching.sort(key=sort_key, reverse=descending)
    start = (page - 1) * page_size
    window = matching[start : start + page_size]
    return 200, {
        "items": [p.to_dict() for p in window],
        "total": len(matching),
        "page": page,
        "page_size": page_size,
        "pages": (len(matching) + page_size - 1) // page_size,
    }


def list_products(query: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """GET /products?page=&page_size=&sort=&category=&in_stock=&min_price=&max_price=&tag="""
    return _handle(_list_products, dict(query or {}))


def _patch_product(sku: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    changes = validate_product_patch(payload)
    return 200, store.update_product(validate_sku(sku), changes).to_dict()


def patch_product(sku: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """PATCH /products/{sku}"""
    return _handle(_patch_product, sku, payload)


def _delete_product(sku: str) -> tuple[int, dict[str, Any]]:
    store.delete_product(validate_sku(sku))
    return 204, {}


def delete_product(sku: str) -> tuple[int, dict[str, Any]]:
    """DELETE /products/{sku}"""
    return _handle(_delete_product, sku)


# -- reservations -------------------------------------------------------------------------


def _create_reservation(sku: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    quantity = validate_quantity(payload.get("quantity"))
    customer = validate_name(payload.get("customer"), "customer")
    reservation = store.reserve(validate_sku(sku), quantity, customer)
    return 201, reservation.to_dict()


def post_reservation(sku: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /products/{sku}/reservations"""
    return _handle(_create_reservation, sku, payload)


def get_reservation(reservation_id: int) -> tuple[int, dict[str, Any]]:
    """GET /reservations/{id}"""
    return _handle(lambda: (200, store.get_reservation(reservation_id).to_dict()))


def delete_reservation(reservation_id: int) -> tuple[int, dict[str, Any]]:
    """DELETE /reservations/{id} -- release the held units."""
    return _handle(lambda: (200, store.release(reservation_id).to_dict()))


def post_fulfilment(reservation_id: int) -> tuple[int, dict[str, Any]]:
    """POST /reservations/{id}/fulfilment -- ship the reserved units."""
    return _handle(lambda: (200, store.fulfil(reservation_id).to_dict()))


# -- quotes -------------------------------------------------------------------------------


def _create_quote(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("items", "items must be a non-empty list")
    try:
        promo = pricing.promo_percent(payload.get("promo_code"))
    except KeyError:
        raise ValidationError("promo_code", "unknown promotion code") from None

    lines = []
    for item in items:
        product = store.get_product(validate_sku(item.get("sku")))
        quantity = validate_quantity(item.get("quantity"))
        lines.append(pricing.price_line(product.sku, product.price, quantity, promo))
    quote = pricing.build_quote(lines)
    return 200, {
        "lines": [
            {
                "sku": line.sku,
                "quantity": line.quantity,
                "unit_price": pricing.money(line.unit_price),
                "percent_off": line.percent_off,
                "subtotal": pricing.money(line.subtotal),
                "discount": pricing.money(line.discount),
                "net": pricing.money(line.net),
            }
            for line in quote.lines
        ],
        "subtotal": pricing.money(quote.subtotal),
        "discount": pricing.money(quote.discount),
        "tax": pricing.money(quote.tax),
        "total": pricing.money(quote.total),
    }


def post_quote(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST /quotes -- price a prospective order without reserving stock."""
    return _handle(_create_quote, payload)
