from dataclasses import dataclass, field
from typing import Optional


class NotConfigured(Exception):
    """A store that needs something from its owner first (keys, a capture). Skipped, not failed."""


class SessionExpired(RuntimeError):
    """The store refused the saved sign-in (Breadfast): nothing to do until a new capture is imported."""


@dataclass
class Offer:
    """One product as one store shows it at one moment."""

    store: str  # "talabat", "amazon_eg", ...
    sku: str  # the store's own id for the product
    name: str
    price: float  # what you pay now, EGP
    original_price: Optional[float] = None  # the struck-through price the store shows, if any
    url: str = ""
    image: str = ""
    category: str = ""
    brand: str = ""
    in_stock: bool = True
    extra: dict = field(default_factory=dict)  # kept with the product: rank, deal label, app link, ...
    original_known: bool = True  # False: this source can't see list prices (keep the one already known)

    @property
    def store_discount_pct(self) -> float:
        """The discount the store itself advertises (0 when it shows none)."""
        if self.original_price and self.original_price > self.price > 0:
            return round(100 * (self.original_price - self.price) / self.original_price, 1)
        return 0.0
