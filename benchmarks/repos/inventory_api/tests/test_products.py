from inventory_service import api


def test_create_product_normalises_fields():
    status, body = api.post_products(
        {"sku": " tls-0042 ", "name": "  Torque   Wrench ", "category": "Tools", "price": "49.9",
         "on_hand": 3, "tags": ["Metric", "metric", " Pro "]}
    )
    assert status == 201
    assert body["sku"] == "TLS-0042"
    assert body["name"] == "Torque Wrench"
    assert body["category"] == "tools"
    assert body["price"] == "49.90"
    assert body["tags"] == ["metric", "pro"]
    assert body["available"] == 3


def test_duplicate_sku_conflicts(catalogue):
    status, body = api.post_products({"sku": "TLS-0001", "name": "Other", "price": "1"})
    assert status == 409
    assert body["error"] == "conflict"


def test_get_unknown_product_is_404():
    status, body = api.get_product("TLS-9999")
    assert status == 404
    assert body["error"] == "not_found"


def test_patch_product_updates_only_given_fields(catalogue):
    status, body = api.patch_product("TLS-0001", {"price": "13.75"})
    assert status == 200
    assert body["price"] == "13.75"
    assert body["name"] == "Claw Hammer"


def test_patch_rejects_sku_change(catalogue):
    status, body = api.patch_product("TLS-0001", {"sku": "TLS-0003"})
    assert status == 422
    assert body["field"] == "sku"


def test_delete_product(catalogue):
    assert api.delete_product("PNT-0001") == (204, {})
    assert api.get_product("PNT-0001")[0] == 404


def test_list_default_sorted_by_sku(catalogue):
    status, body = api.list_products()
    assert status == 200
    assert [p["sku"] for p in body["items"]] == ["GRD-0001", "PNT-0001", "TLS-0001", "TLS-0002"]
    assert body["total"] == 4
    assert body["pages"] == 1


def test_list_sort_descending_by_price(catalogue):
    _, body = api.list_products({"sort": "-price"})
    assert [p["sku"] for p in body["items"]] == ["PNT-0001", "GRD-0001", "TLS-0002", "TLS-0001"]


def test_list_pagination(catalogue):
    _, body = api.list_products({"page": "2", "page_size": "3"})
    assert [p["sku"] for p in body["items"]] == ["TLS-0002"]
    assert body["pages"] == 2


def test_list_filters(catalogue):
    _, body = api.list_products({"category": "tools"})
    assert [p["sku"] for p in body["items"]] == ["TLS-0001", "TLS-0002"]
    _, body = api.list_products({"in_stock": "false"})
    assert [p["sku"] for p in body["items"]] == ["TLS-0002"]
    _, body = api.list_products({"min_price": "20", "tag": "outdoor"})
    assert [p["sku"] for p in body["items"]] == ["GRD-0001"]


def test_list_rejects_bad_query(catalogue):
    assert api.list_products({"page_size": "0"})[0] == 422
    assert api.list_products({"sort": "colour"})[1]["field"] == "sort"
    assert api.list_products({"in_stock": "maybe"})[0] == 422
