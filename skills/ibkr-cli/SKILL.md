---
name: ibkr-cli
description: Guide users through Interactive Brokers CLI operations — from installing IB Gateway/TWS and ibkr-cli itself, to trading stocks, monitoring accounts, and retrieving market data. Use this skill whenever the user mentions Interactive Brokers, IBKR, TWS, IB Gateway, stock trading via CLI, checking portfolios or positions, getting quotes, placing orders, or anything related to brokerage account management through a terminal. Even if the user doesn't say "ibkr" explicitly, trigger when they want to buy/sell stocks from the command line, check their brokerage account, or set up an API connection to a broker.
---

# ibkr-cli

You are helping a user who may have zero prior experience with Interactive Brokers, command-line tools, or trading APIs. Your job is to guide them step by step — from setting up the infrastructure to executing trades — using the `ibkr-cli` tool.

## How to approach the user

Start by understanding where the user is in their journey. Ask or infer:

1. Do they have IB Gateway or TWS installed and running?
2. Is `ibkr-cli` installed?
3. Have they verified connectivity (`ibkr doctor`)?

Don't dump all commands at once. Walk them through the relevant next step based on their current state. If they say "I want to buy AAPL", first check whether they have the infrastructure in place — don't jump straight to `ibkr buy`.

## Step 1: IB Gateway or TWS

ibkr-cli talks to Interactive Brokers through a local API gateway. The user needs one of these running on their machine before anything else works:

- **IB Gateway** (recommended): lightweight, no trading UI, built specifically for API access, uses fewer system resources.
- **TWS (Trader Workstation)**: full trading platform with a graphical interface, also exposes an API.

Either one works. Recommend Gateway unless the user also wants a visual trading interface.

### Installation

Direct the user to download from IBKR's website:

- Gateway: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
- TWS: https://www.interactivebrokers.com/en/trading/tws-updateless-latest.php

After installing, they need to launch the application and log in with their IBKR account credentials.

### API settings

- **IB Gateway**: API is enabled by default. No action needed. Default ports — live: 4001, paper: 4002.
- **TWS**: API is **not** enabled by default. The user must go to Edit > Global Configuration > API > Settings and check "Enable ActiveX and Socket Clients". Default ports — live: 7496, paper: 7497.

### Paper trading first

Always recommend paper trading for new users. When logging in to Gateway or TWS, they can select "Paper Trading" mode. This connects to a simulated environment where no real money is at risk — it's the right place to learn and experiment. Only suggest live profiles once the user explicitly indicates they're ready.

## Step 2: Install ibkr-cli

The CLI requires Python 3.10+. If the user doesn't have Python, help them install it first.

