from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from lib import config
from lib.data_manager import AsyncAutoSavingDict
from providers.monitor.Base.main import BaseMonitorProvider

from .auth import DEFAULT_CHECK_TOKEN_URL, OneHomeAuth
from .differ import ListingsDiffer
from .event_builder import OneHomeEventBuilder
from .http import fetch_listings

# Discord's message content cap is 2000 chars; stay under it with margin.
_LOG_CHAR_LIMIT = 1900
_BURST_HEADER = "<Initial Burst>, Saw:"
_BURST_CONT_HEADER = "<Initial Burst> (cont.):"


def _fmt_price(value: Any) -> str:
    return f"${value:,.0f}" if isinstance(value, (int, float)) else "N/A"


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

    # ---- logging helpers -------------------------------------------------
    def _log(self, message: str) -> List[Any]:
        """Fire a log line at every log provider; return the scheduled tasks."""
        tasks: List[Any] = []
        for lp in self.log_providers:
            task = lp.send_log(message)
            if task is not None:
                tasks.append(task)
        return tasks

    @staticmethod
    def _burst_messages(lines: List[str]) -> List[str]:
        """Pack burst lines into Discord-sized messages (header on each part)."""
        messages: List[str] = []
        header = _BURST_HEADER
        current = header
        for line in lines:
            if len(current) + 1 + len(line) > _LOG_CHAR_LIMIT:
                messages.append(current)
                header = _BURST_CONT_HEADER
                current = f"{header}\n{line}"
            else:
                current = f"{current}\n{line}"
        messages.append(current)
        return messages

    def _log_initial_burst(self, snapshot: Dict[str, Dict[str, Any]]) -> None:
        lines = [
            f"{self.builder.url_for(row) or '(no url)'} - {row.get('address') or 'unknown address'} - {_fmt_price(row.get('price'))}"
            for row in snapshot.values()
        ]
        if not lines:
            return
        for message in self._burst_messages(lines):
            self._log(message)

    async def load(self):
        await self.data.load()
        tracked = len(self.data)
        state = f"resuming with {tracked} tracked listing(s)" if tracked else "cold start (no baseline yet)"
        self._log(
            f"🟢 OneHome monitor started — {state}. "
            f"listing_type={self.cfg.raw.get('listing_type')}, "
            f"cities={self.cfg.raw.get('city_terms')}, cooldown={self.cfg.cooldown}s"
        )

    async def teardown(self):
        tasks = self._log("🔴 OneHome monitor stopped")
        if tasks:
            # Await the flush so the log actually sends before the loop closes.
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_once(self):
        listings, errors = await fetch_listings(
            self.graphql_url,
            self.auth,
            self.cfg.raw,
            max_pages=self.max_pages,
        )

        for err in errors:
            self._log(f"⚠️ OneHome request error: {err}"[:_LOG_CHAR_LIMIT])

        # If the fetch wholly failed (no listings and errors present), don't let
        # the differ treat an empty result as "everything delisted".
        if not listings and errors:
            return

        # Cold start: capture it before diff() seeds the baseline, so we can log
        # the initial burst of everything currently visible.
        is_cold_start = len(self.data) == 0

        snapshot = _snapshot_from_listings(listings)
        changes = self.differ.diff(snapshot)

        if is_cold_start:
            self._log_initial_burst(snapshot)

        if not changes:
            return

        for provider in self.notification_providers:
            event = self.builder.build(changes)
            provider.send_notification(event)
