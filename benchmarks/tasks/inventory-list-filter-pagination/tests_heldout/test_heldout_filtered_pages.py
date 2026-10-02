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


@pytest.fixture(autouse=True)
def six_products(_store):
    for n in (1, 2, 3):
        add(f"GRD-000{n}", f"{n}0.00", on_hand=n, category="garden")
    for n in (1, 2, 3):
        add(f"TLS-000{n}", f"{n}5.00", on_hand=0 if n == 2 else 5, category="tools")


def test_filtered_first_page_is_full():
    status, body = api.list_products({"category": "tools", "page_size": "2"})
    assert status == 200
    assert [p["sku"] for p in body["items"]] == ["TLS-0001", "TLS-0002"]
    assert (body["total"], body["pages"]) == (3, 2)


def test_filtered_second_page():
    _, body = api.list_products({"category": "tools", "page_size": "2", "page": "2"})
    assert [p["sku"] for p in body["items"]] == ["TLS-0003"]


def test_filter_sort_and_page_combined():
    _, body = api.list_products({"in_stock": "true", "sort": "-price", "page_size": "3"})
    assert [p["sku"] for p in body["items"]] == ["TLS-0003", "GRD-0003", "GRD-0002"]
    assert body["total"] == 5
