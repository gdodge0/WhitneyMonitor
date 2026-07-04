# providers/monitor/OneHome/onehome_event.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from lib.notification_event import MonitorDescriptor, NotificationEvent, Severity


# ────────────────────────────────────────────────────────────────────
# OneHome-flavoured "change" row
# ────────────────────────────────────────────────────────────────────
@dataclass
class ListingChange:
    change_type: str  # "new" | "price" | "status"
    listing_id: str
    address: str
    listing_url: Optional[str] = None
    photo_url: Optional[str] = None
    price: Optional[int] = None
    old_price: Optional[int] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    sqft: Optional[int] = None
    status: Optional[str] = None
    old_status: Optional[str] = None
    property_type: Optional[str] = None


# ────────────────────────────────────────────────────────────────────
# Concrete event class
# ────────────────────────────────────────────────────────────────────
@dataclass
class OneHomeListingEvent(NotificationEvent):
    """Tight contract the OneHome Discord parser can rely on."""
    changes: List[ListingChange] = field(default_factory=list)

    @staticmethod
    def build(changes: List[ListingChange], *, version: str = "1.0.0") -> "OneHomeListingEvent":
        return OneHomeListingEvent(
            monitor=MonitorDescriptor(
                name="OneHome Listings Monitor",
                event_type="onehome.listing.update",
                version=version,
            ),
            summary="OneHome listings changed",
            severity=Severity.SUCCESS
            if any(c.change_type == "new" for c in changes)
            else Severity.INFO,
            data={},  # unused in the custom path
            links=[],  # link logic handled inside the parser
            changes=changes,
        )
