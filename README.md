# CHILLLY

CHILLLY is an event-driven trading backend for the TopstepX platform. It
connects to TopstepX's APIs to collect market data, store it in SQLite, manage
the order lifecycle, and execute user supplied strategies. An orchestrator
bootstraps the system, wiring together loosely coupled services over a central
event bus so that new components or strategies can be added with minimal
friction.

## Architecture Overview
Independent services communicate exclusively via events:

- **EventBus** – topic-based routing layer with backpressure that decouples
  producers and consumers. Backends are pluggable, with in-memory and Redis
  implementations available.
- **SystemClock** – deterministic clock publishing UTC-aligned minute boundaries
  to drive bar creation.
- **PollingBarService** – polls the TopstepX History API every 30 seconds,
  emitting partial and finalized one-minute bars.
- **TimeframeAggregator** – builds higher timeframes (5m–1d) from the one-minute
  stream and can warm up from historical data.
- **PersistenceService** – single-writer SQLite sink that records ticks and bars
  by reacting to EventBus topics.
- **SeriesCacheService** – keeps an in-memory sliding window of recent bars for
  strategies or charting tools.
- **HistoricalFetcher** – requests older bars on demand and feeds them back
  through the EventBus.
- **OrderService** – translates order requests to TopstepX REST calls,
  correlates responses, and broadcasts status updates.
- **MarketSubscriptionService** – tracks active contracts, persists the state,
  and restores subscriptions on restart.
- **Strategy framework** – registry and runner that load strategy classes which
  subscribe to relevant topics and may publish order requests.

### Program Flow
1. The orchestrator loads configuration from environment variables,
   optionally installs `uvloop` for faster asyncio scheduling, authenticates
   with TopstepX, and starts the core services.
2. Market data flows from `PollingBarService` into the `EventBus`; aggregators,
   persistence, caches, and strategies consume the events.
3. Strategies can submit orders by publishing to order topics. `OrderService`
   sends the REST requests and publishes fills or rejections.
4. On shutdown the orchestrator flushes outstanding tasks and persists
   subscription state, allowing seamless restarts.

## Repository Layout
- `topstepx_backend/config` – configuration loading via environment variables and validation.
- `topstepx_backend/auth` – authentication manager for TopstepX API with automatic token refresh.
- `topstepx_backend/core` – event bus, system clock, and canonical topic helpers.
- `topstepx_backend/data` – services for historical data retrieval, polling bar service, timeframe aggregation, and bar types.
- `topstepx_backend/networking` – SignalR hub agents, API helpers, and rate limiter.
- `topstepx_backend/services` – persistence, order management, contract discovery, series caching, and subscription management.
- `topstepx_backend/strategy` – base strategy interfaces, runtime context, registry, examples, and runner.
- `tests` – pytest suite for core components.

## Configuration
Create an `.env` file or provide environment variables for authentication and runtime options:

```
TOPSTEP_USERNAME=...
TOPSTEP_API_KEY=...
TOPSTEP_ACCOUNT_ID=...
TOPSTEP_ACCOUNT_NAME=...
DEFAULT_CONTRACTS=CON.F.US.EP.U25,CON.F.US.ENQ.U25
DATABASE_PATH=data/topstepx.db
```

`DEFAULT_CONTRACTS` defines initial market subscriptions when no saved state exists.

## Running

The orchestrator starts all services and strategy runner:

```
python -m topstepx_backend.orchestrator
```

## Writing Strategies
Strategies reside in `topstepx_backend/strategy`. To build your own algorithm:

1. Subclass `BaseStrategy` and implement the `on_bar`/`on_tick` hooks of
   interest.
2. Register it with `register_strategy` so the orchestrator can discover it.
3. The strategy receives events from the `EventBus` and can publish order
   requests which the `OrderService` forwards to TopstepX.

Each strategy declares a single `contract_id` and `timeframe`. The
`StrategyRunner` routes only the appropriate market bars and boundary events to
that strategy, allowing multiple strategies to operate concurrently on different
markets or timeframes without interference.

See `topstepx_backend/strategy/examples` for simple templates.

## Testing
Run the unit tests with pytest:

```
pytest
```
