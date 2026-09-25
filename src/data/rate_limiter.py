"""
Token Bucket Rate Limiter
Enforces precise rate-limiting across free official APIs.
Guarantees SEC EDGAR 8 req/s, FRED 100 req/min, and BoC 5 req/s.
"""

import time
import threading
import asyncio
from typing import Dict


class TokenBucketRateLimiter:
    """
    Thread-safe and async-safe token bucket rate limiter.
    """
    def __init__(self, rate: float, capacity: float = None):
        """
        rate: tokens added per second
        capacity: maximum burst capacity (defaults to 1.5 * rate)
        """
        self.rate = float(rate)
        self.capacity = float(capacity) if capacity else max(1.0, float(rate))
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def _replenish(self):
        now = time.monotonic()
        delta = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + delta * self.rate)
        self.last_update = now

    def acquire(self, tokens: float = 1.0) -> float:
        """
        Synchronous acquisition. Blocks until tokens are available.
        Returns total sleep duration.
        """
        total_slept = 0.0
        while True:
            with self.lock:
                self._replenish()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return total_slept
                needed = tokens - self.tokens
                wait_time = needed / self.rate

            time.sleep(wait_time)
            total_slept += wait_time

    async def acquire_async(self, tokens: float = 1.0) -> float:
        """
        Asynchronous acquisition. Yields control until tokens are available.
        """
        total_slept = 0.0
        while True:
            with self.lock:
                self._replenish()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return total_slept
                needed = tokens - self.tokens
                wait_time = needed / self.rate

            await asyncio.sleep(wait_time)
            total_slept += wait_time


class RateLimiterRegistry:
    """
    Singleton registry holding dedicated limiters per API host.
    """
    def __init__(self):
        self._limiters: Dict[str, TokenBucketRateLimiter] = {
            "sec.gov": TokenBucketRateLimiter(rate=8.0, capacity=8.0),       # SEC: max 10/s -> target 8/s
            "bankofcanada.ca": TokenBucketRateLimiter(rate=5.0, capacity=5.0), # BoC Valet: 5/s
            "stlouisfed.org": TokenBucketRateLimiter(rate=1.5, capacity=3.0),  # FRED: 100/min ≈ 1.66/s
            "api.groq.com": TokenBucketRateLimiter(rate=0.4, capacity=2.0),    # Groq free: 25/min ≈ 0.41/s
        }
        self._lock = threading.Lock()

    def get_limiter(self, host: str, default_rate: float = 2.0) -> TokenBucketRateLimiter:
        with self._lock:
            if host not in self._limiters:
                self._limiters[host] = TokenBucketRateLimiter(rate=default_rate)
            return self._limiters[host]


# Global registry singleton
registry = RateLimiterRegistry()
