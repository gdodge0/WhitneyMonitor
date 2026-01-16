# providers/monitor/Inyo/inyo_event.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from lib.notification_event import NotificationEvent, Severity, MonitorDescriptor


# ────────────────────────────────────────────────────────────────────
# Inyo-flavoured “change” row
# ────────────────────────────────────────────────────────────────────
@dataclass
class InyoChange:
    date: str  # "YYYY-MM-DD"
    target: str
    regular_atc_url: str
    permit_code: str
    permit_name: str
    old_remaining: int
    new_remaining: int
    diff: int
    token: Optional[str] = None,
    ttl: Optional[int] = None


# ────────────────────────────────────────────────────────────────────
# Concrete event class
# ────────────────────────────────────────────────────────────────────
@dataclass
class InyoAvailabilityEvent(NotificationEvent):
    """Tight contract the Inyo Discord parser can rely on."""
    changes: List[InyoChange] = field(default_factory=list)
    auto_reserve_base_url: Optional[str] = None  # “…/atc” (no ?token=)

    @staticmethod
    def build(
            changes: List[InyoChange],
            *,
            auto_reserve_base_url: Optional[str] = None,
            version: str = "1.0.0",
    ) -> "InyoAvailabilityEvent":
        return InyoAvailabilityEvent(
            monitor=MonitorDescriptor(
                name="Inyo NF Permit Monitor",
                event_type="inyo.availability.update",
                version=version,
            ),
            summary="Inyo permit availability changed",
            severity=Severity.SUCCESS
            if any(c.diff > 0 for c in changes)
            else Severity.INFO,
            data={},  # unused in the custom path
            links=[],  # unused (link logic handled inside parser)
            changes=changes,
            auto_reserve_base_url=auto_reserve_base_url,
        )
