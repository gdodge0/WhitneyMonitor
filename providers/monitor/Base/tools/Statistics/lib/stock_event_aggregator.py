from __future__ import annotations

"""Generic helper that groups stock/availability observations into *Statistics* events.

Each event tracks a single (permit code, entry date) pair and accumulates
successive stock counts until one of the termination conditions is met:

1. A higher-than-initial stock count is observed (new restock).
2. A configurable time window elapses (default 3 hours).

The first observation defines the **highest** stock value for that event and is
used by analytics queries.
"""

from dataclasses import dataclass
from datetime import date as _dt_date, datetime, timedelta, timezone
from typing import Dict, Tuple
from .common import create_event, create_item, close_event, get_session
from sqlalchemy import select
from .models import Event  # type: ignore

__all__ = ["StockEventAggregator"]


@dataclass(slots=True)
class _ActiveEvent:
    event_id: int
    initial_count: int
    created_at: datetime  # UTC


class StockEventAggregator:
    """Aggregate availability observations into Statistics events.

    Parameters
    ----------
    source : str
        Name of the monitor/provider (stored in *Event.source*).
    window_hours : int, default 3
        Maximum duration (hours) during which observations are grouped together
        in the same event.
    """

    def __init__(self, source: str, *, window_hours: int = 24) -> None:
        self._source = source
        self._window = timedelta(hours=window_hours)
        # key = (permit_code, ISO-date)
        self._active: Dict[Tuple[str, str], _ActiveEvent] = {}

        # Restore any active events from previous runs
        self._load_active_events()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def purge_expired(self, now: datetime | None = None) -> None:
        """Remove active events older than *window_hours* (in-place)."""
        now = now or datetime.now(timezone.utc)
        to_del = [k for k, meta in self._active.items() if now - meta.created_at > self._window]
        for k in to_del:
            meta = self._active.pop(k, None)
            if meta:
                close_event(meta.event_id)

    def add_observation(
        self,
        *,
        code: str,
        name: str,
        date_iso: str,
        count: int,
        timestamp: datetime | None = None,
    ) -> None:
        """Feed a single stock observation.

        This method is idempotent and safe to call multiple times per
        monitoring loop.  All timestamps are normalised to UTC.
        """
        ts = timestamp or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # -- Sanity guard -------------------------------------------------
        try:
            date_obj = _dt_date.fromisoformat(date_iso)
        except Exception as e:  # pragma: no cover
            raise ValueError(f"date_iso must be YYYY-MM-DD, got {date_iso!r}") from e

        key = (code, date_iso)

        meta = self._active.get(key)

        # ---------------------------------------------------------------
        # Case A – no active event (or was just expired/terminated) -----
        # ---------------------------------------------------------------
        def _start_new_event(initial: int) -> None:
            ev_id = create_event(
                source=self._source,
                timestamp=ts,
                items=[
                    {
                        "name": name,
                        "code": code,
                        "count": initial,
                        "date": date_obj,
                        "timestamp": ts,
                    }
                ],
            )
            self._active[key] = _ActiveEvent(event_id=ev_id, initial_count=initial, created_at=ts)

        if meta is None:
            if count > 0:
                _start_new_event(count)
            return  # nothing else to do if count == 0

        # ---------------------------------------------------------------
        # Case B – existing active event present ------------------------
        # ---------------------------------------------------------------

        # 1) Check WINDOW expiry BEFORE recording into old event
        if ts - meta.created_at > self._window:
            # event timed-out → close & maybe start new one
            self._active.pop(key, None)
            close_event(meta.event_id)
            if count > 0:
                _start_new_event(count)
            return

        # 2) Higher restock than initial → start fresh event
        if count > meta.initial_count:
            # close old event (without adding this observation)
            self._active.pop(key, None)
            close_event(meta.event_id)
            _start_new_event(count)
            return

        # 3) Normal observation within event window --------------------
        create_item(
            event_id=meta.event_id,
            name=name,
            code=code,
            count=count,
            date=date_obj,
            timestamp=ts,
        )

    # ------------------------------------------------------------------
    # Introspection helpers (optional)
    # ------------------------------------------------------------------
    @property
    def active_events(self) -> Dict[Tuple[str, str], _ActiveEvent]:
        """Return internal active-event mapping (mainly for debugging)."""
        return self._active

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_active_events(self) -> None:
        """Load active events from DB on startup and refresh internal map."""
        now = datetime.now(timezone.utc)
        with get_session() as session:
            stmt = (
                select(Event)
                .where(Event.source == self._source)
                .where(Event.is_active == True)  # noqa: E712 pylint: disable=singleton-comparison
            )
            for ev in session.scalars(stmt):
                ev_ts = ev.timestamp
                if ev_ts.tzinfo is None:
                    ev_ts = ev_ts.replace(tzinfo=timezone.utc)
                if not ev.items:
                    close_event(ev.id)
                    continue
                # Assume first item is highest stock (=initial)
                first_it = min(ev.items, key=lambda it: it.timestamp or ev.timestamp)
                key = (first_it.code, first_it.date.isoformat())

                # Check expiry window
                if now - ev_ts > self._window:
                    close_event(ev.id)
                    continue

                self._active[key] = _ActiveEvent(
                    event_id=ev.id,
                    initial_count=first_it.count,
                    created_at=ev_ts,
                ) 