Recommended installation via pipx (isolated environment, won't interfere with other Python packages):

```bash
pipx install ibkr-cli
```

Alternative via pip:

```bash
python -m pip install ibkr-cli
```

Verify it works:

```bash
ibkr --version
```

## Step 3: Verify connectivity

The CLI automatically creates a config file with default profiles on first use — no manual initialization needed. The config file location can be found via `ibkr config-path`, and the user can edit it to customize host, port, or client_id if needed.

The four default profiles are:

| Profile         | Port | Use case              |
|-----------------|------|-----------------------|
| `paper`         | 7497 | TWS paper trading     |
| `live`          | 7496 | TWS live trading      |
| `gateway-paper` | 4002 | IB Gateway paper      |
| `gateway-live`  | 4001 | IB Gateway live       |

Help the user pick the right profile based on which application they're running (Gateway vs TWS) and which mode (paper vs live).

### Run doctor

```bash
ibkr doctor --profile gateway-paper
```

This runs a full health check — TCP reachability and API handshake. If it fails, common causes are:

- Gateway/TWS is not running — ask the user to start it
- Wrong profile — they're using a TWS profile but running Gateway, or vice versa
- API not enabled — only applies to TWS (see Step 1)
- Firewall blocking the port

Walk through these diagnostics one at a time rather than listing them all at once.

## Trading

### The preview-then-submit model

Every buy/sell command requires exactly one of `--preview` or `--submit`. This is a deliberate safety mechanism — it makes it impossible to accidentally place a real order by running a command without thinking. The two-step flow:

1. **Preview** — shows estimated impact (margin, commission, projected position) without touching the market
2. **Submit** — actually places the order

Always guide the user to preview first, especially when they're learning. If the user asks to "buy something", default to showing them the preview command and explain what the output means before suggesting submit.

### Commands

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

### Order options

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

## Account monitoring

### Account summary

```bash
ibkr account summary --profile gateway-paper
```

Returns key metrics like NetLiquidation, TotalCashValue, BuyingPower. If the user asks "how much money do I have" or "what's my account worth", this is the command.

### Positions

```bash
ibkr positions --profile gateway-paper
```

Shows current holdings. If the user asks "what do I own" or "show my portfolio", use this.

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

## Market data

### Snapshot quote

```bash
ibkr quote AAPL --profile gateway-paper
```

Returns a single point-in-time quote. The CLI automatically falls back from live to delayed data if the user's account doesn't have live market data subscriptions — no action needed from the user.

### Watch mode

```bash
ibkr quote AAPL --watch --updates 5 --interval 2 --profile gateway-paper
```

Prints 5 consecutive quote updates, 2 seconds apart. Useful when the user wants to monitor price movement in real time.

### Historical bars

```bash
ibkr bars AAPL --profile gateway-paper
ibkr bars AAPL --duration "1 D" --bar-size "5 mins" --profile gateway-paper
```

| Flag             | Default   | Description                              |
|------------------|-----------|------------------------------------------|
| `--duration`     | `1 D`     | Lookback period: `1 D`, `1 W`, `1 M`    |
| `--bar-size`     | `5 mins`  | Granularity: `1 min`, `1 hour`, `1 day`  |
| `--end`          | now       | End datetime: `"20260317 16:00:00"`      |
| `--what-to-show` | `TRADES`  | Data type: `TRADES`, `MIDPOINT`          |
| `--rth`          | default   | Regular trading hours only               |
| `--all-hours`    | —         | Include extended hours                   |

## JSON output

All read commands and trading commands support `--json` for machine-readable output. Suggest this when the user wants to pipe output to another tool or process it programmatically:

```bash
ibkr quote AAPL --profile gateway-paper --json
ibkr account summary --profile gateway-paper --json
ibkr buy AAPL 10 --preview --profile gateway-paper --json
```

Error responses in JSON mode follow a structured format with `ok`, `error.code`, `error.message`, and `error.exit_code` fields.

## Updating

The CLI checks for new versions automatically once a day and prints a hint if an update is available. To upgrade:

```bash
ibkr update
```

This detects whether the user installed via pipx or pip and runs the appropriate upgrade command. If the user reports issues that may be version-related, suggest running `ibkr update` first.

## Troubleshooting

When things go wrong, use `ibkr doctor` as the first diagnostic step. Common issues and how to resolve them:

- **"Connection refused"**: Gateway/TWS is not running, or the user selected the wrong profile (e.g., using port 4002 but TWS is on 7497)
- **"No market data"**: The user's IBKR account lacks market data subscriptions for the requested instrument. The quote command will automatically try delayed data as fallback.
- **"Client ID conflict"**: Multiple CLI processes are connecting to the same Gateway/TWS simultaneously. Advise running commands one at a time against a given profile.
- **Order rejected**: Use `--preview` first to check margin and commission before submitting. The preview output often reveals why an order would fail.

## Important operational notes

- **One connection at a time**: Running multiple ibkr-cli processes against the same profile simultaneously can cause client_id conflicts. Run commands serially per profile.
- **Profile flag**: Most commands accept `--profile`. If omitted, the CLI uses the default profile from config. Always be explicit about which profile to use when helping the user.
- **Live vs paper**: Never suggest a live profile command without the user explicitly requesting it. Default all examples to paper/gateway-paper.
