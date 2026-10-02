import pytest

from inventory_service import api


@pytest.fixture(autouse=True)
def fresh_store():
    return api.reset_store()


@pytest.fixture
def catalogue():
    products = [
        {"sku": "TLS-0001", "name": "Claw Hammer", "category": "tools", "price": "12.50", "on_hand": 40},
        {"sku": "TLS-0002", "name": "Hand Saw", "category": "tools", "price": "18.00", "on_hand": 0},
        {"sku": "GRD-0001", "name": "Garden Hose", "category": "garden", "price": "25.00", "on_hand": 12,
         "tags": ["Outdoor", "summer"]},
        {"sku": "PNT-0001", "name": "Wall Paint", "category": "paint", "price": "31.99", "on_hand": 7},
    ]
    for payload in products:
        status, body = api.post_products(payload)
        assert status == 201, body
    return products
