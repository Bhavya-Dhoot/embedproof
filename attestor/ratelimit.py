"""In-memory token bucket per (group, client IP). Single-process prototype only."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable


class RateLimiter:
    """Each key owns a bucket of `burst` tokens refilled at `per_sec`; a request costs one token.

    The key table is bounded (`max_keys`, oldest evicted) so spoofed source addresses cannot
    be used to exhaust memory.
    """

    def __init__(self, burst: float = 20, per_sec: float = 2, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic):
        if burst < 1 or per_sec <= 0:
            raise ValueError("burst must be >= 1 and per_sec > 0")
        self.burst, self.per_sec, self.max_keys, self._clock = float(burst), float(per_sec), max_keys, clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Consume a token. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.per_sec)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)  # re-insert as most recently used
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return allowed, 0.0 if allowed else (1.0 - tokens) / self.per_sec
