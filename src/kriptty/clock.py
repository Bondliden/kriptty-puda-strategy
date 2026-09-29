"""Reloj del sistema. En producción es el reloj real; el backtester lo fija
en cada paso para que las estrategias vean el tiempo simulado."""
from __future__ import annotations

import time as _time
from datetime import UTC, datetime

_override: float | None = None


def now() -> float:
    """Segundos epoch (UTC)."""
    return _override if _override is not None else _time.time()


def utcnow() -> datetime:
    return datetime.fromtimestamp(now(), UTC)


def set_time(ts: float | None) -> None:
    """Fija el reloj simulado (None vuelve al reloj real)."""
    global _override
    _override = ts
