"""Size-rotated JSONL log of every decision cycle.

One line per tick, whether or not a recommendation was emitted, so the log can
later be compared with a fixed-time baseline. Rotation is delegated to
:class:`logging.handlers.RotatingFileHandler`, which handles the rollover and
backup numbering.
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional


class DecisionLog:
    """Append JSON objects to a size-rotated ``.jsonl`` file."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
        level: int = logging.INFO,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger(f"decision_log.{id(self)}")
        self._logger.setLevel(level)
        self._logger.propagate = False

        handler = RotatingFileHandler(
            self.path,
            maxBytes=max(0, max_bytes),
            backupCount=max(0, backup_count),
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._handler: Optional[RotatingFileHandler] = handler

    def write(self, entry: dict[str, Any]) -> None:
        """Serialise one decision-cycle entry as a single line."""
        self._logger.info(
            json.dumps(entry, separators=(",", ":"), ensure_ascii=False, default=str)
        )

    def close(self) -> None:
        handler = self._handler
        if handler is None:
            return
        self._logger.removeHandler(handler)
        handler.close()
        self._handler = None

    def __enter__(self) -> "DecisionLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
