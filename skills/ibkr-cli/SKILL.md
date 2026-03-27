---
name: ibkr-cli
description: Guide users through Interactive Brokers CLI operations — from installing IB Gateway/TWS and ibkr-cli itself, to trading stocks, monitoring accounts, retrieving market data, reading financial news, exploring options chains, screening stocks with market scanners, querying company fundamentals, viewing historical trades, checking P&L, reviewing fund transfers, and tracking dividends. Use this skill whenever the user mentions Interactive Brokers, IBKR, TWS, IB Gateway, stock trading via CLI, checking portfolios or positions, getting quotes, placing orders, reading stock news, options chain, greeks, stock screener, market scanner, top gainers, most active, company fundamentals, financial statements, balance sheet, income statement, cash flow, ownership, institutional holders, trade history, realized P&L, unrealized P&L, fund transfers, deposits, withdrawals, dividends, interest, or anything related to brokerage account management through a terminal. Even if the user doesn't say "ibkr" explicitly, trigger when they want to buy/sell stocks from the command line, check their brokerage account, read news about a stock, look up options data, screen for stocks, check company financials, view trade history, check profit and loss, review fund movements, or set up an API connection to a broker.
---

# ibkr-cli

You are helping a user who may have zero prior experience with Interactive Brokers, command-line tools, or trading APIs. Your job is to guide them step by step — from setting up the infrastructure to executing trades — using the `ibkr-cli` tool.

## How to approach the user

Start by understanding where the user is in their journey. Ask or infer:

1. Do they have IB Gateway or TWS installed and running?
2. Is `ibkr-cli` installed?
3. Have they verified connectivity (`ibkr doctor`)?

Don't dump all commands at once. Walk them through the relevant next step based on their current state. If they say "I want to buy AAPL", first check whether they have the infrastructure in place — don't jump straight to `ibkr buy`.

## Routing — read the right reference

Based on what the user needs, read the corresponding reference file for detailed commands and guidance:

| User intent | Reference file |
|---|---|
| Installing Gateway/TWS, installing ibkr-cli, configuring profiles, verifying connectivity, troubleshooting connection issues | `references/setup.md` |
| Buying/selling stocks, previewing orders, limit orders, cancelling orders, order management | `references/trading.md` |
| Quotes, historical bars, news headlines/articles, options chains, options greeks, market scanner/screener, company fundamentals, financial statements, ownership | `references/market-data.md` |
| Account summary, positions, portfolio, JSON output, updating the CLI | `references/account.md` |
| Historical trades, P&L, fund transfers, deposits/withdrawals, dividends, interest, cash transactions, Flex Queries configuration | `references/flex-queries.md` |

When a user's request spans multiple areas (e.g., "set up ibkr and buy some AAPL"), read the relevant references in sequence — start with setup, then move to trading once the infrastructure is confirmed.

## Key principles

These apply across all commands — keep them in mind regardless of which reference you're reading:

- **Connection priority**: Unless the user explicitly mentions "paper" or a paper account, prefer live profiles over paper, and gateway over TWS. The priority order is: `gateway-live` (port 4001) > `live` (port 7496) > `gateway-paper` (port 4002) > `paper` (port 7497). When connecting for the first time or when the user hasn't specified a profile, try `ibkr connect test --profile gateway-live --json` first. If it fails, try the next profile in the priority list. Once a working profile is found, use it for subsequent commands.
- **Profile flag**: Most commands accept `--profile`. Always be explicit about which profile to use. If the user hasn't specified one and you haven't yet determined which profile works, run the connection priority check first.
- **One connection at a time**: Running multiple ibkr-cli processes against the same profile simultaneously can cause client_id conflicts. Run commands serially per profile.
- **JSON output**: All read and trading commands support `--json` for machine-readable output. Error responses follow a structured format with `ok`, `error.code`, `error.message`, and `error.exit_code` fields.
