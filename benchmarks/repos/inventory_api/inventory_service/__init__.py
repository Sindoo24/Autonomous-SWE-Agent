"""Inventory service: products, stock reservations and price quotes.

The HTTP layer is framework-free: every handler in :mod:`inventory_service.api` returns a
``(status_code, body)`` tuple, which keeps the service easy to exercise from tests.
"""

__version__ = "0.3.0"
