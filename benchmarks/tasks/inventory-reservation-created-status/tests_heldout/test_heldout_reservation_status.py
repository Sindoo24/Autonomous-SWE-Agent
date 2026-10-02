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


def test_new_reservation_returns_201():
    add("TLS-0001", "12.50", on_hand=40)
    status, body = api.post_reservation("TLS-0001", {"quantity": 2, "customer": "ACME Ltd"})
    assert status == 201
    assert body == {"id": 1, "sku": "TLS-0001", "quantity": 2, "customer": "ACME Ltd", "status": "active"}


def test_each_new_reservation_is_created():
    add("GRD-0001", "25.00", on_hand=10)
    first = api.post_reservation("GRD-0001", {"quantity": 1, "customer": "Bo"})
    second = api.post_reservation("grd-0001", {"quantity": 1, "customer": "Cy"})
    assert (first[0], second[0]) == (201, 201)
    assert second[1]["id"] == first[1]["id"] + 1


def test_rejected_reservation_is_not_201():
    add("PNT-0001", "31.99", on_hand=1)
    status, body = api.post_reservation("PNT-0001", {"quantity": 5, "customer": "Bo"})
    assert status == 409
    assert body["error"] == "insufficient_stock"
