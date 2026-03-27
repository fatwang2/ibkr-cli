# Historical Data (Flex Queries)

These commands provide historical account data sourced from IBKR Flex Queries. They do **not** require IB Gateway or TWS to be running — they work over HTTPS directly with IBKR's reporting service.

## Important notes

- **Data delay**: Flex Queries data is up to **T-1** (one business day behind). For real-time data, use Gateway-based commands (`orders executions`, `positions`, `account summary`).
- **Configuration required**: These commands require a Flex Web Service token and query ID. See the Setup section below.

## Setup

Users must configure two values before using these commands:

```bash
# Set the Flex Web Service token (from Account Management > Settings > FlexWeb Service)
ibkr config set flex.token <YOUR_TOKEN>

# Set the Flex Query ID (from Account Management > Reports > Flex Queries)
ibkr config set flex.query_id <YOUR_QUERY_ID>
```

Alternatively, environment variables `IBKR_FLEX_TOKEN` and `IBKR_FLEX_QUERY_ID` can be used (they take precedence over config file values).

To verify configuration:
```bash
ibkr config show
```

### How to create a Flex Query in IBKR Account Management

1. Log in to Account Management at interactivebrokers.com
2. Go to **Reports > Flex Queries**
3. Click **Create** under Activity Flex Queries
4. Select the sections you need: **Trades**, **Cash Transactions**, **Statement of Funds** (for transfers), **FIFO Performance Summary** (for P&L)
5. Set the period to **Last N Calendar Days**
6. Save and note the **Query ID**
7. Go to **Settings > FlexWeb Service** to generate a **token**

## Commands

### `ibkr trades` — Historical trade records

```bash
ibkr trades                    # Last 30 days (default)
ibkr trades --days 7           # Last 7 days
ibkr trades --days 90          # Last 90 days
ibkr trades --json             # JSON output
```

Shows: date, symbol, buy/sell, quantity, price, proceeds, commission, net cash, realized P&L, currency.

### `ibkr pnl` — P&L by symbol

```bash
ibkr pnl                       # Last 30 days (default)
ibkr pnl --days 90             # Last 90 days
ibkr pnl --json                # JSON output
```

Shows per-symbol breakdown: realized P&L, unrealized P&L, total P&L. Includes a TOTAL summary row.

### `ibkr transfers` — Fund deposits, withdrawals, and transfers

```bash
ibkr transfers                 # Last 90 days (default)
ibkr transfers --days 180      # Last 180 days
ibkr transfers --json          # JSON output
```

Shows: date, type (DEPOSIT/WITHDRAWAL/TRANSFER), amount, currency.

### `ibkr dividends` — Dividends, interest, and other cash transactions

```bash
ibkr dividends                 # Last 30 days (default)
ibkr dividends --days 90       # Last 90 days
ibkr dividends --json          # JSON output
```

Shows: date, type, symbol, description, amount, currency. Includes dividends, withholding tax, broker interest, and other cash movements.

## Configuration management

```bash
ibkr config show               # Show current configuration (token is masked)
ibkr config set <key> <value>  # Set a config value
ibkr config path               # Show config file location
```

Supported config keys: `flex.token`, `flex.query_id`, `default_profile`.

## Data source comparison

| Data | Real-time (Gateway) | Historical (Flex Queries) |
|------|:---:|:---:|
| Current positions | `ibkr positions` | - |
| Today's executions | `ibkr orders executions` | - |
| Historical trades | - | `ibkr trades` |
| Realized P&L (today) | `ibkr account summary` | - |
| Realized P&L (historical) | - | `ibkr pnl` |
| Fund transfers | - | `ibkr transfers` |
| Dividends/interest | - | `ibkr dividends` |

Gateway commands require IB Gateway/TWS running. Flex Queries commands only need HTTPS access and the configured token/query_id.
