# ibkr-cli

A local-first CLI for Interactive Brokers built on top of `ib_async`, `Typer`, and `Rich`.

## Features

- Profile-based local connection management for TWS and IB Gateway
- Connectivity checks for TCP reachability and IBKR API handshake
- Account queries for summary and positions
- Order queries for open, completed, and executions
- Safe stock order preview via IBKR what-if orders
- Real stock order submission with explicit `--submit`
- Open order cancellation by order ID
- Market data snapshot quotes with live-to-delayed fallback
- Finite quote watch mode for repeated quote updates
- Historical bar retrieval

## Requirements

- Python 3.10+
- A running TWS or IB Gateway session
- Market data subscriptions as required by your IBKR account

## Installation

Install from PyPI with `pipx` so the `ibkr` command is isolated from your main Python environment:

```bash
pipx install ibkr-cli
```

If you prefer to install into the current Python environment:

```bash
python -m pip install ibkr-cli
```

After installation:

```bash
ibkr --help
ibkr --version
```

## Quick start

### Initialize local config

```bash
ibkr profile init
```

This creates a config file under the path returned by:

```bash
ibkr config-path
```

The default generated profiles are:

- `paper` -> `127.0.0.1:7497`
- `live` -> `127.0.0.1:7496`
- `gateway-paper` -> `127.0.0.1:4002`
- `gateway-live` -> `127.0.0.1:4001`

### Inspect available profiles

```bash
ibkr profile list
ibkr profile show gateway-paper
```

### Check connectivity

```bash
ibkr doctor --profile gateway-paper
ibkr connect test --profile gateway-paper
```

## Core commands

### Account and positions

```bash
ibkr account summary --profile gateway-paper
ibkr positions --profile gateway-paper
```

### Orders

```bash
ibkr orders open --profile gateway-paper
ibkr orders completed --profile gateway-paper
ibkr orders executions --profile gateway-paper
ibkr orders cancel 12345 --profile gateway-paper
```

### Trading

Preview first:

```bash
ibkr buy AAPL 10 --preview --profile gateway-paper
ibkr sell AAPL 10 --preview --profile gateway-paper
```

Submit only when you explicitly intend to place an order:

```bash
ibkr buy AAPL 10 --submit --profile gateway-paper
ibkr sell AAPL 10 --submit --profile gateway-paper
```

### Market data

Snapshot quote:

```bash
ibkr quote AAPL --profile gateway-paper
ibkr quote AAPL --profile gateway-paper --json
```

Finite quote watch:

```bash
ibkr quote AAPL --watch --updates 5 --interval 2 --profile gateway-paper
```

Historical bars:

```bash
ibkr bars AAPL --profile gateway-paper
ibkr bars AAPL --profile gateway-paper --duration "1 D" --bar-size "5 mins" --json
```

## JSON output

Most read and trading commands support `--json` for machine-readable output.

Examples:

```bash
ibkr quote AAPL --profile gateway-paper --json
ibkr orders completed --profile gateway-paper --json
ibkr buy AAPL 10 --preview --profile gateway-paper --json
```

### Error JSON shape

When a command fails in `--json` mode, the CLI returns a structured error payload and exits with a non-zero process code.

Shape:

```json
{
  "ok": false,
  "error": {
    "code": "invalid_arguments",
    "message": "Choose exactly one of --preview or --submit.",
    "exit_code": 2,
    "details": {
      "preview": false,
      "submit": false
    }
  }
}
```

Current error code families include:

- `invalid_arguments`
- `config_load_failed`
- `config_already_exists`
- `unknown_profile`
- `connectivity_check_failed`
- `account_query_failed`
- `order_query_failed`
- `order_operation_failed`
- `market_data_request_failed`

## Operational notes

### Prefer paper trading first

Use `gateway-paper` or `paper` while validating commands that submit or cancel real orders.

### Submit is explicit

`buy` and `sell` require exactly one of:

- `--preview`
- `--submit`

This prevents accidental live order placement from a default command path.

### Run same-profile validations serially

If multiple CLI processes connect to the same TWS or IB Gateway profile with the same fixed `client_id`, IBKR can reject or interfere with the second connection.

For validation and manual testing, prefer running commands sequentially per profile unless you intentionally assign unique client IDs.

### Market data fallback

`quote` automatically falls back from live market data to delayed market data when live prices are unavailable.

### Command name conflicts

This package installs the command `ibkr`.

If your machine already has another CLI exposing the same command name, uninstall the old tool or adjust your `PATH` so that this package's `ibkr` entrypoint is the one your shell resolves first.

## Development

Install in editable mode:

```bash
python -m pip install -e .
```

Install in editable mode with optional test dependencies:

```bash
python -m pip install -e ".[test]"
```

Run directly from source if needed:

```bash
python -m ibkr_cli.app --help
```

Run the offline test suite:

```bash
python -m unittest discover -s tests -v
```

The packaged entrypoint for installed users is:

```bash
ibkr --help
```
