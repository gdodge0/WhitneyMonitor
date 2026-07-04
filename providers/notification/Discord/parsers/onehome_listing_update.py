"""
Parser for event_type == 'onehome.listing.update'

Renders one rich embed per listing change (new listing, price change, or
status change) with a photo, clickable title link, and property details.
"""
from __future__ import annotations

import time
from itertools import islice
from typing import Dict, Iterator, List, Optional

from providers.monitor.OneHome.onehome_event import ListingChange, OneHomeListingEvent

# change_type → (title prefix, embed colour)
_CHANGE_STYLE = {
    "new": ("🏠 New Listing", 0x57F287),
    "price": ("💰 Price Change", 0xFEE75C),
    "status": ("🔁 Status Change", 0x5865F2),
}


def _chunked(it: Iterator, size: int):
    while chunk := list(islice(it, size)):
        yield chunk


def _fmt_price(value: Optional[int]) -> str:
    return f"${value:,.0f}" if isinstance(value, (int, float)) else "N/A"


def _price_field(ch: ListingChange) -> Dict:
    if ch.change_type == "price" and ch.old_price is not None:
        arrow = "🔻" if (ch.price or 0) < ch.old_price else "🔺"
        return {
            "name": "Price",
            "value": f"{_fmt_price(ch.old_price)} → {_fmt_price(ch.price)} {arrow}",
            "inline": True,
        }
    return {"name": "Price", "value": _fmt_price(ch.price), "inline": True}


def _status_field(ch: ListingChange) -> Dict:
    if ch.change_type == "status" and ch.old_status:
        return {
            "name": "Status",
            "value": f"{ch.old_status} → {ch.status or 'N/A'}",
            "inline": True,
        }
    return {"name": "Status", "value": ch.status or "N/A", "inline": True}


def to_payloads(event: OneHomeListingEvent, *, role_id: Optional[int] = None) -> List[Dict]:
    now_ts = int(time.time())
    embeds: List[Dict] = []

    for ch in event.changes:
        title_prefix, colour = _CHANGE_STYLE.get(ch.change_type, ("🏠 Listing", 0x5865F2))

        beds_baths = "/".join(
            str(v) if v is not None else "?" for v in (ch.beds, ch.baths)
        )
        fields = [
            _price_field(ch),
            {"name": "Beds/Baths", "value": beds_baths, "inline": True},
            {"name": "Sqft", "value": str(ch.sqft) if ch.sqft else "N/A", "inline": True},
            _status_field(ch),
        ]
        if ch.property_type:
            fields.append({"name": "Type", "value": ch.property_type, "inline": True})

        embed: Dict = {
            "title": f"{title_prefix} — {ch.address or 'Unknown address'}",
            "description": f"Update Timestamp: <t:{now_ts}:T>",
            "color": colour,
            "fields": fields,
        }
        if ch.listing_url:
            embed["url"] = ch.listing_url
        if ch.photo_url:
            embed["image"] = {"url": ch.photo_url}

        embeds.append(embed)

    payloads: List[Dict] = []
    for idx, chunk in enumerate(_chunked(iter(embeds), 10)):
        payloads.append({
            "content": f"<@&{role_id}>" if idx == 0 and role_id else "",
            "embeds": chunk,
            "flags": 0,
            "username": "OneHome Listings Monitor",
            "allowed_mentions": {"parse": ["roles"] if role_id else []},
        })

    return payloads
