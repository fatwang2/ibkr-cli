# Setup and Connectivity

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

## Troubleshooting

When things go wrong, use `ibkr doctor` as the first diagnostic step. Common issues and how to resolve them:

- **"Connection refused"**: Gateway/TWS is not running, or the user selected the wrong profile (e.g., using port 4002 but TWS is on 7497)
- **"No market data"**: The user's IBKR account lacks market data subscriptions for the requested instrument. The quote command will automatically try delayed data as fallback.
- **"Client ID conflict"**: Multiple CLI processes are connecting to the same Gateway/TWS simultaneously. Advise running commands one at a time against a given profile.
- **Order rejected**: Use `--preview` first to check margin and commission before submitting. The preview output often reveals why an order would fail.
