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


def test_reserve_exactly_available():
    add("PNT-0001", "31.99", on_hand=7)
    status, body = api.post_reservation("PNT-0001", {"quantity": 7, "customer": "ACME Ltd"})
    assert status == 201
    assert body["quantity"] == 7
    assert api.get_product("PNT-0001")[1]["available"] == 0


def test_reserve_remaining_after_partial():
    add("PNT-0001", "31.99", on_hand=7)
    api.post_reservation("PNT-0001", {"quantity": 6, "customer": "A"})
    status, _ = api.post_reservation("PNT-0001", {"quantity": 1, "customer": "B"})
    assert status == 201
    status, body = api.post_reservation("PNT-0001", {"quantity": 1, "customer": "C"})
    assert status == 409
    assert body["available"] == 0


def test_over_request_still_rejected():
    add("PNT-0001", "31.99", on_hand=7)
    status, body = api.post_reservation("PNT-0001", {"quantity": 8, "customer": "A"})
    assert (status, body["available"]) == (409, 7)
