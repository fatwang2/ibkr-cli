# Market Data, News, and Options

## Quotes

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

## Historical bars

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

## News

ibkr-cli can retrieve news headlines and full articles for any symbol from IBKR's news providers.

### List news providers

```bash
ibkr news providers --profile gateway-paper
```

Shows available news sources (e.g., BRFG for Briefing.com, DJNL for Dow Jones). The user needs to know provider codes if they want to filter headlines by source.

### Headlines

```bash
ibkr news headlines AAPL --profile gateway-paper
ibkr news headlines AAPL --limit 20 --profile gateway-paper
ibkr news headlines AAPL --providers "BRFG,DJNL" --profile gateway-paper
ibkr news headlines AAPL --start "20260101 00:00:00" --end "20260318 00:00:00" --profile gateway-paper
```

| Flag           | Default | Description                                        |
|----------------|---------|----------------------------------------------------|
| `--providers`  | all     | Comma-separated provider codes to filter by         |
| `--start`      | —       | Start time in UTC: `"YYYYMMDD HH:MM:SS"`           |
| `--end`        | —       | End time in UTC: `"YYYYMMDD HH:MM:SS"`             |
| `--limit`      | `10`    | Maximum number of headlines (1–300)                 |

If the user asks "what's happening with AAPL" or "any news on Tesla", use `ibkr news headlines`.

### Read an article

Each headline includes a `provider_code` and `article_id`. To read the full article:

```bash
ibkr news article BRFG "BRFG$12345" --profile gateway-paper
```

Guide the user to first run `headlines` to get the article ID, then use `article` to read the full text.

## Options

ibkr-cli supports querying options chains and fetching option quotes with greeks.

### List option chains

```bash
ibkr options chain AAPL --profile gateway-paper
```

Shows all available exchanges, trading classes, expirations, and strikes for a symbol's options. The user needs the expiration date from this output to fetch quotes.

### Option quotes with greeks

```bash
ibkr options quotes AAPL 20260320 --profile gateway-paper
```

Fetches option quotes for a specific expiration. By default, it auto-selects strikes within ±10% of the current underlying price and shows both calls and puts.

| Flag        | Default | Description                                              |
|-------------|---------|----------------------------------------------------------|
| `--right`   | both    | Filter by `C` (call) or `P` (put)                       |
| `--strike`  | auto    | Specific strike price. Repeatable for multiple strikes   |
| `--exchange`| `SMART` | Exchange routing                                         |

Each row includes: bid, ask, last, volume, open interest, and full greeks (IV, delta, gamma, theta, vega).

**Typical workflow:**

1. `ibkr options chain AAPL` — see available expirations
2. `ibkr options quotes AAPL 20260320` — get quotes for a specific expiry
3. `ibkr options quotes AAPL 20260320 --right C --strike 150 --strike 155` — narrow down

If the user asks "what are the options for AAPL", "show me AAPL calls", or "what's the delta on AAPL puts", use the options commands.
