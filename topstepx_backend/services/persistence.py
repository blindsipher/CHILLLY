"""Single-writer persistence service with UPSERT semantics and float scaling."""

import logging
import sqlite3
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.data.models import Bar
from topstepx_backend.services.contract_service import ContractService
from topstepx_backend.networking.api_helpers import utc_now


@dataclass
class PersistenceEvent:
    """Base class for persistence events."""

    pass


@dataclass
class BarSaveEvent(PersistenceEvent):
    """Event to save bar data."""

    bars: List[Bar]
    source: str = "live"


@dataclass
class TickSaveEvent(PersistenceEvent):
    """Event to save tick data."""

    symbol: str
    timestamp: datetime
    price: float
    volume: int
    source: str = "live"


class PersistenceService:
    """
    Single-writer database service that subscribes to persistence.* events.

    Features:
    - UPSERT semantics with PRIMARY KEY constraints
    - Float to scaled integer conversion for OHLCV data
    - SQLite WAL mode configuration
    - Comprehensive error handling and retry logic
    - Metrics tracking for observability
    """

    # Default scaling factor for float to integer conversion (4 decimal places)
    DEFAULT_PRICE_SCALE = 10000
    VOLUME_SCALE = 1  # Volume is already integer

    def __init__(
        self,
        event_bus: EventBus,
        database_path: str,
        contract_service: Optional[ContractService] = None,
    ):
        self.event_bus = event_bus
        self.database_path = database_path
        self.contract_service = contract_service
        self.logger = logging.getLogger(__name__)
        self._running = False
        self._subscription = None

        # Cache for contract tick sizes to avoid repeated lookups
        self._tick_size_cache: Dict[str, float] = {}

        # Database connection (created on start)
        self._conn: Optional[sqlite3.Connection] = None

        # Metrics
        self._metrics = {
            "bars_saved": 0,
            "ticks_saved": 0,
            "upserts_performed": 0,
            "errors": 0,
            "avg_batch_size": 0.0,
            "last_save_time": None,
        }
        self._metrics_lock = asyncio.Lock()

    async def start(self):
        """Start the persistence service and subscribe to persistence events."""
        if self._running:
            self.logger.warning("PersistenceService already running")
            return

        # Initialize database connection
        await self._init_database()

        # Subscribe to persistence events
        self._subscription = await self.event_bus.subscribe(
            "persistence.*",
            critical=True,  # Critical subscriber - block publishers if overwhelmed
            maxsize=2000,
        )

        self._running = True

        # Start event processing loop
        asyncio.create_task(self._process_events())

        self.logger.info("PersistenceService started")

    async def _get_price_scale(self, contract_id: str) -> int:
        """Get the price scale for a contract based on its tick size.

        This ensures we store prices as integers without losing precision.
        For example:
        - tick_size 0.25 -> scale 4 -> store as int(price * 4)
        - tick_size 0.5 -> scale 2 -> store as int(price * 2)
        - tick_size 0.01 -> scale 100 -> store as int(price * 100)
        """
        # Check cache first
        if contract_id in self._tick_size_cache:
            tick_size = self._tick_size_cache[contract_id]
        else:
            # Look up contract if service is available
            if self.contract_service:
                try:
                    contract = await self.contract_service.get_contract_by_id(
                        contract_id
                    )
                    if contract and contract.tick_size > 0:
                        tick_size = contract.tick_size
                        self._tick_size_cache[contract_id] = tick_size
                    else:
                        # Fall back to default if contract not found
                        tick_size = 1.0 / self.DEFAULT_PRICE_SCALE
                except Exception as e:
                    self.logger.warning(
                        f"Failed to get tick size for {contract_id}: {e}"
                    )
                    tick_size = 1.0 / self.DEFAULT_PRICE_SCALE
            else:
                # No contract service, use default
                tick_size = 1.0 / self.DEFAULT_PRICE_SCALE

        # Calculate scale as inverse of tick size
        # This ensures prices are stored as integers aligned to tick boundaries
        scale = int(1.0 / tick_size)
        return scale

    async def stop(self):
        """Stop the persistence service and close database connection."""
        if not self._running:
            return

        self._running = False

        # Unsubscribe from events
        if self._subscription:
            self._subscription.close()

        # Close database connection
        if self._conn:
            self._conn.close()
            self._conn = None

        self.logger.info("PersistenceService stopped")

    async def _init_database(self):
        """Initialize database connection and configure for optimal performance."""
        try:
            # Open database with WAL mode for better concurrency off the event loop
            self._conn = await asyncio.to_thread(
                sqlite3.connect,
                self.database_path,
                check_same_thread=False,  # Allow access from different threads
                timeout=30.0,  # 30 second timeout for busy database
            )

            # Configure SQLite for optimal performance in a background thread
            await asyncio.to_thread(self._configure_connection)

            # Create tables if they don't exist
            await self._create_tables()

            self.logger.info(f"Database initialized: {self.database_path}")

        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise

    async def _create_tables(self):
        """Create database tables with appropriate constraints."""
        try:
            await asyncio.to_thread(self._create_tables_sync)
            self.logger.info("Database tables created/verified")
        except Exception as e:
            self.logger.error(f"Failed to create tables: {e}")
            raise

    def _configure_connection(self) -> None:
        """Configure SQLite PRAGMA settings for optimal performance."""
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # Faster than FULL, still safe
        self._conn.execute("PRAGMA cache_size=10000")  # 10MB cache
        self._conn.execute("PRAGMA temp_store=memory")
        self._conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory map

    def _create_tables_sync(self) -> None:
        """Synchronous helper to create tables and indexes."""
        # Bars table with scaled integer prices and PRIMARY KEY constraint
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                open_scaled INTEGER NOT NULL,
                high_scaled INTEGER NOT NULL,
                low_scaled INTEGER NOT NULL,
                close_scaled INTEGER NOT NULL,
                volume INTEGER NOT NULL,
                source TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
                PRIMARY KEY (symbol, timeframe, timestamp_ms)
            ) WITHOUT ROWID
            """
        )

        # Ticks table
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticks (
                symbol TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                price_scaled INTEGER NOT NULL,
                volume INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
                PRIMARY KEY (symbol, timestamp_ms)
            ) WITHOUT ROWID
            """
        )

        # Create indexes for better query performance
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_bars_symbol_timeframe
            ON bars (symbol, timeframe, timestamp_ms DESC)
            """
        )

        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time
            ON ticks (symbol, timestamp_ms DESC)
            """
        )

        self._conn.commit()

    async def _process_events(self):
        """Main event processing loop."""
        self.logger.info("Starting persistence event processing loop")

        try:
            async for topic, payload in self._subscription:
                if not self._running:
                    break

                try:
                    await self._handle_persistence_event(topic, payload)

                except Exception as e:
                    async with self._metrics_lock:
                        self._metrics["errors"] += 1
                    self.logger.error(f"Error handling persistence event: {e}")

        except Exception as e:
            self.logger.error(f"Persistence event loop error: {e}")

        self.logger.info("Persistence event processing loop stopped")

    async def _handle_persistence_event(self, topic: str, payload: Dict[str, Any]):
        """Handle a specific persistence event."""
        if topic.startswith("persistence.bar"):
            await self._handle_bar_save(payload)
        elif topic.startswith("persistence.tick"):
            await self._handle_tick_save(payload)
        else:
            self.logger.warning(f"Unknown persistence event: {topic}")

    async def _handle_bar_save(self, payload):
        """Handle bar save events with UPSERT semantics and enhanced error handling."""
        try:
            # Input validation
            if not payload:
                self.logger.warning("Empty payload received for bar save")
                return
            # Handle direct bar dict from LiveBarBuilder
            if isinstance(payload, dict) and "contract_id" in payload:
                # Convert single bar dict to Bar object
                from topstepx_backend.data.models import Bar

                # Parse timestamp if it's a string
                if isinstance(payload.get("timestamp"), str):
                    timestamp = datetime.fromisoformat(
                        payload["timestamp"].replace("Z", "+00:00")
                    )
                else:
                    timestamp = payload.get("timestamp", utc_now())

                bar = Bar(
                    timestamp=timestamp,
                    contract_id=payload["contract_id"],
                    timeframe=payload["timeframe"],
                    open=float(payload["open"]),
                    high=float(payload["high"]),
                    low=float(payload["low"]),
                    close=float(payload["close"]),
                    volume=int(payload["volume"]),
                    source=payload.get("source", "live"),
                    revision=int(payload.get("revision", 1)),
                )
                bars = [bar]
                source = payload.get("source", "live")

            elif isinstance(payload, BarSaveEvent):
                bars = payload.bars
                source = payload.source
            elif isinstance(payload, dict) and "bars" in payload:
                bars = payload["bars"]
                source = payload.get("source", "unknown")
            else:
                self.logger.error(f"Invalid bar save payload: {type(payload)}")
                return

            if not bars:
                return

            # Prepare batch data with scaled integers
            batch_data = []
            for bar in bars:
                # Get contract-specific price scale
                price_scale = await self._get_price_scale(bar.contract_id)

                scaled_data = (
                    bar.contract_id,
                    bar.timeframe,
                    int(bar.timestamp.timestamp() * 1000),  # Convert to milliseconds
                    int(bar.open * price_scale),
                    int(bar.high * price_scale),
                    int(bar.low * price_scale),
                    int(bar.close * price_scale),
                    int(bar.volume),
                    source,
                    bar.revision,
                )
                batch_data.append(scaled_data)

            # Perform UPSERT operation off the event loop
            await asyncio.to_thread(self._write_bars_sync, batch_data)

            # Update metrics
            async with self._metrics_lock:
                self._metrics["bars_saved"] += len(bars)
                self._metrics["upserts_performed"] += 1
                self._metrics["last_save_time"] = utc_now()

                # Running average of batch size
                count = self._metrics["upserts_performed"]
                current_avg = self._metrics["avg_batch_size"]
                self._metrics["avg_batch_size"] = (
                    current_avg * (count - 1) + len(bars)
                ) / count

            self.logger.debug(f"Saved {len(bars)} bars to database")

        except Exception as e:
            self.logger.error(f"Error saving bars: {e}")
            # Enhanced error handling and recovery
            async with self._metrics_lock:
                self._metrics["errors"] += 1

            # Rollback on error
            if self._conn:
                try:
                    self._conn.rollback()
                except Exception as rollback_error:
                    self.logger.error(f"Rollback failed: {rollback_error}")

            # Check if this is a connection issue and attempt recovery
            if (
                "database is locked" in str(e).lower()
                or "no such table" in str(e).lower()
            ):
                await self._attempt_database_recovery()

            raise

    async def _handle_tick_save(self, payload):
        """Handle tick save events with UPSERT semantics."""
        try:
            if isinstance(payload, TickSaveEvent):
                symbol = payload.symbol
                timestamp_ms = int(payload.timestamp.timestamp() * 1000)
                # Get contract-specific price scale
                price_scale = await self._get_price_scale(symbol)
                price_scaled = int(payload.price * price_scale)
                volume = payload.volume
                source = payload.source
            elif isinstance(payload, dict):
                symbol = payload["symbol"]
                timestamp_ms = int(payload["timestamp"].timestamp() * 1000)
                # Get contract-specific price scale
                price_scale = await self._get_price_scale(symbol)
                price_scaled = int(payload["price"] * price_scale)
                volume = payload["volume"]
                source = payload.get("source", "unknown")
            else:
                self.logger.error(f"Invalid tick save payload: {type(payload)}")
                return

            # UPSERT tick data off the event loop
            await asyncio.to_thread(
                self._write_tick_sync,
                symbol,
                timestamp_ms,
                price_scaled,
                volume,
                source,
            )

            # Update metrics
            async with self._metrics_lock:
                self._metrics["ticks_saved"] += 1
                self._metrics["last_save_time"] = utc_now()

            self.logger.debug(f"Saved tick for {symbol} at {timestamp_ms}")

        except Exception as e:
            self.logger.error(f"Error saving tick: {e}")
            # Rollback on error
            if self._conn:
                self._conn.rollback()
            raise

    def get_metrics(self) -> Dict[str, Any]:
        """Get persistence service metrics."""
        base_metrics = self._metrics.copy()
        base_metrics.update(
            {
                "running": self._running,
                "database_path": self.database_path,
                "subscription_active": self._subscription is not None,
            }
        )
        return base_metrics

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    async def _attempt_database_recovery(self):
        """Attempt to recover from database connection issues."""
        self.logger.warning("Attempting database recovery...")
        try:
            # Close existing connection
            if self._conn:
                self._conn.close()
                self._conn = None

            # Wait a moment
            await asyncio.sleep(1)

            # Reinitialize database
            await self._init_database()
            self.logger.info("Database recovery successful")

        except Exception as recovery_error:
            self.logger.error(f"Database recovery failed: {recovery_error}")
            # If recovery fails, we'll need to restart the service
            self._running = False

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on persistence service."""
        health_status = {
            "running": self._running,
            "database_connected": self._conn is not None,
            "subscription_active": self._subscription is not None,
            "last_save_time": self._metrics.get("last_save_time"),
            "error_count": self._metrics.get("errors", 0),
            "total_bars_saved": self._metrics.get("bars_saved", 0),
            "total_ticks_saved": self._metrics.get("ticks_saved", 0),
        }

        # Test database connectivity
        if self._conn:
            try:
                await asyncio.to_thread(self._conn.execute, "SELECT 1")
                health_status["database_test"] = "passed"
            except Exception as e:
                health_status["database_test"] = f"failed: {e}"
                health_status["database_connected"] = False

        return health_status

    def _write_bars_sync(self, batch_data: List[Any]) -> None:
        """Synchronous bar write operation for use with asyncio.to_thread."""
        try:
            self._conn.executemany(
                """
                INSERT INTO bars (
                    symbol, timeframe, timestamp_ms, open_scaled, high_scaled, 
                    low_scaled, close_scaled, volume, source, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp_ms) DO UPDATE SET
                    open_scaled = excluded.open_scaled,
                    high_scaled = excluded.high_scaled,
                    low_scaled = excluded.low_scaled,
                    close_scaled = excluded.close_scaled,
                    volume = excluded.volume,
                    source = excluded.source,
                    revision = excluded.revision
            """,
                batch_data,
            )

            self._conn.commit()

        except Exception as e:
            self._conn.rollback()
            raise e

    def _write_tick_sync(
        self,
        symbol: str,
        timestamp_ms: int,
        price_scaled: int,
        volume: int,
        source: str,
    ) -> None:
        """Synchronous tick write operation for use with asyncio.to_thread."""
        try:
            self._conn.execute(
                """
                INSERT INTO ticks (symbol, timestamp_ms, price_scaled, volume, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timestamp_ms) DO UPDATE SET
                    price_scaled = excluded.price_scaled,
                    volume = excluded.volume,
                    source = excluded.source
            """,
                (symbol, timestamp_ms, price_scaled, volume, source),
            )

            self._conn.commit()

        except Exception as e:
            self._conn.rollback()
            raise e
