"""Thread-safe, bounded feeds shared by the controller and the GUI.

No Qt imports: the GUI polls these feeds from its own thread and the core only
appends under a short lock. Both buffers are bounded, so a frozen GUI cannot
grow memory without limit.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class LogLine:
    """One captured log record, already rendered."""

    seq: int
    ts: float
    level: str
    logger: str
    message: str


class LogBroadcaster(logging.Handler):
    """A bounded logging handler the GUI can poll without touching loggers."""

    def __init__(self, capacity: int = 5000, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._capacity = max(1, capacity)
        self._lines: deque[LogLine] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self._closed = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._lines)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
            return

        with self._lock:
            if self._closed:
                return
            self._seq += 1
            self._lines.append(
                LogLine(
                    seq=self._seq,
                    ts=record.created,
                    level=record.levelname,
                    logger=record.name,
                    message=message,
                )
            )

    def drain(self, since_seq: int = 0) -> tuple[list[LogLine], int]:
        """Return lines newer than ``since_seq`` and the newest sequence."""
        with self._lock:
            latest = self._seq
            lines = [line for line in self._lines if line.seq > since_seq]
        return lines, latest

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lines.clear()
        super().close()


@dataclass(frozen=True)
class UiEvent:
    """A compact event row for the Live Events table."""

    seq: int
    ts: float
    kind: str
    subject: str
    action: str
    result: str
    detail: Mapping[str, Any] = field(default_factory=dict)


class EventFeed:
    """Bounded feed of decision, gate and camera events."""

    def __init__(self, capacity: int = 1000) -> None:
        self._capacity = max(1, capacity)
        self._events: deque[UiEvent] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self._closed = False

    def publish(
        self,
        kind: str,
        subject: str,
        action: str,
        result: str = "",
        detail: Optional[Mapping[str, Any]] = None,
    ) -> Optional[UiEvent]:
        with self._lock:
            if self._closed:
                return None
            self._seq += 1
            event = UiEvent(
                seq=self._seq,
                ts=time.time(),
                kind=kind,
                subject=subject,
                action=action,
                result=result,
                detail=MappingProxyType(dict(detail or {})),
            )
            self._events.append(event)
        return event

    def drain(self, since_seq: int = 0) -> tuple[list[UiEvent], int]:
        with self._lock:
            latest = self._seq
            events = [event for event in self._events if event.seq > since_seq]
        return events, latest

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._events.clear()
