from decimal import Decimal

import pytest

from inventory_service import api, pricing
from inventory_service.errors import ValidationError
from inventory_service.validation import parse_price, validate_quantity, validate_sku


@pytest.mark.parametrize("raw", ["", "TL-0001", "TOOLSS-0001", "TLS-01", "TLS_0001", None])
def test_invalid_skus(raw):
    with pytest.raises(ValidationError):
        validate_sku(raw)


def test_valid_sku_is_upper_cased():
    assert validate_sku("grd-1234") == "GRD-1234"


@pytest.mark.parametrize("raw", [0, -1, 10_001, "3", 2.0, True])
def test_invalid_quantities(raw):
    with pytest.raises(ValidationError):
        validate_quantity(raw)


def test_parse_price():
    assert parse_price("19.9") == Decimal("19.90")
    assert parse_price(7) == Decimal("7.00")
    for bad in ["-1", "abc", 1.5, "0.001", "100000.01"]:
        with pytest.raises(ValidationError):
            parse_price(bad)


def test_missing_price_is_422():
    status, body = api.post_products({"sku": "TLS-0005", "name": "Pliers"})
    assert status == 422
    assert body["field"] == "price"


def test_volume_tiers():
    assert pricing.volume_percent(49) == 0
    assert pricing.volume_percent(50) == 5
    assert pricing.volume_percent(250) == 10


def test_quote_with_promo(catalogue):
    status, body = api.post_quote(
        {"items": [{"sku": "TLS-0002", "quantity": 2}, {"sku": "GRD-0001", "quantity": 1}],
         "promo_code": "spring12"}
    )
    assert status == 200
    assert body["subtotal"] == "61.00"
    assert body["discount"] == "7.32"
    assert body["tax"] == "9.66"
    assert body["total"] == "63.34"


def test_quote_unknown_promo(catalogue):
    status, body = api.post_quote({"items": [{"sku": "TLS-0001", "quantity": 1}], "promo_code": "NOPE"})
    assert status == 422
    assert body["field"] == "promo_code"
