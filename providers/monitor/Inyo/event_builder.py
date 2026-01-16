# monitors/Inyo/event_builder.py
from __future__ import annotations

import time
from datetime import datetime, UTC
from typing import List, Dict, Any, Optional

from lib.notification_event import (
    NotificationEvent,
    MonitorDescriptor,
    Severity,
    Link,
)
from .inyo_event import InyoAvailabilityEvent, InyoChange
from lib.aes_token import ConfidentialTokenService
from providers.notification.Base.main import BaseNotificationProvider


class InyoEventBuilder:
    """Build either an Inyo-specific or generic NotificationEvent."""

    INYO_SLUG = "inyo.availability.update"

    # ------------------------------------------------------------------ #
    def __init__(
            self,
            *,
            version: str = "1.0.0",
            auto_reserve_base_url: Optional[str] = None,
            aes_token_service: Optional[ConfidentialTokenService] = None,
            token_kid: int = 0,
            token_ttl_seconds: int = 3_600,
            max_permits_per_click: int = 15,
    ):
        self.version = version
        self.auto_reserve_base_url = auto_reserve_base_url
        self.aes_token_service = aes_token_service
        self.token_kid = token_kid
        self.token_ttl_seconds = token_ttl_seconds
        self.max_permits_per_click = max_permits_per_click

    # ------------------------------------------------------------------ #
    def build(
            self,
            diffs: List[Dict[str, Any]],
            target: str,
            provider: BaseNotificationProvider,
    ):
        # ---- aggregate diff rows, make InyoChange objects ----------
        changes: List[InyoChange] = []
        generic_changes: List[Dict[str, Any]] = []
        auto_reserved = False

        for day in diffs:
            date = day["date"]
            for permit in day["permits"]:
                new_remaining = permit["new_remaining"]
                diff = permit["diff"]
                old_remaining = new_remaining - diff
                code = permit.get("code")
                name = permit.get("name")

                # --- optional ATC token --------------------------------
                token = None
                if (
                        self.auto_reserve_base_url and
                        self.aes_token_service and
                        new_remaining > 0
                ):
                    payload = {
                        "date": date,
                        "target": target,
                        "permit": code,
                        "count": min(new_remaining, self.max_permits_per_click),
                    }
                    token = self.aes_token_service.issue_token(
                        payload,
                        ttl_seconds=self.token_ttl_seconds,
                        kid=self.token_kid,
                    )

                # objects for both schemas
                changes.append(InyoChange(
                    date=date,
                    target=target,
                    regular_atc_url=f"https://www.recreation.gov/permits/{target}/registration/detailed-availability",
                    permit_code=code,
                    permit_name=name,
                    old_remaining=old_remaining,
                    new_remaining=new_remaining,
                    diff=diff,
                    token=token,
                    ttl=self.token_ttl_seconds,
                ))
                generic = {
                    "date": date,
                    "permit_type": name,
                    "new_availability": new_remaining,
                    "Regular ATC:": f"https://www.recreation.gov/permits/{target}/registration/detailed-availability",
                }

                if token:
                    generic['Auto-Reserve (expires in 30m)'] = f"{self.auto_reserve_base_url}?token={token}"

                generic_changes.append(generic)

        # ---- try Inyo-specific event first -------------------------
        inyo_evt = InyoAvailabilityEvent.build(
            changes,
            auto_reserve_base_url=self.auto_reserve_base_url,
            version=self.version,
        )

        if provider.supports_event(inyo_evt):
            return inyo_evt

        # ---- provider can’t handle it → generic fallback -------------
        sev = Severity.SUCCESS if any(c["new_availability"] > 0 for c in generic_changes) else Severity.INFO
        return NotificationEvent(
            monitor=MonitorDescriptor(
                name="Inyo NF Permit Monitor",
                event_type="availability.change",
                version=self.version,
            ),
            summary="Permit availability changed",
            severity=sev,
            data={
                "changes": generic_changes,
                "location": "Inyo NF",
            }
        )
