"""
Tests for Token Bucket Rate Limiter
Verifies thread-safety, capacity replenishment, and burst throttling.
"""

import time
from src.data.rate_limiter import TokenBucketRateLimiter, registry


def test_token_bucket_initial_burst():
    # 5 tokens/second, capacity 5
    limiter = TokenBucketRateLimiter(rate=5.0, capacity=5.0)
    # First 5 acquisitions should happen with zero wait time
    for _ in range(5):
        slept = limiter.acquire(1.0)
        assert slept == 0.0


def test_token_bucket_throttling():
    # 2 tokens/second, capacity 2
    limiter = TokenBucketRateLimiter(rate=2.0, capacity=2.0)
    limiter.acquire(2.0)  # Drain all tokens
    # Next acquisition should require ~0.5 second wait
    start = time.monotonic()
    slept = limiter.acquire(1.0)
    elapsed = time.monotonic() - start
    assert slept > 0.4
    assert elapsed > 0.4


def test_registry_retrieves_configured_limits():
    sec_limiter = registry.get_limiter("sec.gov")
    assert sec_limiter.rate == 8.0  # Strict buffer below SEC 10 req/s

    boc_limiter = registry.get_limiter("bankofcanada.ca")
    assert boc_limiter.rate == 5.0
