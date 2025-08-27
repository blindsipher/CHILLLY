"""Historical data fetcher for TopstepX API with EventBus integration."""

import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

try:
    from aiocache import Cache
except Exception:  # pragma: no cover - aiocache may not be installed
    Cache = None

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.core.topics import persist_bar_save
from topstepx_backend.networking.api_helpers import auth_headers, utc_now


@dataclass
class HistoricalRequest:
    """Configuration for a historical data request."""

    contract_id: str  # TopStepX uses contractId in API
    start_date: datetime
    end_date: datetime
    timeframe: str = "1m"  # Default to 1-minute bars
    priority: int = 1  # 1=high, 2=medium, 3=low


class HistoricalFetcher:
    """Fetches historical bar data from TopstepX API and publishes via EventBus."""

    _HISTORY_ENDPOINT = "/api/History/retrieveBars"

    def __init__(
        self,
        config: TopstepConfig,
        auth_manager: AuthManager,
        rate_limiter: RateLimiter,
        event_bus: EventBus,
    ):
        self.config = config
        self._auth_manager = auth_manager
        self._rate_limiter = rate_limiter
        self._event_bus = event_bus
        self.logger = logging.getLogger(__name__)

        # HTTP session for reuse
        self._session: Optional[aiohttp.ClientSession] = None

        # Cache setup (in-memory by default, optional Redis)
        if Cache and config.cache_backend == "redis":
            self._cache = Cache.from_url(config.redis_url)
            self._cache_is_async = True
        elif Cache:
            self._cache = Cache(Cache.MEMORY)
            self._cache_is_async = True
        else:  # Fallback simple dict cache
            self._cache = {}
            self._cache_is_async = False

        # Request queue for data collection
        self._request_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._active_requests: Dict[str, HistoricalRequest] = {}
        self._queue_overflows = 0

        # Collection state
        self._collection_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_hourly_collection = utc_now() - timedelta(hours=2)

        # Statistics
        self._stats = {
            "requests_processed": 0,
            "bars_fetched": 0,
            "bars_published": 0,
            "api_errors": 0,
            "last_collection": None,
        }

    async def start(self):
        """Initialize the HTTP session."""
        if not self._session or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=60)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
            )
            self.logger.debug("HistoricalFetcher session initialized")

    async def stop(self):
        """Cleanup the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            self.logger.debug("HistoricalFetcher session closed")
            # Allow a short period for connections to close gracefully
            await asyncio.sleep(0.25)

    async def _ensure_session(self):
        """Ensure session is available."""
        if not self._session or self._session.closed:
            await self.start()

    async def start_systematic_collection(self, contracts: List[str]):
        """Start systematic hourly collection for specified contracts."""
        if self._collection_task is None:
            # Ensure session is initialized
            await self._ensure_session()

            self._running = True
            self._collection_task = asyncio.create_task(
                self._systematic_collection_loop(contracts)
            )
            self.logger.info(
                f"Started systematic collection for {len(contracts)} contracts"
            )
        else:
            self.logger.warning("Systematic collection already running")

    async def stop_systematic_collection(self):
        """Stop systematic collection and finish pending requests."""
        if self._collection_task and self._running:
            self._running = False
            self._collection_task.cancel()

            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass

            self._collection_task = None
            self.logger.info("Systematic collection stopped")

        # Also stop the session when systematic collection stops
        await self.stop()

    async def _systematic_collection_loop(self, contracts: List[str]):
        """Main loop for systematic data collection."""
        self.logger.info("Starting systematic historical data collection")

        while self._running:
            try:
                current_time = utc_now()

                # Check if it's time for hourly collection
                if current_time - self._last_hourly_collection >= timedelta(hours=1):
                    await self._perform_hourly_collection(contracts)
                    self._last_hourly_collection = current_time

                # Process pending requests
                await self._process_request_queue()

                # Sleep until next collection cycle (15 minutes)
                await asyncio.sleep(900)  # 15 minutes

            except asyncio.CancelledError:
                self.logger.info("Systematic collection cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in systematic collection: {e}")
                await asyncio.sleep(60)  # Brief pause on error

    async def _perform_hourly_collection(self, contracts: List[str]):
        """Perform hourly data collection for all contracts."""
        self.logger.info("Starting hourly data collection")

        # Collect data for the last 2 hours to ensure completeness
        end_time = utc_now().replace(minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(hours=2)

        for contract in contracts:
            request = HistoricalRequest(
                contract_id=contract,
                start_date=start_time,
                end_date=end_time,
                timeframe="1m",
                priority=1,
            )
            try:
                self._request_queue.put_nowait(request)
            except asyncio.QueueFull:
                self._queue_overflows += 1
                self.logger.warning(
                    f"Request queue full! Dropped hourly collection for {contract}. "
                    f"Total overflows: {self._queue_overflows}"
                )

        self._stats["last_collection"] = utc_now()

    async def _process_request_queue(self):
        """Process pending historical data requests."""
        processed = 0
        max_per_cycle = 5  # Limit to respect rate limits

        while processed < max_per_cycle and not self._request_queue.empty():
            try:
                request = await asyncio.wait_for(self._request_queue.get(), timeout=0.1)

                success = await self._fetch_historical_data(request)
                if success:
                    self._stats["requests_processed"] += 1

                processed += 1

            except asyncio.TimeoutError:
                break
            except Exception as e:
                self.logger.error(f"Error processing request: {e}")

    async def _fetch_historical_data(self, request: HistoricalRequest) -> bool:
        """Fetch historical data for a specific request with batching."""
        try:
            # Check if request needs to be split into chunks (max 20k bars per request)
            chunks = self._split_request_if_needed(request)

            for chunk in chunks:
                success = await self._fetch_single_chunk(chunk)
                if not success:
                    return False
                # Add small delay between chunks to respect rate limits
                if len(chunks) > 1:
                    await asyncio.sleep(0.5)

            return True

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch data for {request.contract_id}: {e}")
            self._stats["api_errors"] += 1
            return False

    def _split_request_if_needed(
        self, request: HistoricalRequest
    ) -> List[HistoricalRequest]:
        """Split large requests into chunks of max 20k bars."""
        max_bars_per_request = 20000

        # Calculate approximate bars based on timeframe
        timeframe_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}

        minutes_per_bar = timeframe_minutes.get(request.timeframe, 1)
        total_minutes = (request.end_date - request.start_date).total_seconds() / 60
        estimated_bars = int(total_minutes / minutes_per_bar)

        # If estimated bars is within limit, return single request
        if estimated_bars <= max_bars_per_request:
            return [request]

        # Split into chunks
        chunks = []
        chunk_duration_minutes = max_bars_per_request * minutes_per_bar
        chunk_duration = timedelta(minutes=chunk_duration_minutes)

        current_start = request.start_date
        while current_start < request.end_date:
            chunk_end = min(current_start + chunk_duration, request.end_date)

            chunk = HistoricalRequest(
                contract_id=request.contract_id,
                start_date=current_start,
                end_date=chunk_end,
                timeframe=request.timeframe,
                priority=request.priority,
            )
            chunks.append(chunk)
            current_start = chunk_end

        self.logger.info(
            f"Split large request into {len(chunks)} chunks for {request.contract_id}"
        )
        return chunks

    def _cache_key(self, request: HistoricalRequest) -> str:
        """Generate cache key for a request."""
        return f"{request.contract_id}:{request.timeframe}:{request.start_date.date().isoformat()}"

    async def _cache_get(self, key: str):
        if self._cache_is_async:
            return await self._cache.get(key)
        return self._cache.get(key)

    async def _cache_set(self, key: str, value: Any):
        if self._cache_is_async:
            await self._cache.set(key, value, ttl=self.config.cache_ttl)
        else:
            self._cache[key] = value

    async def _fetch_single_chunk(self, request: HistoricalRequest) -> bool:
        """Fetch a single chunk of historical data."""
        # Ensure session is available
        if not self._session or self._session.closed:
            self.logger.error(
                "Session is not available. Fetch call ignored. Ensure start() has been called."
            )
            raise RuntimeError(
                "HistoricalFetcher session not started. Call start() before using the fetcher."
            )

        try:
            cache_key = self._cache_key(request)
            cached = await self._cache_get(cache_key)
            if cached:
                bars_published = await self._process_api_response(
                    request.contract_id, request.timeframe, cached
                )
                self._stats["bars_fetched"] += bars_published
                self._stats["bars_published"] += bars_published
                self.logger.debug(f"Cache hit for {cache_key}")
                return True

            # Wait for rate limit availability - use correct endpoint
            await self._rate_limiter.acquire(self._HISTORY_ENDPOINT)

            # Prepare API request - Use POST with JSON payload per TopStepX docs
            headers = auth_headers(self._auth_manager.get_token())
            url = f"{self.config.projectx_base_url}{self._HISTORY_ENDPOINT}"

            # Calculate request parameters - ProjectX Gateway API expects simple YYYY-MM-DDTHH:MM:SSZ format
            start_iso = request.start_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_iso = request.end_date.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Map timeframe to ProjectX Gateway API format (unit codes: 2=Minute, 3=Hour, 4=Day)
            timeframe_mapping = {
                "1m": {"unitNumber": 1, "unit": 2},  # 1 minute
                "5m": {"unitNumber": 5, "unit": 2},  # 5 minutes
                "15m": {"unitNumber": 15, "unit": 2},  # 15 minutes
                "1h": {"unitNumber": 1, "unit": 3},  # 1 hour
                "1d": {"unitNumber": 1, "unit": 4},  # 1 day
            }

            timeframe_config = timeframe_mapping.get(
                request.timeframe, {"unitNumber": 1, "unit": 2}
            )

            # ProjectX Gateway API expects flat JSON payload (no request wrapper)
            payload = {
                "contractId": request.contract_id,
                "live": self.config.live_mode,
                "startTime": start_iso,
                "endTime": end_iso,
                "unit": timeframe_config["unit"],
                "unitNumber": timeframe_config["unitNumber"],
                "limit": 5000,
                "includePartialBar": False,
            }

            self.logger.debug(
                f"Fetching {request.contract_id} from {request.start_date} to {request.end_date}"
            )

            # Use the persistent session object
            async with self._session.post(
                url, headers=headers, json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    bars_published = await self._process_api_response(
                        request.contract_id, request.timeframe, data
                    )

                    self._stats["bars_fetched"] += bars_published
                    self._stats["bars_published"] += bars_published
                    await self._cache_set(cache_key, data)
                    self.logger.debug(
                        f"Fetched and published {bars_published} bars for {request.contract_id} chunk"
                    )
                    return True

                else:
                    error_text = await response.text()
                    self.logger.error(f"API error {response.status}: {error_text}")
                    self._stats["api_errors"] += 1
                    return False

        except aiohttp.ClientResponseError as e:
            self.logger.error(
                f"HTTP Error fetching history chunk: {e.status} {e.message}"
            )
            self._stats["api_errors"] += 1
            return False
        except asyncio.TimeoutError:
            self.logger.error("Request timed out while fetching history chunk")
            self._stats["api_errors"] += 1
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error in _fetch_single_chunk: {e}")
            self._stats["api_errors"] += 1
            return False

    async def _process_api_response(
        self, symbol: str, timeframe: str, data: Dict[str, Any]
    ) -> int:
        """Process API response and publish bars to EventBus via PersistenceService."""
        try:
            if not data or "bars" not in data:
                self.logger.warning(f"No bars in API response for {symbol}")
                return 0

            bars = data["bars"]
            if not bars:
                self.logger.debug(f"Empty bars array for {symbol}")
                return 0

            # Publish bars to EventBus (single-writer pattern)
            published_count = 0
            for bar in bars:
                # Convert timestamp from milliseconds to datetime
                timestamp_ms = bar.get("timestamp", 0)
                timestamp_dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

                # Create bar event for PersistenceService
                bar_event = {
                    "timestamp": timestamp_dt.isoformat(),
                    "contract_id": symbol,
                    "timeframe": timeframe,
                    "open": float(bar.get("open", 0)),
                    "high": float(bar.get("high", 0)),
                    "low": float(bar.get("low", 0)),
                    "close": float(bar.get("close", 0)),
                    "volume": int(bar.get("volume", 0)),
                    "source": "historical",
                    "revision": 1,
                }

                # Publish to PersistenceService via EventBus (single-writer pattern)
                await self._event_bus.publish(persist_bar_save(), bar_event)
                published_count += 1

            self.logger.debug(
                f"Published {published_count} bars to EventBus for {symbol}"
            )
            return published_count

        except Exception as e:
            self.logger.error(f"Error processing API response for {symbol}: {e}")
            return 0

    async def fetch_historical_range(
        self, symbol: str, start_date: datetime, end_date: datetime, priority: int = 1
    ) -> bool:
        """Manually request historical data for a specific range."""
        request = HistoricalRequest(
            contract_id=symbol,
            start_date=start_date,
            end_date=end_date,
            timeframe="1m",
            priority=priority,
        )

        try:
            self._request_queue.put_nowait(request)
            self.logger.info(
                f"Queued historical request for {symbol}: {start_date} to {end_date}"
            )
            return True
        except asyncio.QueueFull:
            self._queue_overflows += 1
            self.logger.error(
                f"Request queue full! Cannot queue request for {symbol}: {start_date} to {end_date}. "
                f"Total overflows: {self._queue_overflows}"
            )
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get fetcher statistics."""
        return {
            **self._stats,
            "queue_size": self._request_queue.qsize(),
            "queue_maxsize": self._request_queue.maxsize,
            "queue_utilization_pct": round(
                (self._request_queue.qsize() / self._request_queue.maxsize) * 100, 2
            ),
            "queue_overflows": self._queue_overflows,
            "is_running": self._running,
            "active_requests": len(self._active_requests),
        }

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop_systematic_collection()
