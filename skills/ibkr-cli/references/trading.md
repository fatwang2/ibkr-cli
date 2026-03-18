# Trading and Order Management

## The preview-then-submit model

Every buy/sell command requires exactly one of `--preview` or `--submit`. This is a deliberate safety mechanism — it makes it impossible to accidentally place a real order by running a command without thinking. The two-step flow:

1. **Preview** — shows estimated impact (margin, commission, projected position) without touching the market
2. **Submit** — actually places the order

Always guide the user to preview first, especially when they're learning. If the user asks to "buy something", default to showing them the preview command and explain what the output means before suggesting submit.

## Buy and sell

Preview (no real order):
```bash
ibkr buy AAPL 10 --preview --profile gateway-paper
ibkr sell AAPL 10 --preview --profile gateway-paper
```

Submit (real order):
```bash
ibkr buy AAPL 10 --submit --profile gateway-paper
ibkr sell AAPL 10 --submit --profile gateway-paper
```

## Order options

| Flag             | Default  | Description                          |
|------------------|----------|--------------------------------------|
| `--type`         | `MKT`    | Order type: `MKT` (market) or `LMT` (limit) |
| `--limit`        | —        | Limit price (required when `--type LMT`) |
| `--exchange`     | `SMART`  | Exchange routing                     |
| `--currency`     | `USD`    | Currency                             |
| `--tif`          | `DAY`    | Time in force                        |
| `--outside-rth`  | off      | Allow outside regular trading hours  |
| `--account`      | —        | Target sub-account (multi-account setups) |

When the user wants to buy at a specific price, guide them toward a limit order:

```bash
ibkr buy AAPL 10 --type LMT --limit 150.00 --preview --profile gateway-paper
```

## Order management

### View orders

```bash
ibkr orders open --profile gateway-paper        # Currently active orders
ibkr orders completed --profile gateway-paper    # Filled/cancelled orders
ibkr orders executions --profile gateway-paper   # Execution details (fills)
```

### Cancel an order

```bash
ibkr orders cancel <order_id> --profile gateway-paper
```

The order_id comes from the `orders open` output. Guide the user to check open orders first if they don't know their order ID.
