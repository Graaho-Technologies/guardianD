from __future__ import annotations

import functools
import time
from typing import Tuple, Type

from .logger import get_logger

_log = get_logger(__name__)


def retry(
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        wait = backoff_seconds * (2 ** (attempt - 1))
                        _log.debug(
                            "retry %s/%s for %s after %.1fs: %s",
                            attempt, max_attempts, fn.__name__, wait, exc,
                        )
                        time.sleep(wait)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
