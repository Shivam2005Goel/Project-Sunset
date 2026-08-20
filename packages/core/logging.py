"""Structured logging.

Every line carries `estate_id` and `institution_id` where they exist, because when you
are debugging a fleet of 23 agents at 3am on Day 9, a log line without a subject is
noise. Uses structlog when installed, falls back to stdlib JSON otherwise - the fallback
matters because local mode must run with zero optional dependencies.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from packages.core.clock import iso

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("aftercare")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
    _CONFIGURED = True


class BoundLogger:
    """A logger with sticky context fields."""

    def __init__(self, name: str, context: dict[str, Any] | None = None) -> None:
        _configure()
        self._name = name
        self._log = logging.getLogger(f"aftercare.{name}")
        self._context = dict(context or {})

    def bind(self, **fields: Any) -> BoundLogger:
        merged = {**self._context, **{k: v for k, v in fields.items() if v is not None}}
        return BoundLogger(self._name, merged)

    def _emit(self, level: int, event: str, /, **fields: Any) -> None:
        # A caller logging an FSM transition naturally wants a field called "event".
        # That is also the name of the message key, so move it aside rather than let
        # one silently shadow the other.
        if "event" in fields:
            fields["event_detail"] = fields.pop("event")
        payload = {
            "ts": iso(),
            "level": logging.getLevelName(level).lower(),
            "logger": self._name,
            "event": event,
            **self._context,
            **{k: v for k, v in fields.items() if v is not None},
        }
        self._log.log(level, json.dumps(payload, default=str))

    def debug(self, event: str, /, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, /, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, /, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, /, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)


def get_logger(name: str, **context: Any) -> BoundLogger:
    return BoundLogger(name, context)


def set_level(level: int | str) -> None:
    _configure()
    logging.getLogger("aftercare").setLevel(level)
