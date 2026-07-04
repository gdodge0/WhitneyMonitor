"""
Diff OneHome listing snapshots to detect new listings, price changes, and
status changes, while persisting the baseline in a dict-like store (e.g.
``AsyncAutoSavingDict``).

Snapshot shape (keyed by listing ``id``)::

    {
        "<listing_id>": {
            "price": int | None,
            "status": str | None,
            "major_change": str | None,
            ... (render fields: address, beds, baths, sqft, photo_url, ...)
        },
        ...
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, MutableMapping


class ListingsDiffer:
    def __init__(self, store: MutableMapping[str, Dict[str, Any]]) -> None:
        self.store = store

    def diff(
        self,
        new: Dict[str, Dict[str, Any]],
        *,
        update_state: bool = True,
    ) -> List[Dict[str, Any]]:
        """Compare ``new`` against the persisted baseline.

        Returns a list of change rows. Each row is the listing's snapshot dict
        plus ``change_type`` (``"new"`` | ``"price"`` | ``"status"``) and, for
        price/status changes, the previous value (``old_price`` / ``old_status``).

        Cold start: if the baseline is empty we seed it and emit **no** changes,
        so the first scan doesn't flood notifications with every current listing.
        """
        cold_start = len(self.store) == 0
        changes: List[Dict[str, Any]] = []

        if not cold_start:
            for listing_id, snap in new.items():
                old = self.store.get(listing_id)

                if old is None:
                    changes.append({**snap, "change_type": "new"})
                    continue

                if snap.get("price") != old.get("price"):
                    changes.append({
                        **snap,
                        "change_type": "price",
                        "old_price": old.get("price"),
                    })

                if (
                    snap.get("status") != old.get("status")
                    or snap.get("major_change") != old.get("major_change")
                ):
                    changes.append({
                        **snap,
                        "change_type": "status",
                        "old_status": old.get("status"),
                    })

        if update_state:
            self.store.clear()
            self.store.update(new)

        return changes
