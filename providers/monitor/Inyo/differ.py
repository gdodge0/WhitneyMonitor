from typing import Mapping, MutableMapping, Dict, Any, List, Set, FrozenSet


class PermitAvailabilityDiffer:
    """
    Compare “remaining” quota between snapshots while persisting the baseline
    in any dict‑like object (e.g. AsyncAutoSavingDict).

    The snapshot **must** use the following structure::

        {
            "https://source‑url": {
                "YYYY‑MM‑DD": {
                    "PERMIT_CODE": {
                        "quota_usage_by_member_daily": {"total": int, "remaining": int},
                        "is_walkup": bool,
                        ...
                    },
                    ...
                },
                ...
            },
            ...
        }

    Unknown permit codes (those **absent** from *permit_lookup*) are **ignored** and
    therefore never included in the diff output. This keeps the operation fast
    even when the snapshots contain a very large number of unknown codes.

    Parameters
    ----------
    store : MutableMapping[str, Dict[str, Any]]
        Rolling baseline; mutated *in‑place* when `update_state=True`.
        It **must** have the same structure as *new*.
    permit_lookup : dict[str, str], optional
        Maps permit codes to user‑friendly names.
    """

    def __init__(
        self,
        store: MutableMapping[str, Dict[str, Any]],
        permit_lookup: Dict[str, str] | None = None,
    ) -> None:
        self.store = store
        self.permit_lookup = permit_lookup or {}
        # Cache known codes for O(1) membership checks in diff()
        self._known_codes: FrozenSet[str] = frozenset(self.permit_lookup)

    # ------------------------------------------------------------------ #
    def diff(
        self,
        new: Mapping[str, Mapping[str, Dict[str, Any]]],
        *,
        update_state: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Compare `new` against the snapshot held in `self.store`.

        Returns
        -------
        list[dict]
            One element per source URL that changed. Each entry has the form::

                {
                    "source": "<URL>",
                    "dates": [
                        {
                            "date": "YYYY‑MM‑DD",
                            "permits": [
                                {
                                    "code": "<PERMIT_CODE>",
                                    "name": "<friendly name>",
                                    "new_remaining": <int>,
                                    "diff": <int>,
                                },
                                ...
                            ],
                        },
                        ...
                    ],
                }
        """
        out: List[Dict[str, Any]] = []

        # Iterate over all sources present in either snapshot
        all_sources: Set[str] = set(self.store) | set(new)

        for source in sorted(all_sources):
            old_dates = self.store.get(source, {})
            new_dates = new.get(source, {})
            # Identify dates whose entire record differs OR is brand‑new/removed.
            modified_dates: Set[str] = (
                (set(old_dates) ^ set(new_dates))
                | {d for d in (set(old_dates) & set(new_dates)) if old_dates[d] != new_dates[d]}
            )

            source_changes: List[Dict[str, Any]] = []

            for date in sorted(modified_dates):
                if date not in new_dates:  # date disappeared entirely
                    continue

                # Consider only permits we explicitly know about
                codes = (
                    (set(new_dates[date]) | set(old_dates.get(date, {})))
                    & self._known_codes
                )
                if not codes:
                    continue  # no known permits for this date

                day = {"date": date, "permits": []}

                for code in codes:
                    new_remaining = (
                        new_dates[date].get(code, {})
                        .get("quota_usage_by_member_daily", {})
                        .get("remaining", 0)
                    )
                    old_remaining = (
                        old_dates.get(date, {})
                        .get(code, {})
                        .get("quota_usage_by_member_daily", {})
                        .get("remaining", 0)
                    )
                    diff_val = new_remaining - old_remaining
                    if diff_val == 0:
                        continue  # no net change

                    day["permits"].append(
                        {
                            "code": code,
                            "name": self.permit_lookup.get(code, code),
                            "new_remaining": new_remaining,
                            "diff": diff_val,
                        }
                    )

                if day["permits"]:
                    source_changes.append(day)

            if source_changes:
                out.append({"source": source, "dates": source_changes})

        # Move the baseline forward if requested
        if update_state:
            self.store.clear()
            # We deliberately store the object as‑is; callers may choose to deep‑copy
            self.store.update(new)

        return out
