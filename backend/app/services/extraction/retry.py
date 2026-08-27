"""Tiny retry helper for external calls (CU + GPT).

Ported from pre-qualification-agent (services/analyzer/retry.py): transient
Azure/network failures retry with exponential backoff; the last failure is
re-raised unchanged so call sites keep their error contract.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

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
            # Visible in the logs: a burst of these means the Azure deployment
            # is throttling (429) and the reader's concurrency is too high.
            logger.warning("retry %d/%d after %s: %s", attempt, attempts, type(exc).__name__, str(exc)[:200])
            time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc
