# Market Data, News, Options, Scanner, and Fundamentals

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

## Scanner

The market scanner screens stocks by various criteria — top gainers, most active, high dividend yield, etc.

### Discover available parameters

```bash
ibkr scanner params codes --profile gateway-paper
ibkr scanner params instruments --profile gateway-paper
ibkr scanner params locations --profile gateway-paper
```

The `codes` section lists all scan types (e.g., `TOP_PERC_GAIN`, `MOST_ACTIVE`, `HOT_BY_VOLUME`). The user needs a scan code to run a scan.

### Run a scan

```bash
ibkr scanner run TOP_PERC_GAIN --profile gateway-paper
ibkr scanner run MOST_ACTIVE --limit 10 --profile gateway-paper
ibkr scanner run HOT_BY_VOLUME --above-price 10 --below-price 100 --profile gateway-paper
```

| Flag                 | Default          | Description                         |
|----------------------|------------------|-------------------------------------|
| `--instrument`       | `STK`            | Instrument type (STK, ETF.EQ.US)    |
| `--location`         | `STK.US.MAJOR`   | Market location code                |
| `--limit`            | `20`             | Maximum results (1–50)              |
| `--above-price`      | —                | Minimum price filter                |
| `--below-price`      | —                | Maximum price filter                |
| `--above-volume`     | —                | Minimum volume filter               |
| `--market-cap-above` | —                | Minimum market cap filter           |
| `--market-cap-below` | —                | Maximum market cap filter           |

If the user asks "what stocks are moving today", "show me top gainers", or "find high dividend stocks", use the scanner commands.

## Fundamentals

> **Subscription required:** All fundamentals commands require a **Reuters Fundamentals** subscription (~$7/month). Subscribe via IBKR Account Management > Settings > Market Data Subscriptions (search for "Reuters Fundamentals" or "LSEG"). Without this subscription, commands will fail with error code `fundamentals_request_failed` and a message explaining how to subscribe.

### Company snapshot

```bash
ibkr fundamentals snapshot AAPL --profile gateway-live
```

Returns company overview including industry, employees, key ratios (P/E, market cap, margins, ROE, etc.), officers, and business summary.

### Financial summary

```bash
ibkr fundamentals summary AAPL --profile gateway-live
```

Returns key financial metrics across multiple reporting periods (TTM, annual, interim).

### Full financial statements

```bash
ibkr fundamentals financials AAPL --profile gateway-live
```

Returns income statement, balance sheet, and cash flow — both annual and interim periods.

### Ownership

```bash
ibkr fundamentals ownership AAPL --profile gateway-live
```

Returns institutional and insider holders with shares held and percentages.

| Flag         | Default | Description                              |
|--------------|---------|------------------------------------------|
| `--exchange` | `SMART` | Exchange routing                         |
| `--currency` | `USD`   | Currency for contract qualification      |
| `--timeout`  | `10.0`  | API timeout in seconds                   |

**Typical workflow:**

1. `ibkr fundamentals snapshot AAPL` — company overview and key ratios
2. `ibkr fundamentals financials AAPL` — detailed financial statements
3. `ibkr fundamentals ownership AAPL` — who owns the stock

If the user asks "what's AAPL's P/E ratio", "show me Tesla's balance sheet", "who owns MSFT", or "company financials for NVDA", use the fundamentals commands.
