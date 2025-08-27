# REST API

## GET `/status`
Returns system status and metrics reported by the orchestrator.

**Response Body**
```json
{
  "status": "<status message>",
  // additional fields with health reports and service metrics
}
```

## GET `/metrics`
Retrieve runtime metrics for the orchestrator and all registered strategies.

No authentication is required.

**Response Body**
```json
{
  "running": true,
  "total_strategies": 2,
  "running_strategies": 2,
  "strategies": {
    "rsi_ema_ES_1m": {
      "strategy_id": "rsi_ema_ES_1m",
      "running": true,
      "initialized": true,
      "context_metrics": {
        "account_id": "acct1",
        "contract_id": "ES",
        "timeframe": "1m",
        "order_count": 42
      }
    },
    "donchian_RTY_5m": {
      "strategy_id": "donchian_RTY_5m",
      "running": true,
      "initialized": true,
      "context_metrics": {
        "account_id": "acct2",
        "contract_id": "RTY",
        "timeframe": "5m",
        "order_count": 5
      }
    }
  }
}
```

## POST `/orders`
Submit an order for execution.

**Request Body**
```json
{
  "strategy_id": "<strategy identifier>",
  "account_id": "<account identifier>",
  "contract_id": "<contract identifier>",
  "type": "<order type>",
  "side": "<BUY|SELL>",
  "size": <integer>,
  "limit_price": <number>,        // optional
  "stop_price": <number>,         // optional
  "trail_price": <number>,        // optional
  "time_in_force": "Day",        // optional, default
  "custom_tag": "<string>"       // optional
}
```

**Response Body**
```json
{"status": "submitted"}
```

## POST `/strategies`
Register a new strategy using an arbitrary configuration object.

**Request Body**
```json
{ /* strategy configuration */ }
```

**Response Body**
```json
{"status": "added"}
```

## DELETE `/strategies/{strategy_id}`
Remove a previously registered strategy.

**Response Body**
```json
{"status": "removed"}
```

# WebSocket Gateway
Events published on the internal event bus can be forwarded to connected WebSocket clients.
Each message is a JSON object with the following structure:

```json
{
  "topic": "<event topic>",
  "payload": <event payload>
}
```

Clients may restrict delivered topics using `patterns` query parameters or path suffixes when connecting to `/ws/{token}`.
