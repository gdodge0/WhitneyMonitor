from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class Severity(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class Link:
    label: str
    url: str
    expires_at: Optional[datetime] = None


@dataclass
class MonitorDescriptor:
    name: str
    event_type: str           # e.g. "availability.change"
    version: str              # "1.2.0"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationEvent:
    monitor: MonitorDescriptor
    summary: str
    severity: Severity
    data: Dict[str, Any]
    links: List[Link] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.utcnow)
