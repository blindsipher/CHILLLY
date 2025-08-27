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
