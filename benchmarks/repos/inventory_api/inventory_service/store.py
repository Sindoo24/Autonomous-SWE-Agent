"""In-memory persistence for products and reservations.

The store owns all stock bookkeeping: ``on_hand`` is the physical count, ``reserved`` is the
part of it promised to customers but not yet shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from inventory_service.errors import ConflictError, InsufficientStockError, NotFoundError
from inventory_service.models import (
    RESERVATION_FULFILLED,
    RESERVATION_RELEASED,
    Product,
    Reservation,
)


@dataclass
class InventoryStore:
    products: dict[str, Product] = field(default_factory=dict)
    reservations: dict[int, Reservation] = field(default_factory=dict)
    next_reservation_id: int = 1

    # -- products -------------------------------------------------------------------------

    def add_product(self, product: Product) -> Product:
        if product.sku in self.products:
            raise ConflictError(f"product {product.sku} already exists")
        self.products[product.sku] = product
        return product

    def get_product(self, sku: str) -> Product:
        try:
            return self.products[sku]
        except KeyError:
            raise NotFoundError(f"product {sku} not found") from None

    def list_products(self) -> list[Product]:
        """All products in insertion order (callers sort as they need)."""
        return list(self.products.values())

    def update_product(self, sku: str, changes: dict[str, Any]) -> Product:
        """Apply already-validated field changes to a product.

        ``on_hand`` may not drop below what is currently reserved.
        """
        current = self.get_product(sku)
        updated = replace(current, **changes)
        if updated.on_hand < updated.reserved:
            raise ConflictError(
                f"on_hand {updated.on_hand} is below reserved quantity {updated.reserved}"
            )
        self.products[sku] = updated
        return updated

    def delete_product(self, sku: str) -> None:
        self.get_product(sku)
        if any(r.sku == sku and r.is_active for r in self.reservations.values()):
            raise ConflictError(f"product {sku} has active reservations")
        del self.products[sku]

    # -- reservations ---------------------------------------------------------------------

    def get_reservation(self, reservation_id: int) -> Reservation:
        try:
            return self.reservations[reservation_id]
        except KeyError:
            raise NotFoundError(f"reservation {reservation_id} not found") from None

    def reserve(self, sku: str, quantity: int, customer: str) -> Reservation:
        """Hold ``quantity`` units of ``sku`` for ``customer``."""
        product = self.get_product(sku)
        if quantity > product.available:
            raise InsufficientStockError(sku, quantity, product.available)
        product.reserved += quantity
        reservation = Reservation(
            id=self.next_reservation_id, sku=sku, quantity=quantity, customer=customer
        )
        self.reservations[reservation.id] = reservation
        self.next_reservation_id += 1
        return reservation

    def release(self, reservation_id: int) -> Reservation:
        """Cancel an active reservation and return its units to the available pool."""
        reservation = self._active(reservation_id)
        product = self.get_product(reservation.sku)
        product.reserved -= reservation.quantity
        reservation.status = RESERVATION_RELEASED
        return reservation

    def fulfil(self, reservation_id: int) -> Reservation:
        """Ship a reservation: the units leave both the reserved and the on-hand count."""
        reservation = self._active(reservation_id)
        product = self.get_product(reservation.sku)
        product.reserved -= reservation.quantity
        product.on_hand -= reservation.quantity
        reservation.status = RESERVATION_FULFILLED
        return reservation

    def _active(self, reservation_id: int) -> Reservation:
        reservation = self.get_reservation(reservation_id)
        if not reservation.is_active:
            raise ConflictError(f"reservation {reservation_id} is already {reservation.status}")
        return reservation
