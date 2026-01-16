"""
General.py – provider-agnostic Discord fallback
==============================================

• Works with any NotificationEvent (no event-specific assumptions).
• One embed per “change” row when event.data["changes"] is a list.
  ─→ For each change, every key/value becomes an embed field (max 25).
• If no "changes" list, the entire event.data payload is shown as a JSON block.
• Embeds are batched (≤10) to respect Discord limits.
• Optional role mention (first payload only).

Entry-point function:  to_payloads(event: NotificationEvent, role_id: int|None) → list[dict]
"""

from __future__ import annotations

import json
import time
from itertools import islice
from typing import Any, Dict, List, Iterator, Optional

from lib.notification_event import NotificationEvent, Severity

# ────────────────────────────────────────────────────────────────────
# Config & helpers
# ────────────────────────────────────────────────────────────────────
_SEVERITY_COLOUR = {
    Severity.INFO: 0x5865F2,
    Severity.SUCCESS: 0x57F287,
    Severity.WARNING: 0xFEE75C,
    Severity.ERROR: 0xED4245,
    Severity.CRITICAL: 0xED4245,
}
_MAX_FIELD_LEN = 1024


def _chunked(it: Iterator, size: int):
    while chunk := list(islice(it, size)):
        yield chunk


def _stringify(val: Any) -> str:
    if isinstance(val, (dict, list)):
        return json.dumps(val, separators=(",", ":"))
    return str(val)


# ────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────
def to_payloads(
        event: NotificationEvent,
        *,
        role_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Convert *event* → list[Discord webhook payload dicts].

    Parameters
    ----------
    event   : NotificationEvent
    role_id : Discord role ID to @mention on first payload (optional)

    Returns
    -------
    list[dict]
    """
    now_ts = int(time.time())
    embeds: List[Dict[str, Any]] = []
    changes = (
        event.data.get("changes")
        if isinstance(event.data, dict)
        else None
    )

    # ─── Case 1: we have a "changes" list ─────────────────────────────
    if isinstance(changes, list) and changes:
        for ch in changes:
            fields = []
            # Convert every key/value pair into an embed field (≤25)
            for key, val in list(ch.items())[:25]:
                fields.append(
                    {
                        "name": str(key).replace("_", " ").title(),
                        "value": _stringify(val)[:_MAX_FIELD_LEN],
                        "inline": True,
                    }
                )

            embeds.append(
                {
                    "title": event.summary or "Event Update",
                    "description": f"Update Timestamp: <t:{now_ts}:T>",
                    "color": _SEVERITY_COLOUR.get(event.severity, 0x5865F2),
                    "fields": fields,
                }
            )
    else:
        # ─── Case 2: generic blob (no changes list) ───────────────────
        pretty_json = json.dumps(event.data, indent=2, default=str) if event.data else "{}"
        embeds.append(
            {
                "title": event.summary or "Event Data",
                "description": f"```json\n{pretty_json}```",
                "color": _SEVERITY_COLOUR.get(event.severity, 0x5865F2),
            }
        )

    # ─── Optional: monitor metadata as an extra embed field block ────
    meta = getattr(event.monitor, "metadata", {}) or {}
    if meta:
        meta_fields = [
            {
                "name": str(k).title(),
                "value": _stringify(v)[:_MAX_FIELD_LEN],
                "inline": True,
            }
            for k, v in list(meta.items())[:25]
        ]
        embeds[0].setdefault("fields", []).extend(meta_fields)

    # ─── Batch embeds (≤10 per payload) ───────────────────────────────
    payloads: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(_chunked(iter(embeds), 10)):
        payloads.append(
            {
                "embeds": chunk,
                "content": f"<@&{role_id}>" if idx == 0 and role_id else "",
                "allowed_mentions": {"parse": []},
            }
        )

    return payloads
