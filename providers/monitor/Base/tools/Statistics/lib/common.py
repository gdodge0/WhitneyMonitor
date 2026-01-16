from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, date as dt_date, UTC
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Sequence, Union

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, joinedload, sessionmaker

from .models import Base, Event, Item

# ---------------------------------------------------------------------------
# Engine / Session lifecycle helpers
# ---------------------------------------------------------------------------

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def init_sqlite_db(db_location: Union[str, Path], *, echo: bool = False) -> Engine:
    """Initialise (or connect to) an SQLite DB and create all tables.

    Parameters
    ----------
    db_location : str | pathlib.Path
        • Plain file path: ``"db/app.db"`` or ``"/abs/path/app.sqlite"``.
        • Full SQLAlchemy URI: ``"sqlite:///db/app.db"`` (allows extra params).
    echo : bool, default ``False``
        Emit SQL statements to stdout for debugging.

    Returns
    -------
    sqlalchemy.engine.Engine
        The configured Engine. Also stored in the module as a singleton.
    """
    global _engine, _SessionLocal

    db_location = str(db_location)
    if db_location.startswith("sqlite:///"):
        uri = db_location
        sqlite_filepath = Path(db_location.removeprefix("sqlite:///"))
    else:
        sqlite_filepath = Path(db_location)
        uri = f"sqlite:///{sqlite_filepath}"

    # Ensure parent directories exist
    if not sqlite_filepath.parent.exists():
        sqlite_filepath.parent.mkdir(parents=True, exist_ok=True)

    # Create Engine + schema
    _engine = create_engine(uri, echo=echo, future=True)
    Base.metadata.create_all(_engine)

    # Configure Session factory
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context-manager yielding a SQLAlchemy Session bound to the global Engine."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_sqlite_db() first.")

    session: Session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CRUD helpers – Events
# ---------------------------------------------------------------------------

def create_event(
        *,
        source: str,
        timestamp: datetime,
        items: Optional[List[dict]] = None,
        is_active: bool = True,
) -> int:
    """Create an *Event* with zero or more *Item*s and return its ID."""
    with get_session() as session:
        event = Event(source=source, timestamp=timestamp, is_active=is_active)
        for item_data in items or []:
            event.items.append(Item(**item_data))
        session.add(event)
        session.flush()  # populates primary key
        return event.id


def _apply_event_filters(stmt, filters: Dict[str, Any]):
    """Internal: mutate SQLAlchemy *stmt* with equality filters on Event attributes."""
    for key, value in filters.items():
        if not hasattr(Event, key):
            raise ValueError(f"Invalid Event field: {key}")
        stmt = stmt.filter(getattr(Event, key) == value)
    return stmt


def filter_events(
        *,
        filters: Optional[Dict[str, Any]] = None,
        min_ts: datetime | None = None,
        max_ts: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        order_desc: bool = False,
) -> List[Event]:
    """Return Events matching *any* field plus optional timestamp range.

    Parameters
    ----------
    filters : dict[str, Any] | None
        Equality filters; keys must be valid column names on Event.
    min_ts, max_ts : datetime | None
        Inclusive timestamp range on ``Event.timestamp``.
    order_desc : bool
        Sort by ``Event.id`` descending if ``True`` (default ascending).
    """
    filters = filters or {}
    with get_session() as session:
        stmt = select(Event).options(joinedload(Event.items)).offset(offset)
        if order_desc:
            stmt = stmt.order_by(Event.id.desc())
        else:
            stmt = stmt.order_by(Event.id)
        stmt = _apply_event_filters(stmt, filters)
        if min_ts is not None:
            stmt = stmt.filter(Event.timestamp >= min_ts)
        if max_ts is not None:
            stmt = stmt.filter(Event.timestamp <= max_ts)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


def update_event(event_id: int, **changes) -> bool:
    """Update mutable fields (``source`` and/or ``timestamp``) for an Event."""
    with get_session() as session:
        event = session.get(Event, event_id)
        if event is None:
            return False
        if "source" in changes:
            event.source = changes["source"]
        if "timestamp" in changes:
            event.timestamp = changes["timestamp"]
        session.add(event)
        return True


def delete_event(event_id: int) -> bool:
    """Delete an Event (and its orphan-cascaded Items). Returns *True* if found."""
    with get_session() as session:
        event = session.get(Event, event_id)
        if event is None:
            return False
        session.delete(event)
        return True


# ---------------------------------------------------------------------------
# CRUD helpers – Items
# ---------------------------------------------------------------------------

