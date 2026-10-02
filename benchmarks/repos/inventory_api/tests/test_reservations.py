from inventory_service import api


def test_reservation_holds_stock(catalogue):
    _, reservation = api.post_reservation("TLS-0001", {"quantity": 5, "customer": "ACME Ltd"})
    assert reservation["quantity"] == 5
    assert reservation["status"] == "active"
    _, product = api.get_product("TLS-0001")
    assert product["reserved"] == 5
    assert product["available"] == 35


def test_reservation_beyond_stock_conflicts(catalogue):
    status, body = api.post_reservation("PNT-0001", {"quantity": 50, "customer": "ACME Ltd"})
    assert status == 409
    assert body["error"] == "insufficient_stock"
    assert body["available"] == 7


def test_release_returns_units(catalogue):
    _, reservation = api.post_reservation("GRD-0001", {"quantity": 4, "customer": "Bo"})
    status, body = api.delete_reservation(reservation["id"])
    assert status == 200
    assert body["status"] == "released"
    assert api.get_product("GRD-0001")[1]["available"] == 12
    assert api.delete_reservation(reservation["id"])[0] == 409


def test_fulfilment_reduces_on_hand(catalogue):
    _, reservation = api.post_reservation("TLS-0001", {"quantity": 3, "customer": "Bo"})
    status, body = api.post_fulfilment(reservation["id"])
    assert status == 200
    assert body["status"] == "fulfilled"
    _, product = api.get_product("TLS-0001")
    assert (product["on_hand"], product["reserved"]) == (37, 0)


def test_cannot_delete_product_with_active_reservation(catalogue):
    api.post_reservation("TLS-0001", {"quantity": 1, "customer": "Bo"})
    assert api.delete_product("TLS-0001")[0] == 409


def test_unknown_reservation_is_404():
    assert api.get_reservation(42)[0] == 404
