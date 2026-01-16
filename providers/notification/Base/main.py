"""
BaseNotificationProvider
========================

• Auto-discovers custom “event → payload” parsers located in a sibling
  *parsers/* package (same package as the concrete provider).

    discord_provider.py
    parsers/
        ├─ __init__.py
        ├─ inyo_availability_update.py   (custom)
        └─ general.py                       (generic fallback)

• Public helpers:
    supports_event(evt)     → bool
    payloads_for_event(evt, **kwargs) → list[dict]   (calls parser.to_payloads)

Concrete providers still implement:
    async send_notification(evt)
    async send_log(meta)
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lib import config
from lib.notification_event import NotificationEvent


# ---------------------------------------------------------------------------
class BaseNotificationProvider:
    """Shared capability discovery + parser loading."""

    # Subclasses should live in a package with a *parsers* sub-package.
    PARSER_DIR: Optional[Path] = None
    PARSER_PACKAGE: Optional[str] = None

    # ------------- construction ----------------------------------------
    def __init__(self, cfg: config.NotificationBlock):
        self.cfg = cfg

        # Derive PARSER_DIR / PACKAGE lazily (once per subclass)
        cls = self.__class__
        if cls.PARSER_DIR is None or cls.PARSER_PACKAGE is None:
            mod = sys.modules[cls.__module__]
            provider_path = Path(mod.__file__).resolve()
            provider_pkg = cls.__module__.rsplit(".", 1)[0]          # e.g. lib.notifiers
            parsers_dir = provider_path.parent / "parsers"

            cls.PARSER_DIR = parsers_dir
            cls.PARSER_PACKAGE = f"{provider_pkg}.parsers"

    # ------------- capability helpers ----------------------------------
    @staticmethod
    def _slug(event_type: str) -> str:
        """Convert 'foo.bar.baz' → 'foo_bar_baz'."""
        return event_type.replace(".", "_")

    def supports_event(self, evt: NotificationEvent) -> bool:
        """True if a slug-specific parser file exists."""
        slug = self._slug(evt.monitor.event_type)
        return (self.PARSER_DIR / f"{slug}.py").exists()

    # ------------- internal loader -------------------------------------
    def _load_parser(self, evt: NotificationEvent) -> Callable[..., List[Dict]]:
        """
        Import and return `to_payloads` for the event.
        Falls back to *General.py* inside the same parsers package.
        """
        slug = self._slug(evt.monitor.event_type)
        try:
            module = importlib.import_module(f"{self.PARSER_PACKAGE}.{slug}")
        except ModuleNotFoundError:
            module = importlib.import_module(f"{self.PARSER_PACKAGE}.General")

        try:
            return getattr(module, "to_payloads")
        except AttributeError as e:  # pragma: no cover
            raise RuntimeError(f"Parser in {module.__name__} lacks to_payloads()") from e

    # ------------- public convenience ----------------------------------
    def payloads_for_event(
        self,
        evt: NotificationEvent,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Return a list of provider-specific payload dictionaries by delegating
        to the dynamically-loaded parser.
        Extra **kwargs are forwarded to the parser’s to_payloads().
        """
        parser = self._load_parser(evt)
        return parser(evt, **kwargs)

    # ------------- to be implemented by concrete providers -------------
    def send_notification(self, meta) -> None:  # noqa: D401
        raise NotImplementedError

    def send_log(self, meta) -> None:  # noqa: D401
        raise NotImplementedError
