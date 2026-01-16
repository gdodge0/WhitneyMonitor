"""
Parser for event_type == 'inyo.availability.update'
"""

from __future__ import annotations
import time
from itertools import islice
from typing import List, Dict, Iterator, Optional

from providers.monitor.Inyo.inyo_event import InyoAvailabilityEvent, InyoChange


# ---------- util -------------------------------------------------------------
def _chunked(it: Iterator, size: int):
    while chunk := list(islice(it, size)):
        yield chunk


# ---------- public API -------------------------------------------------------
def to_payloads(event: InyoAvailabilityEvent, *, role_id: Optional[int] = None) -> List[Dict]:
    now_ts = int(time.time())
    embeds: List[Dict] = []

    for ch in event.changes:  # one embed per change
        fields = [
            {"name": "Date", "value": ch.date, "inline": True},
            {"name": "Permit Type", "value": ch.permit_name, "inline": True},
            {
                "name": "Availability Change",
                "value": f"{ch.old_remaining} -> {ch.new_remaining}",
                "inline": True,
            },
            {
                "name": "Regular ATC",
                "value": f"[Click Here]({ch.regular_atc_url})",
            },
        ]

        if ch.token and event.auto_reserve_base_url:
            expiry_ts = now_ts + ch.ttl  # or attach real TTL
            fields.insert(3, {  # right after Availability
                "name": f"Auto-Reserve (Expires <t:{expiry_ts}:R>)",
                "value": f"[Click Here]({event.auto_reserve_base_url}?token={ch.token})",
            })

        embeds.append(
            {
                "title": "Availability Update",
                "description": f"Update Timestamp: <t:{now_ts}:T>",
                "fields": fields,
            }
        )

    # -------- batch into <=10-embed messages --------
    payloads: List[Dict] = []
    for idx, chunk in enumerate(_chunked(iter(embeds), 10)):
        payloads.append({
            "content": f"<@&{role_id}>" if idx == 0 and role_id else "",
            "embeds": chunk,
            "flags": 0,
            "username": "Inyo NF Permit Availability Monitor",
            "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/f/f9/Mount_Whitney_2003-03-25.jpg"
        })

    return payloads
