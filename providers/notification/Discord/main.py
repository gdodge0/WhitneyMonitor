from __future__ import annotations
import importlib
import json
import time
from pathlib import Path
from typing import Any, List, Dict, Callable, Optional

from providers.notification.Base.main import BaseNotificationProvider
from lib.http_utils import fire_and_forget
from lib.notification_event import NotificationEvent
from providers.notification.Discord.parsers.general import to_payloads  # ⬅ generic fallback

PARSER_PACKAGE = "lib.notifiers.parsers"  # dotted-import path
PARSER_DIR = Path(__file__).with_suffix('').parent / "parsers"


# ---------------------------------------------------------------------------

def _slug(event_type: str) -> str:
    """Convert 'inyo.availability.update' → 'inyo_availability_update'."""
    return event_type.replace(".", "_")


class DiscordNotificationProvider(BaseNotificationProvider):

    def send_notification(self, meta: NotificationEvent) -> None:  # type: ignore[override]
        role_id: Optional[int] = self.cfg.get("role_id")

        # choose specific or generic parser
        if self.supports_event(meta):
            payloads = self._load_parser(meta)(meta, role_id=role_id)
        else:
            payloads = to_payloads(meta, role_id=role_id)

        for p in payloads:
            fire_and_forget("POST", url=self.cfg["event_hook"], json=p)

    def send_log(self, meta: Any) -> None:  # type: ignore[override]
        if not (self.cfg.get("enable_logs") and self.cfg.get("log_hook")):
            return
        content = (
            f"```json\n{json.dumps(meta, indent=2)}```"
            if isinstance(meta, (dict, list))
            else str(meta)
        )
        fire_and_forget("POST", url=self.cfg["log_hook"], json={"content": content})
