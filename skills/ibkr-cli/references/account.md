# Account Monitoring and Utilities

## Account summary

```bash
ibkr account summary --profile gateway-paper
```

Returns key metrics like NetLiquidation, TotalCashValue, BuyingPower. If the user asks "how much money do I have" or "what's my account worth", this is the command.

## Positions

```bash
ibkr positions --profile gateway-paper
```

Shows current holdings. If the user asks "what do I own" or "show my portfolio", use this.

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
