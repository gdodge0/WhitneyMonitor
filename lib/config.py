from __future__ import annotations
"""Flexible configuration loader for Whitney‑style monitor configs.

Key features
============
* **Loose schema** – only core fields validated (secret_key, redis,
  service, monitor) plus *cooldown* and *targets* rules.
* **Env‑var interpolation** – ${VAR} → os.environ['VAR'].
* **Cached date helpers** – DateRanges.complete_months() is pre‑computed.
* **Target namespace** – provider.targets["445860"] returns the DateRanges
  object for that target ID (and the mapping itself is iterable like a dict).
* **Round‑trip safe** – Config.to_yaml() emits the original structure.

The previous *date_ranges* key has been removed in favour of a nested
*targets* section:

```yaml
monitor:
  providers:
    - Inyo
  inyo:
    cooldown: 15 # seconds
    targets:
      - "445860":
          - start: "2025-07-01"
            end: "2025-09-20"
            enabled_days: [ Mon, Tue, Wed, Thu, Fri, Sat, Sun ]
      - "233262":
          - start: "2025-07-01"
            end: "2025-09-20"
            enabled_days: [ Mon, Tue, Wed, Thu, Fri, Sat, Sun ]
```
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, overload, Iterable
import os
import re
import yaml
import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

__all__ = [
    "RedisConfig",
    "ServiceConfig",
    "DateRanges",
    "MonitorProvider",
    "Config",
]

# ---------------------------------------------------------------------------
# Env‑var interpolation
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\${([^}:]+)(?::([^}]*))?}")


def _interpolate(value: Any) -> Any:
    """Recursively substitute ${VARS} with their os.environ values."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2)
            return os.getenv(var, default or "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# date‑processing helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_BITMASK_RE = re.compile(r"[01]{7}$")
_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _validate_ranges(date_ranges: Iterable[Iterable[str]]) -> None:
    """Raise ValueError if *date_ranges* (triples) are malformed."""
    if not isinstance(date_ranges, (list, tuple)):
        raise ValueError("Top‑level target ranges must be a list/tuple")
    for idx, triple in enumerate(date_ranges):
        if not (isinstance(triple, (list, tuple)) and len(triple) == 3):
            raise ValueError(f"Range #{idx} should be [start, end, bitmask]")
        start_s, end_s, mask = triple
        for label, s in (("start", start_s), ("end", end_s)):
            if not _DATE_RE.fullmatch(s):
                raise ValueError(f"Range #{idx} – {label} '{s}' not yyyy‑mm‑dd")
        try:
            start = date.fromisoformat(start_s)
            end = date.fromisoformat(end_s)
        except ValueError as e:
            raise ValueError(f"Range #{idx} – invalid date: {e}") from e
        if start > end:
            raise ValueError(f"Range #{idx} – start after end")
        if not _BITMASK_RE.fullmatch(mask):
            raise ValueError(f"Range #{idx} – bitmask must be 7 chars of 0/1")


def _complete_months(triples: List[List[str]]) -> List[List[str]]:
    """Return a list of [month_start, month_end] windows covering *triples*."""
    months: set[tuple[date, date]] = set()
    for start_s, end_s, _ in triples:
        start, end = date.fromisoformat(start_s), date.fromisoformat(end_s)
        y, m = start.year, start.month
        while True:
            m_start = date(y, m, 1)
            m_end = date(y, m, calendar.monthrange(y, m)[1])
            if m_end >= start and m_start <= end:
                months.add((m_start, m_end))
            if (y, m) >= (end.year, end.month):
                break
            m = m + 1 if m < 12 else 1
            y = y if m != 1 else y + 1
    return [[s.isoformat(), e.isoformat()] for s, e in sorted(months)]


def _date_matches(day_s: str, triples: List[List[str]], *, match_previous_dates: bool = False) -> bool:
    """Return **True** when *day_s* is a future date that is enabled.

    A date is considered enabled if it satisfies *both*:
    (a) its weekday bit is **1** in at least one triple's mask, and
    (b) the date lies within that triple's [start, end] inclusive window.

    When *match_previous_dates* is **False** (default) any date that is
    **today or earlier** in the Pacific time zone is automatically rejected.
    """
    d = date.fromisoformat(day_s)

    if not match_previous_dates:
        today_pt = datetime.now(ZoneInfo("America/Los_Angeles")).date()
        if d <= today_pt:
            return False

    bit_idx = d.weekday()  # 0=Mon … 6=Sun
    for s, e, mask in triples:
        if (
            mask[bit_idx] == "1"
            and date.fromisoformat(s) <= d <= date.fromisoformat(e)
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# DateRanges wrapper (per‑target)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DateRanges:
    """Lightweight wrapper around a list of date‑range dictionaries."""

    _yaml: List[Dict[str, Any]]
    _triples: List[List[str]] = field(init=False, repr=False)
    _months: List[List[str]] = field(init=False, repr=False)

    def __post_init__(self):
        triples: List[List[str]] = []
        for idx, item in enumerate(self._yaml or []):
            if not isinstance(item, dict):
                raise ValueError(f"date range #{idx} must be a mapping")
            start, end = item.get("start"), item.get("end")
            enabled_days = item.get("enabled_days", [])
            if not (start and end):
                raise ValueError(f"range #{idx} requires 'start' & 'end'")
            if not isinstance(enabled_days, list):
                raise ValueError(f"range #{idx}.enabled_days must be list")
            mask = "".join("1" if d in enabled_days else "0" for d in _DAY_ORDER)
            triples.append([start, end, mask])
        _validate_ranges(triples)
        object.__setattr__(self, "_triples", triples)
        object.__setattr__(self, "_months", _complete_months(triples))

    # Public API ------------------------------------------------------
    def validate(self) -> None:
        _validate_ranges(self._triples)

    def complete_months(self) -> List[List[str]]:
        """Return pre‑calculated complete months covering all ranges."""
        return self._months

    def date_matches(self, day_s: str) -> bool:
        """Return **True** if *day_s* meets the enabled rules."""
        return _date_matches(day_s, self._triples)

    # Python sugar ----------------------------------------------------
    def __contains__(self, day: str) -> bool:
        return self.date_matches(day)

    def __iter__(self):
        return iter(self._triples)

    def __len__(self):
        return len(self._triples)

    def __repr__(self):
        return f"DateRanges({self._triples!r})"


# ---------------------------------------------------------------------------
# Simple wrappers for dot‑access dicts
# ---------------------------------------------------------------------------

class _DotDict(MutableMapping[str, Any]):
    """Expose dict keys as attributes with dot‑notation."""

    __slots__ = ("_data",)

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    # Mapping protocol ----------------------------------------------
    def __getitem__(self, k):
        return self._data[k]

    def __setitem__(self, k, v):
        self._data[k] = v

    def __delitem__(self, k):
        del self._data[k]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    # Attribute access ----------------------------------------------
    def __getattr__(self, item):
        try:
            return self._data[item]
        except KeyError as e:
            raise AttributeError(item) from e

    def __repr__(self):
        return f"{self.__class__.__name__}({self._data!r})"


class NotificationBlock(_DotDict):
    """Typed alias for readability – no extra behaviour."""
    pass


class ToolsNamespace(_DotDict):
    """Expose each configured tool as an attribute of the provider."""

    def __init__(self, provider_raw: Dict[str, Any]):
        tool_names = provider_raw.get("tools", [])
        mapping: Dict[str, Any] = {}
        for name in tool_names:
            # Case‑insensitive lookup:
            for k, v in provider_raw.items():
                if k.lower() == name.lower():
                    mapping[name] = v
                    break
            else:
                mapping[name] = None  # declared but not configured
        super().__init__(mapping)

    # Attr fallback should be case‑insensitive
    def __getattr__(self, item):
        for k in self._data:
            if k.lower() == item.lower():
                return self._data[k]
        raise AttributeError(item)


# ---------------------------------------------------------------------------
# Core config structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RedisConfig:
    broker_url: str
    result_backend: str
    task_log_backend: str


@dataclass(slots=True)
class ServiceConfig:
    base_url: str


@dataclass(slots=True)
class MonitorProvider:
    """Wrapper around a single monitor provider block from YAML."""

    name: str
    raw: Dict[str, Any]
    _targets_cache: Optional[Dict[str, DateRanges]] = field(init=False, default=None, repr=False)
    _tools_ns: Optional[ToolsNamespace] = field(init=False, default=None, repr=False)

    # Basic fields ----------------------------------------------------
    @property
    def cooldown(self) -> int:
        return self.raw["cooldown"]

    # Notifications ---------------------------------------------------
    @property
    def notifications(self) -> Dict[str, NotificationBlock]:
        sec = self.raw.get("notifications", {})
        return {k: NotificationBlock(v) for k, v in sec.items() if k != "providers"}

    def notification(self, name: str) -> NotificationBlock:
        try:
            return self.notifications[name]
        except KeyError as e:
            raise KeyError(f"Notification provider '{name}' not found") from e

    # Targets (new) ---------------------------------------------------
    @property
    def targets(self) -> Dict[str, DateRanges]:
        """Return mapping of target‑ID → DateRanges, lazily loaded."""
        if self._targets_cache is None:
            tgt_list = self.raw.get("targets", [])
            if not isinstance(tgt_list, (list, tuple)):
                raise ValueError("'targets' must be a list of single‑key mappings")
            mapping: Dict[str, DateRanges] = {}
            for idx, item in enumerate(tgt_list):
                if not (isinstance(item, dict) and len(item) == 1):
                    raise ValueError(f"targets[{idx}] must be a single‑key mapping")
                target_id, ranges_yaml = next(iter(item.items()))
                mapping[target_id] = DateRanges(ranges_yaml or [])
            self._targets_cache = mapping
        return self._targets_cache

    def target(self, target_id: str) -> DateRanges:
        """Convenience accessor – raises KeyError if not found."""
        try:
            return self.targets[target_id]
        except KeyError as e:
            raise KeyError(f"Target '{target_id}' not found in provider '{self.name}'") from e

    # Tools -----------------------------------------------------------
    @property
    def tools(self) -> ToolsNamespace:
        if self._tools_ns is None:
            self._tools_ns = ToolsNamespace(self.raw)
        return self._tools_ns

    # Legacy helpers --------------------------------------------------
    @property
    def tool_names(self) -> List[str]:
        return self.raw.get("tools", [])

    def tool_config(self, name: str) -> Optional[Dict[str, Any]]:
        return getattr(self.tools, name, None)

    # Mapping passthrough --------------------------------------------
    def __getitem__(self, k):
        return self.raw[k]

    def __iter__(self):
        return iter(self.raw)

    def __len__(self):
        return len(self.raw)

    def __repr__(self):
        return f"MonitorProvider(name={self.name!r})"


@dataclass(slots=True)
class MonitorConfig:
    providers: Dict[str, MonitorProvider]

    def __iter__(self):
        return iter(self.providers.values())

    @overload
    def __getitem__(self, name: str) -> MonitorProvider: ...

    @overload
    def __getitem__(self, idx: int) -> MonitorProvider: ...

    def __getitem__(self, key):
        return list(self.providers.values())[key] if isinstance(key, int) else self.providers[key]


# ---------------------------------------------------------------------------
# Main Config class
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Config:
    secret_key: str
    IPC_key: str
    redis: RedisConfig
    service: ServiceConfig
    heartbeat_url: Optional[str]
    monitor: MonitorConfig
    extra: Dict[str, Any] = field(default_factory=dict)

    # Factory ---------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        data = _interpolate(raw)
        # Required high‑level keys
        for k in ("secret_key", "redis", "service", "monitor", "IPC_key"):
            if k not in data:
                raise ValueError(f"Missing required key '{k}'")

        # Simple nested sections
        redis_cfg = RedisConfig(**data["redis"])
        svc_cfg = ServiceConfig(**data["service"])

        # Providers ---------------------------------------------------
        mon_sec = data["monitor"]
        names = mon_sec.get("providers", [])
        if not names:
            raise ValueError("monitor.providers needs at least one provider")

        providers: Dict[str, MonitorProvider] = {}
        for name in names:
            block = mon_sec.get(name.lower())
            if not block:
                raise ValueError(f"Block for provider '{name.lower()}' missing")

            # cooldown ------------------------------------------------
            if not isinstance(block.get("cooldown"), int) or block["cooldown"] <= 0:
                raise ValueError(f"Provider '{name}' needs positive integer 'cooldown'")

            # notifications.providers --------------------------------
            if not block.get("notifications", {}).get("providers"):
                raise ValueError(f"Provider '{name}' must list notifications.providers")

            # targets -------------------------------------------------
            tgt_list = block.get("targets")
            if not tgt_list:
                raise ValueError(f"Provider '{name}' must define 'targets'")
            if not isinstance(tgt_list, (list, tuple)):
                raise ValueError(f"Provider '{name}'.targets must be a list of mappings")

            for idx, item in enumerate(tgt_list):
                if not (isinstance(item, dict) and len(item) == 1):
                    raise ValueError(f"Provider '{name}' targets[{idx}] must be single‑key mapping")
                target_id, ranges_yaml = next(iter(item.items()))
                try:
                    DateRanges(ranges_yaml or [])
                except ValueError as e:
                    raise ValueError(f"Provider '{name}' target '{target_id}': {e}") from e

            providers[name] = MonitorProvider(name, block)

        mon_cfg = MonitorConfig(providers)

        # Anything else at root level is treated as *extra*
        extra_root = {k: v for k, v in data.items() if k not in {
            "secret_key", "redis", "service", "heartbeat_url", "monitor", "IPC_key"
        }}

        return cls(
            data["secret_key"],
            data["IPC_key"],
            redis_cfg,
            svc_cfg,
            data.get("heartbeat_url"),
            mon_cfg,
            extra_root,
        )

    @classmethod
    def from_yaml(cls, src: str | Path | bytes) -> "Config":
        if isinstance(src, (str, Path)):
            fp = Path(src).open("r", encoding="utf-8") if isinstance(src, Path) else open(src, "r", encoding="utf-8")
            with fp:
                raw = yaml.safe_load(fp)
        else:
            raw = yaml.safe_load(src)
        return cls.from_dict(raw)

    # Serialisation ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "secret_key": self.secret_key,
            "IPC_key": self.IPC_key,
            "redis": asdict(self.redis),
            "service": asdict(self.service),
            "heartbeat_url": self.heartbeat_url,
            "monitor": {
                "providers": list(self.monitor.providers),
                **{k.lower(): v.raw for k, v in self.monitor.providers.items()},
            },
            **self.extra,
        }

    def to_yaml(self) -> str:
        """Return round‑trippable YAML string."""
        return yaml.dump(self.to_dict(), sort_keys=False)

    # Helper ----------------------------------------------------------
    def provider(self, name: str) -> MonitorProvider:
        return self.monitor.providers[name]

    # Demo CLI --------------------------------------------------------
    @staticmethod
    def _demo_cli() -> None:  # pragma: no cover
        import argparse, sys, pprint

        p = argparse.ArgumentParser(description="Demo loader for new 'targets' format")
        p.add_argument("config")
        args = p.parse_args()

        cfg = Config.from_yaml(Path(args.config))
        for pvr in cfg.monitor:
            print(f"Provider '{pvr.name}' targets:")
            for tid, dr in pvr.targets.items():
                print(f"  - {tid}: {len(dr)} range(s)")
            print("  Tools:", list(pvr.tools))
        sys.exit()


if __name__ == "__main__":
    Config._demo_cli()
