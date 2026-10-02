import pytest

from inventory_service import api


@pytest.fixture(autouse=True)
def _store():
    api.reset_store()


def add(sku, price, on_hand=0, category="general", name=None):
    status, body = api.post_products(
        {"sku": sku, "name": name or sku, "category": category, "price": price, "on_hand": on_hand}
    )
    assert status == 201, body
    return body

from inventory_service.errors import ValidationError
from inventory_service.validation import validate_sku


def test_post_rejects_trailing_characters():
    for sku in ("TLS-00421", "GRD-1234-B", "PNT-0001 X"):
        status, body = api.post_products({"sku": sku, "name": "Thing", "price": "9.00"})
        assert (sku, status, body.get("field")) == (sku, 422, "sku")
    assert api.list_products()[1]["total"] == 0


def test_lookup_with_trailing_characters_is_422():
    add("TLS-0042", "9.00")
    status, body = api.get_product("TLS-00420")
    assert status == 422
    assert body["field"] == "sku"


def test_well_formed_skus_still_accepted():
    assert validate_sku(" pnt-0001 ") == "PNT-0001"
    assert validate_sku("ABCDE-9999") == "ABCDE-9999"
    with pytest.raises(ValidationError):
        validate_sku("TLS-12345")
