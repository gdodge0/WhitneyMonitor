# providers/monitor/OneHome/event_builder.py
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from .onehome_event import ListingChange, OneHomeListingEvent


class OneHomeEventBuilder:
    """Turn differ change rows into an :class:`OneHomeListingEvent`.

    ``listing_url_template`` is ``str.format``-friendly and receives the whole
    change row plus ``{token}`` (the URL-encoded share token), so it can build a
    viewable portal link, e.g. ``".../property/{id}?token={token}"``.
    """

    def __init__(
        self,
        *,
        listing_url_template: Optional[str] = None,
        share_token: Optional[str] = None,
        version: str = "1.0.0",
    ) -> None:
        self.listing_url_template = listing_url_template
        # The share token is base64 (contains '='/'+') → percent-encode for URLs.
        self._enc_token = urllib.parse.quote(share_token, safe="") if share_token else ""
        self.version = version

    def _url_for(self, row: Dict[str, Any]) -> Optional[str]:
        if not self.listing_url_template:
            return None
        try:
            return self.listing_url_template.format(token=self._enc_token, **row)
        except (KeyError, IndexError):
            return None

    def build(self, rows: List[Dict[str, Any]]) -> OneHomeListingEvent:
        changes = [
            ListingChange(
                change_type=row["change_type"],
                listing_id=row.get("listing_id") or row.get("id", ""),
                address=row.get("address", ""),
                listing_url=self._url_for(row),
                photo_url=row.get("photo_url"),
                price=row.get("price"),
                old_price=row.get("old_price"),
                beds=row.get("beds"),
                baths=row.get("baths"),
                sqft=row.get("sqft"),
                status=row.get("status"),
                old_status=row.get("old_status"),
                property_type=row.get("property_type"),
            )
            for row in rows
        ]
        return OneHomeListingEvent.build(changes, version=self.version)
