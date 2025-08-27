# CHILLLY

This project contains backend services for managing market subscriptions and trading operations.

## Default Contract Subscriptions

Operators can specify which contracts are subscribed to on startup by setting the `DEFAULT_CONTRACTS` environment variable or by providing it in an `.env` file. The value should be a comma-separated list of full contract identifiers. Example:

```
DEFAULT_CONTRACTS=CON.F.US.EP.U25,CON.F.US.ENQ.U25
```

When no prior subscription state exists, these contracts are loaded as the initial active subscriptions.
