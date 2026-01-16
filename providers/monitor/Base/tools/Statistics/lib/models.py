from datetime import datetime, date as dt_date
from typing import List

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Tracks whether the event is still actively receiving observations
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # one-to-many
    items: Mapped[list["Item"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return (
            f"Event(id={self.id!r}, source={self.source!r}, timestamp={self.timestamp!r}, "
            f"is_active={self.is_active!r})"
        )


class Item(Base):
    __tablename__ = "item"

    id: Mapped[int] = mapped_column(primary_key=True)

    # FK column …
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))

    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(128))
    count: Mapped[int] = mapped_column(Integer)

    # Timestamp when this particular observation was recorded
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # Date on which the item/permit will be consumed (e.g., entry date)
    date: Mapped[dt_date] = mapped_column(Date)

    # …and relationship back to Event
    event: Mapped["Event"] = relationship(back_populates="items")

    def __repr__(self):
        return (
            f"Item(id={self.id!r}, name={self.name!r}, count={self.count!r}, "
            f"timestamp={self.timestamp!r})"
        )
