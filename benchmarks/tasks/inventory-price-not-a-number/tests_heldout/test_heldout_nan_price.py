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


def test_post_nan_price_is_422():
    for price in ("NaN", "nan", "sNaN", "-NaN"):
        status, body = api.post_products({"sku": "TLS-0001", "name": "Hammer", "price": price})
        assert (price, status, body.get("field")) == (price, 422, "price")


def test_patch_and_query_nan_are_422():
    add("TLS-0001", "12.50")
    status, body = api.patch_product("TLS-0001", {"price": "NaN"})
    assert (status, body["field"]) == (422, "price")
    status, body = api.list_products({"max_price": "NaN"})
    assert (status, body["field"]) == (422, "max_price")
    assert api.get_product("TLS-0001")[1]["price"] == "12.50"


def test_infinite_price_is_422():
    status, body = api.post_products({"sku": "TLS-0002", "name": "Saw", "price": "Infinity"})
    assert (status, body["field"]) == (422, "price")
