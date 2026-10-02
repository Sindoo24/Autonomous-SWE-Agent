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


def test_volume_discount_is_percent_of_line_subtotal():
    add("FST-0001", "3.35", on_hand=500)
    status, body = api.post_quote({"items": [{"sku": "FST-0001", "quantity": 60}]})
    assert status == 200
    line = body["lines"][0]
    assert (line["percent_off"], line["subtotal"], line["discount"], line["net"]) == (5, "201.00", "10.05", "190.95")
    assert (body["tax"], body["total"]) == ("34.37", "225.32")


def test_promo_discount_on_small_unit_price():
    add("FST-0002", "0.99", on_hand=100)
    _, body = api.post_quote({"items": [{"sku": "FST-0002", "quantity": 10}], "promo_code": "STAFF20"})
    assert body["discount"] == "1.98"
    assert body["tax"] == "1.43"
    assert body["total"] == "9.35"


def test_multi_line_totals_are_sums_of_lines():
    add("FST-0001", "3.35", on_hand=500)
    add("TLS-0001", "10.00", on_hand=5)
    _, body = api.post_quote({"items": [{"sku": "FST-0001", "quantity": 3}, {"sku": "TLS-0001", "quantity": 1}],
                              "promo_code": "WELCOME5"})
    assert [l["discount"] for l in body["lines"]] == ["0.50", "0.50"]
    assert body["discount"] == "1.00"
    assert body["total"] == "22.48"
