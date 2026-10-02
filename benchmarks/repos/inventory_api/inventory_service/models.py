"""Domain records for products and reservations."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

RESERVATION_ACTIVE = "active"
RESERVATION_RELEASED = "released"
RESERVATION_FULFILLED = "fulfilled"


@dataclass
class Product:
    sku: str
    name: str
    category: str
    price: Decimal
    on_hand: int = 0
    reserved: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def available(self) -> int:
        """Units that can still be reserved."""
        return self.on_hand - self.reserved

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "price": f"{self.price:.2f}",
            "on_hand": self.on_hand,
            "reserved": self.reserved,
            "available": self.available,
            "tags": list(self.tags),
        }


@dataclass
class Reservation:
    id: int
    sku: str
    quantity: int
    customer: str
    status: str = RESERVATION_ACTIVE

    @property
    def is_active(self) -> bool:
        return self.status == RESERVATION_ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sku": self.sku,
            "quantity": self.quantity,
            "customer": self.customer,
            "status": self.status,
        }
