from __future__ import annotations

from typing import Any, Dict, List, Optional

from lib import config
from lib.data_manager import AsyncAutoSavingDict
from providers.monitor.Base.main import BaseMonitorProvider

from .auth import DEFAULT_CHECK_TOKEN_URL, OneHomeAuth
from .differ import ListingsDiffer
from .event_builder import OneHomeEventBuilder
from .http import fetch_listings


def _address(prop: Dict[str, Any]) -> str:
    """Build a human-readable single-line address from property fields."""
    line_parts = [
        prop.get("StreetNumber"),
        prop.get("StreetDirPrefix"),
        prop.get("StreetName"),
        prop.get("StreetSuffix"),
        prop.get("StreetDirSuffix"),
    ]
    line = " ".join(str(p) for p in line_parts if p)
    unit = prop.get("UnitNumber")
    if unit:
        line = f"{line} #{unit}"
    city = prop.get("City") or prop.get("PostalCity")
    tail = ", ".join(
        str(p) for p in (city, prop.get("StateOrProvince"), prop.get("PostalCode")) if p
    )
    return ", ".join(p for p in (line, tail) if p)


def _photo_url(media: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """Pick a representative image URL (prefer larger sizes), lowest Order first."""
    if not media:
        return None
    ordered = sorted(media, key=lambda m: m.get("Order") or 0)
    for item in ordered:
        image = item.get("Image") or {}
        for size in ("Large", "Medium", "Thumbnail"):
            url = (image.get(size) or {}).get("mediaUrl")
            if url:
                return url
    return None


def _snapshot_from_listings(listings: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Flatten raw GraphQL listings into the differ's snapshot shape, keyed by id."""
    snapshot: Dict[str, Dict[str, Any]] = {}
    for listing in listings:
        listing_id = listing.get("id")
        if not listing_id:
            continue
        prop = listing.get("property") or {}
        snapshot[str(listing_id)] = {
            # Top-level GraphQL id already has the form "aotf~<num>~<OSN>",
            # which is exactly the portal URL's path segment.
            "id": str(listing_id),
            "listing_id": prop.get("ListingId"),
            "price": prop.get("ListPrice"),
            "status": prop.get("StandardStatus"),
            "major_change": prop.get("MajorChangeType"),
            "beds": prop.get("BedroomsTotal"),
            "baths": prop.get("BathroomsTotalInteger"),
            "sqft": prop.get("LivingArea") or prop.get("LivingAreaTotal"),
            "property_type": prop.get("PropertyType"),
            "address": _address(prop),
            "photo_url": _photo_url(listing.get("media")),
        }
    return snapshot


class OneHomeMonitorProvider(BaseMonitorProvider):
    def __init__(self, cfg: config.MonitorProvider, global_cfg: config.Config):
        super().__init__(cfg, global_cfg)

        self.graphql_url: str = cfg.raw["graphql_url"]
        self.max_pages: int = cfg.raw.get("max_pages", 10)

        share_token: str = cfg.raw["share_token"]
        self.auth = OneHomeAuth(
            share_token,
            cfg.raw.get("check_token_url", DEFAULT_CHECK_TOKEN_URL),
            token_ttl=cfg.raw.get("token_ttl", 1500),
        )

        self.data = AsyncAutoSavingDict(cfg.raw["data_dir"], cfg.raw["data_file"])
        self.differ = ListingsDiffer(self.data)
        self.builder = OneHomeEventBuilder(
            listing_url_template=cfg.raw.get("listing_url_template"),
            share_token=share_token,
            version="1.0.0",
        )

    async def load(self):
        await self.data.load()

    async def run_once(self):
        listings, errors = await fetch_listings(
            self.graphql_url,
            self.auth,
            self.cfg.raw,
            max_pages=self.max_pages,
        )

        for err in errors:
            for lp in self.log_providers:
                lp.send_notification(f"OneHome fetch error: {err}")

        # If the fetch wholly failed (no listings and errors present), don't let
        # the differ treat an empty result as "everything delisted".
        if not listings and errors:
            return

        snapshot = _snapshot_from_listings(listings)
        changes = self.differ.diff(snapshot)
        if not changes:
            return

        for provider in self.notification_providers:
            event = self.builder.build(changes)
            provider.send_notification(event)
