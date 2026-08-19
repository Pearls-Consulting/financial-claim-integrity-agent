"""Tiny retry helper for external calls (CU + GPT).

Ported from pre-qualification-agent (services/analyzer/retry.py): transient
Azure/network failures retry with exponential backoff; the last failure is
re-raised unchanged so call sites keep their error contract.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def with_retries(fn: Callable[[], T], *, attempts: int = 3, backoff_seconds: float = 1.5) -> T:
    attempts = max(1, int(attempts))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc
