import asyncio
import time
import logging
from typing import Dict
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""

    capacity: int
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self):
        self.tokens = float(self.capacity)
        self.last_refill = time.time()

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate

        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now

    async def can_consume(self, tokens: int = 1) -> bool:
        """Check if tokens can be consumed without actually consuming them."""
        async with self.lock:
            self._refill()
            return self.tokens >= tokens

    async def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if successful."""
        async with self.lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    async def time_until_available(self, tokens: int = 1) -> float:
        """Return seconds until requested tokens will be available."""
        async with self.lock:
            self._refill()
            if self.tokens >= tokens:
                return 0.0

            needed_tokens = tokens - self.tokens
            return needed_tokens / self.refill_rate

    async def available_tokens(self) -> int:
        """Get current number of available tokens."""
        async with self.lock:
            self._refill()
            return int(self.tokens)


class RateLimitError(Exception):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, limit_type: str, retry_after: float):
        self.limit_type = limit_type
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded for {limit_type}. Retry after {retry_after:.2f} seconds"
        )


class RateLimiter:
    """Rate limiter for TopstepX API with different buckets for different endpoints."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # TopstepX rate limits from documentation:
        # - General endpoints: 200 requests per 60 seconds
        # - History endpoints: 50 requests per 30 seconds
        self.buckets: Dict[str, TokenBucket] = {
            "general": TokenBucket(
                capacity=200,
                refill_rate=200.0 / 60.0,  # 3.33 tokens per second
            ),
            "history": TokenBucket(
                capacity=50,
                refill_rate=50.0 / 30.0,  # 1.67 tokens per second
            ),
        }

        # Mapping of API endpoints to rate limit buckets
        self.endpoint_mapping = {
            "/api/History/retrieveBars": "history",
            # All other endpoints use general bucket by default
        }

        # 429 response handling metrics
        self._metrics = {
            "general_rate_limited": 0,
            "history_rate_limited": 0,
            "total_429_responses": 0,
            "total_backoff_time": 0.0
        }

    def get_bucket_for_endpoint(self, endpoint: str) -> str:
        """Get the appropriate rate limit bucket for an endpoint."""
        for pattern, bucket in self.endpoint_mapping.items():
            if pattern in endpoint:
                return bucket
        return "general"  # Default bucket

    async def acquire(
        self, endpoint: str = "", tokens: int = 1, raise_on_limit: bool = False
    ) -> bool:
        """
        Acquire tokens for making a request.

        Args:
            endpoint: API endpoint path to determine which bucket to use
            tokens: Number of tokens to acquire (default: 1)
            raise_on_limit: If True, raise RateLimitError instead of waiting

        Returns:
            True if tokens were acquired

        Raises:
            RateLimitError: If raise_on_limit=True and rate limit exceeded
        """
        bucket_name = self.get_bucket_for_endpoint(endpoint)
        bucket = self.buckets[bucket_name]

        # Try to consume immediately
        if await bucket.consume(tokens):
            available_tokens = await bucket.available_tokens()
            self.logger.debug(
                f"Rate limit OK for {bucket_name}: {available_tokens} tokens remaining"
            )
            return True

        # Check how long we need to wait
        wait_time = await bucket.time_until_available(tokens)

        if raise_on_limit:
            raise RateLimitError(bucket_name, wait_time)

        # Wait for tokens to become available
        self.logger.warning(
            f"Rate limit hit for {bucket_name}. Waiting {wait_time:.2f} seconds..."
        )
        await asyncio.sleep(wait_time)

        # Try again after waiting
        if await bucket.consume(tokens):
            available_tokens = await bucket.available_tokens()
            self.logger.debug(
                f"Rate limit OK after wait for {bucket_name}: {available_tokens} tokens remaining"
            )
            return True

        # This shouldn't happen if our math is correct
        self.logger.error(f"Failed to acquire tokens for {bucket_name} after waiting")
        return False

    async def check_limit(self, endpoint: str = "", tokens: int = 1) -> tuple[bool, float]:
        """
        Check if request can be made without consuming tokens.

        Returns:
            (can_make_request, wait_time_if_not)
        """
        bucket_name = self.get_bucket_for_endpoint(endpoint)
        bucket = self.buckets[bucket_name]

        if await bucket.can_consume(tokens):
            return True, 0.0

        wait_time = await bucket.time_until_available(tokens)
        return False, wait_time

    async def get_status(self) -> Dict[str, Dict[str, float]]:
        """Get current status of all rate limit buckets."""
        status = {}
        for name, bucket in self.buckets.items():
            available_tokens = await bucket.available_tokens()
            status[name] = {
                "available_tokens": available_tokens,
                "capacity": bucket.capacity,
                "refill_rate_per_sec": bucket.refill_rate,
                "utilization_pct": (1 - available_tokens / bucket.capacity) * 100,
            }
        return status

    async def reset_bucket(self, bucket_name: str):
        """Reset a specific bucket to full capacity (for testing)."""
        if bucket_name in self.buckets:
            bucket = self.buckets[bucket_name]
            async with bucket.lock:
                bucket.tokens = float(bucket.capacity)
                bucket.last_refill = time.time()
            self.logger.info(f"Reset rate limit bucket: {bucket_name}")

    async def reset_all(self):
        """Reset all buckets to full capacity (for testing)."""
        for bucket_name in self.buckets:
            await self.reset_bucket(bucket_name)

    async def handle_429_response(self, response, endpoint: str):
        """Handle 429 Too Many Requests with documented backoff strategy."""
        bucket_name = self.get_bucket_for_endpoint(endpoint)
        
        # Parse Retry-After header, default to 1 second if not present
        retry_after = 1.0
        if hasattr(response, 'headers') and response.headers:
            try:
                retry_after_header = response.headers.get('Retry-After', '1')
                retry_after = float(retry_after_header)
            except (ValueError, TypeError):
                retry_after = 1.0
        
        # Add buffer and cap at 60 seconds per documentation
        wait_time = min(retry_after * 1.5, 60.0)
        
        # Update metrics
        self._metrics['total_429_responses'] += 1
        self._metrics['total_backoff_time'] += wait_time
        self._metrics[f"{bucket_name}_rate_limited"] += 1
        
        self.logger.warning(
            f"429 Rate limited on {endpoint} ({bucket_name} bucket), "
            f"waiting {wait_time:.2f}s (Retry-After: {retry_after}s)"
        )
        
        await asyncio.sleep(wait_time)
        return wait_time

    async def handle_429_with_exponential_backoff(self, endpoint: str, attempt: int = 0):
        """Handle 429 with exponential backoff when no response headers available."""
        bucket_name = self.get_bucket_for_endpoint(endpoint)
        
        # Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (capped)
        base_wait = 2 ** attempt
        wait_time = min(base_wait, 60.0)
        
        # Update metrics
        self._metrics['total_429_responses'] += 1
        self._metrics['total_backoff_time'] += wait_time
        self._metrics[f"{bucket_name}_rate_limited"] += 1
        
        self.logger.warning(
            f"Rate limited on {endpoint} ({bucket_name} bucket), "
            f"exponential backoff attempt {attempt + 1}, waiting {wait_time:.2f}s"
        )
        
        await asyncio.sleep(wait_time)
        return wait_time

    def get_429_metrics(self) -> Dict[str, float]:
        """Get 429 response handling metrics."""
        return dict(self._metrics)

    def reset_metrics(self):
        """Reset 429 handling metrics (for testing)."""
        for key in self._metrics:
            self._metrics[key] = 0 if isinstance(self._metrics[key], int) else 0.0
