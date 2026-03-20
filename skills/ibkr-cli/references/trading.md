# Trading and Order Management

## The preview-then-submit model

Every buy/sell command requires exactly one of `--preview` or `--submit`. This is a deliberate safety mechanism — it makes it impossible to accidentally place a real order by running a command without thinking. The two-step flow:

1. **Preview** — shows estimated impact (margin, commission, projected position) without touching the market
2. **Submit** — actually places the order

Always guide the user to preview first, especially when they're learning. If the user asks to "buy something", default to showing them the preview command and explain what the output means before suggesting submit.

## Buy and sell

Preview (no real order):
```bash
ibkr buy AAPL 10 --preview --profile <profile>
ibkr sell AAPL 10 --preview --profile <profile>
```

Submit (real order):
```bash
ibkr buy AAPL 10 --submit --profile <profile>
ibkr sell AAPL 10 --submit --profile <profile>
```

## Order types

### Market order (default)

```bash
ibkr buy AAPL 10 --preview --profile <profile>
```

### Limit order

```bash
ibkr buy AAPL 10 --type LMT --limit 150.00 --preview --profile <profile>
```

### Stop order

Triggers a market order when the stop price is reached:

```bash
ibkr sell AAPL 10 --type STP --stop 140.00 --preview --profile <profile>
```

### Stop-limit order

Triggers a limit order when the stop price is reached:

```bash
ibkr sell AAPL 10 --type "STP LMT" --stop 140.00 --limit 139.50 --preview --profile <profile>
```

### Trailing stop order

The stop price trails the market by a fixed dollar amount or percentage:

```bash
# Trail by $2.00
ibkr sell AAPL 10 --type TRAIL --trail-amount 2.00 --preview --profile <profile>

# Trail by 5%
ibkr sell AAPL 10 --type TRAIL --trail-percent 5 --preview --profile <profile>
```

You can optionally set an initial stop price with `--stop` for TRAIL orders.

### Bracket order (take-profit + stop-loss)

A bracket order places three linked orders at once: the parent order, a take-profit limit order, and a stop-loss order on the opposite side. Use `--take-profit` and `--stop-loss` together with a MKT or LMT parent:

```bash
# Market buy with take-profit at 160 and stop-loss at 140
ibkr buy AAPL 10 --take-profit 160.00 --stop-loss 140.00 --preview --profile <profile>

# Limit buy with take-profit and stop-loss
ibkr buy AAPL 10 --type LMT --limit 150.00 --take-profit 160.00 --stop-loss 140.00 --preview --profile <profile>
```

Both `--take-profit` and `--stop-loss` must be specified together. When the parent order fills, the take-profit and stop-loss become active. When either child fills, the other is automatically cancelled.

## Order options

| Flag             | Default  | Description                          |
|------------------|----------|--------------------------------------|
| `--type`         | `MKT`    | Order type: `MKT`, `LMT`, `STP`, `STP LMT`, or `TRAIL` |
| `--limit`        | —        | Limit price (required for `LMT` / `STP LMT`) |
| `--stop`         | —        | Stop trigger price (required for `STP` / `STP LMT`, optional for `TRAIL`) |
| `--trail-amount` | —        | Trailing dollar amount (for `TRAIL` orders) |
| `--trail-percent`| —        | Trailing percentage (for `TRAIL` orders) |
| `--take-profit`  | —        | Take-profit limit price (creates a bracket order) |
| `--stop-loss`    | —        | Stop-loss price (creates a bracket order) |
| `--exchange`     | `SMART`  | Exchange routing                     |
| `--currency`     | `USD`    | Currency                             |
| `--tif`          | `DAY`    | Time in force                        |
| `--outside-rth`  | off      | Allow outside regular trading hours  |
| `--account`      | —        | Target sub-account (multi-account setups) |

## Order management

### View orders

```bash
ibkr orders open --profile <profile>        # Currently active orders
ibkr orders completed --profile <profile>    # Filled/cancelled orders
ibkr orders executions --profile <profile>   # Execution details (fills)
```

### Cancel an order

```bash
ibkr orders cancel <order_id> --profile <profile>
```

The order_id comes from the `orders open` output. Guide the user to check open orders first if they don't know their order ID.