def create_item(
        *,
        event_id: int,
        name: str,
        code: str,
        count: int,
        date: dt_date,
        timestamp: datetime | None = None,
) -> int:
    """Create an *Item* linked to an existing Event and return its ID."""
    with get_session() as session:
        parent = session.get(Event, event_id)
        if parent is None:
            raise ValueError(f"Event {event_id} not found")
        if timestamp is None:
            timestamp = datetime.now(UTC)

        item = Item(event=parent, name=name, code=code, count=count, date=date, timestamp=timestamp)
        session.add(item)
        session.flush()
        return item.id


def _apply_item_filters(stmt, filters: Dict[str, Any]):
    """Internal: mutate *stmt* with equality filters on Item attributes."""
    for key, value in filters.items():
        if not hasattr(Item, key):
            raise ValueError(f"Invalid Item field: {key}")
        stmt = stmt.filter(getattr(Item, key) == value)
    return stmt


def filter_items(
        *,
        filters: Optional[Dict[str, Any]] = None,
        event_filters: Optional[Dict[str, Any]] = None,
        event_min_ts: datetime | None = None,
        event_max_ts: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        order_desc: bool = False,
) -> List[Item]:
    """Return Items matching their own fields, plus optional *Event* filters or time range.

    Parameters
    ----------
    filters : dict[str, Any] | None
        Equality filters on Item columns (``name``, ``code``, ``count``, etc.).
    event_filters : dict[str, Any] | None
        Equality filters on the *parent* Event (e.g. ``{"source": "sensor"}``).
    event_min_ts, event_max_ts : datetime | None
        Inclusive time window applied to ``Event.timestamp``. Works even when
        querying Items.
    """
    filters = filters or {}
    event_filters = event_filters or {}

    with get_session() as session:
        stmt = (
            select(Item)
            .join(Item.event)
            .options(joinedload(Item.event))
            .offset(offset)
        )
        if order_desc:
            stmt = stmt.order_by(Item.id.desc())
        else:
            stmt = stmt.order_by(Item.id)

        stmt = _apply_item_filters(stmt, filters)
        stmt = _apply_event_filters(stmt, event_filters)

        if event_min_ts is not None:
            stmt = stmt.filter(Event.timestamp >= event_min_ts)
        if event_max_ts is not None:
            stmt = stmt.filter(Event.timestamp <= event_max_ts)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Same query helper but using an *existing* session (no auto-close)
# ---------------------------------------------------------------------------

def filter_items_with_session(
        session: Session,
        *,
        filters: Optional[Dict[str, Any]] = None,
        event_filters: Optional[Dict[str, Any]] = None,
        event_min_ts: datetime | None = None,
        event_max_ts: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        order_desc: bool = False,
) -> List[Item]:
    """Version of :func:`filter_items` that uses caller-supplied *session*.

    It mirrors the behaviour of :func:`filter_items` but avoids opening its
    own session – useful when the caller already controls the transaction
    scope (e.g. within a Flask request context)."""

    filters = filters or {}
    event_filters = event_filters or {}

    stmt = (
        select(Item)
        .join(Item.event)
        .options(joinedload(Item.event))
        .offset(offset)
    )
    if order_desc:
        stmt = stmt.order_by(Item.id.desc())
    else:
        stmt = stmt.order_by(Item.id)

    stmt = _apply_item_filters(stmt, filters)
    stmt = _apply_event_filters(stmt, event_filters)

    if event_min_ts is not None:
        stmt = stmt.filter(Event.timestamp >= event_min_ts)
    if event_max_ts is not None:
        stmt = stmt.filter(Event.timestamp <= event_max_ts)
    if limit is not None:
        stmt = stmt.limit(limit)

    return list(session.scalars(stmt))


def update_item(item_id: int, **changes) -> bool:
    """Update mutable fields (``name``, ``code``, ``count``) for an Item."""
    allowed = {"name", "code", "count"}
    invalid = set(changes) - allowed
    if invalid:
        raise ValueError(f"Invalid Item fields: {', '.join(invalid)}")

    with get_session() as session:
        item = session.get(Item, item_id)
        if item is None:
            return False
        for key, value in changes.items():
            setattr(item, key, value)
        session.add(item)
        return True


def delete_item(item_id: int) -> bool:
    """Delete an Item. Returns *True* if the Item existed."""
    with get_session() as session:
        item = session.get(Item, item_id)
        if item is None:
            return False
        session.delete(item)
        return True


# ---------------------------------------------------------------------------
# Additional helper – mark Event inactive
# ---------------------------------------------------------------------------


def close_event(event_id: int) -> None:
    """Set is_active=False for the given event id (no-op if already closed)."""
    with get_session() as session:
        event = session.get(Event, event_id)
        if event is None:
            return
        if event.is_active:
            event.is_active = False
            session.add(event)
