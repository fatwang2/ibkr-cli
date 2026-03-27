"""IBKR Flex Web Service client (synchronous).

Two-step HTTP workflow:
1. SendRequest — submit token + query_id, get a reference code
2. GetStatement — poll with reference code until the XML statement is ready

No dependency on ib_async or IB Gateway; uses only HTTPS.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence
from urllib.request import Request, urlopen
from urllib.error import URLError

from ibkr_cli.config import FlexConfig, FLEX_BASE_URL

_REFERENCE_CODE_RE = re.compile(r"<ReferenceCode>(\d+)</ReferenceCode>")
_ERROR_CODE_RE = re.compile(r"<ErrorCode>(\d+)</ErrorCode>")
_ERROR_MSG_RE = re.compile(r"<ErrorMessage>(.+?)</ErrorMessage>", re.DOTALL)

_MAX_ATTEMPTS = 5
_RETRY_DELAY = 2.0
_TIMEOUT = 20


# ── Data models ──────────────────────────────────────────────


@dataclass(frozen=True)
class FlexTrade:
    trade_date: Optional[str]
    symbol: str
    description: Optional[str]
    asset_category: Optional[str]
    buy_sell: str
    quantity: float
    price: float
    proceeds: float
    commission: float
    net_cash: float
    realized_pnl: float
    currency: str


@dataclass(frozen=True)
class FlexCashTransaction:
    date: str
    symbol: Optional[str]
    description: Optional[str]
    transaction_type: str  # Dividends, Withholding Tax, Broker Interest, etc.
    amount: float
    currency: str


@dataclass(frozen=True)
class FlexTransfer:
    date: str
    type: str  # DEPOSIT / WITHDRAWAL
    amount: float
    currency: str
    description: Optional[str]


@dataclass(frozen=True)
class FlexSymbolPnL:
    symbol: str
    description: Optional[str]
    asset_category: Optional[str]
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


# ── HTTP layer ───────────────────────────────────────────────


def _http_get(url: str) -> str:
    req = Request(url, headers={"User-Agent": "ibkr-cli/1.0"})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def _extract_error(text: str) -> Optional[str]:
    m = _ERROR_MSG_RE.search(text)
    return m.group(1).strip() if m else None


def _extract_error_code(text: str) -> Optional[str]:
    m = _ERROR_CODE_RE.search(text)
    return m.group(1) if m else None


def _send_request(flex: FlexConfig, *, days: int) -> str:
    """Step 1: request a statement, return the reference code."""
    url = (
        f"{FLEX_BASE_URL}/SendRequest"
        f"?t={flex.token}&q={flex.query_id}&v=3&p={days}"
    )
    text = _http_get(url)
    m = _REFERENCE_CODE_RE.search(text)
    if m:
        return m.group(1)
    error = _extract_error(text) or f"Failed to get reference code: {text[:200]}"
    raise RuntimeError(error)


def _get_statement(flex: FlexConfig, *, reference_code: str) -> str:
    """Step 2: poll until the statement XML is ready."""
    url = (
        f"{FLEX_BASE_URL}/GetStatement"
        f"?q={reference_code}&t={flex.token}&v=3"
    )
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        text = _http_get(url)
        error_code = _extract_error_code(text)
        if error_code == "1019":
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_DELAY)
                continue
            raise TimeoutError("Timeout waiting for Flex statement generation")
        if error_code is not None:
            error = _extract_error(text) or f"Flex error {error_code}"
            raise RuntimeError(error)
        return text
    raise RuntimeError("Failed to get Flex statement")


def fetch_statement_xml(flex: FlexConfig, *, days: int) -> str:
    """Fetch a Flex statement as raw XML."""
    ref = _send_request(flex, days=days)
    return _get_statement(flex, reference_code=ref)


# ── XML parsing helpers ──────────────────────────────────────


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _to_float(value: Optional[str]) -> float:
    if not value or value.strip() == "":
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _format_date(value: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    v = value.strip()
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        try:
            return date(int(v[0:4]), int(v[5:7]), int(v[8:10]))
        except ValueError:
            return None
    if len(v) >= 8 and v[:8].isdigit():
        try:
            return date(int(v[0:4]), int(v[4:6]), int(v[6:8]))
        except ValueError:
            return None
    return None


# ── Parsers ──────────────────────────────────────────────────


def parse_trades(xml: str) -> List[FlexTrade]:
    root = ET.fromstring(xml.strip())
    trades: List[FlexTrade] = []
    for elem in root.iter():
        if _local_name(elem.tag) != "Trade":
            continue
        a = elem.attrib
        symbol = (a.get("symbol") or "").strip()
        if not symbol:
            continue

        trade_date_raw = a.get("tradeDate") or (a.get("dateTime", "").split(";")[0] or None)
        trade_date = _format_date(trade_date_raw) if trade_date_raw else None

        buy_sell = (a.get("buySell") or "").upper() or "UNK"

        trades.append(
            FlexTrade(
                trade_date=trade_date,
                symbol=symbol,
                description=(a.get("description") or "").strip() or None,
                asset_category=a.get("assetCategory") or None,
                buy_sell=buy_sell,
                quantity=abs(_to_float(a.get("quantity"))),
                price=_to_float(a.get("tradePrice")),
                proceeds=_to_float(a.get("proceeds")),
                commission=_to_float(a.get("ibCommission") or a.get("commission")),
                net_cash=_to_float(a.get("netCash")),
                realized_pnl=_to_float(a.get("fifoPnlRealized")),
                currency=(a.get("currency") or "USD").strip(),
            )
        )
    trades.sort(key=lambda t: (t.trade_date or ""), reverse=True)
    return trades


def parse_cash_transactions(xml: str) -> List[FlexCashTransaction]:
    root = ET.fromstring(xml.strip())
    items: List[FlexCashTransaction] = []
    for elem in root.iter():
        if _local_name(elem.tag) != "CashTransaction":
            continue
        a = elem.attrib

        report_date = a.get("reportDate") or a.get("dateTime", "").split(";")[0] or ""
        if report_date:
            report_date = _format_date(report_date)

        amount = _to_float(a.get("amount"))
        if amount == 0:
            continue

        items.append(
            FlexCashTransaction(
                date=report_date,
                symbol=(a.get("symbol") or "").strip() or None,
                description=(a.get("description") or "").strip() or None,
                transaction_type=(a.get("type") or "").strip(),
                amount=amount,
                currency=(a.get("currency") or "USD").strip(),
            )
        )
    items.sort(key=lambda t: t.date, reverse=True)
    return items


def parse_transfers(xml: str) -> List[FlexTransfer]:
    root = ET.fromstring(xml.strip())
    items: List[FlexTransfer] = []
    for elem in root.iter():
        tag = _local_name(elem.tag)
        if tag != "StatementOfFundsLine":
            continue
        a = elem.attrib
        code = (a.get("activityCode") or "").strip().upper()
        if code not in ("DEP", "WITH", "TRANS"):
            continue
        amount = _to_float(a.get("amount"))
        if amount == 0:
            continue

        report_date = a.get("reportDate") or a.get("date") or ""
        if report_date:
            report_date = _format_date(report_date)

        type_label = {"DEP": "DEPOSIT", "WITH": "WITHDRAWAL", "TRANS": "TRANSFER"}.get(code, code)
        items.append(
            FlexTransfer(
                date=report_date,
                type=type_label,
                amount=amount,
                currency=(a.get("currency") or "USD").strip(),
                description=(a.get("description") or "").strip() or None,
            )
        )
    items.sort(key=lambda t: t.date, reverse=True)
    return items


def parse_symbol_pnls(xml: str) -> List[FlexSymbolPnL]:
    root = ET.fromstring(xml.strip())
    pnls: List[FlexSymbolPnL] = []
    for elem in root.iter():
        if _local_name(elem.tag) != "FIFOPerformanceSummaryUnderlying":
            continue
        a = elem.attrib
        symbol = (a.get("symbol") or "").strip()
        if not symbol:
            continue

        pnls.append(
            FlexSymbolPnL(
                symbol=symbol,
                description=(a.get("description") or "").strip() or None,
                asset_category=a.get("assetCategory") or None,
                realized_pnl=_to_float(a.get("totalRealizedPnl")),
                unrealized_pnl=_to_float(a.get("totalUnrealizedPnl")),
                total_pnl=_to_float(a.get("totalFifoPnl")),
            )
        )
    pnls.sort(key=lambda p: p.total_pnl, reverse=True)
    return pnls


# ── Public API (used by CLI commands) ────────────────────────


def get_flex_trades(flex: FlexConfig, *, days: int = 30) -> Dict:
    xml = fetch_statement_xml(flex, days=days)
    trades = parse_trades(xml)
    return {
        "rows": [
            {
                "trade_date": t.trade_date,
                "symbol": t.symbol,
                "description": t.description,
                "buy_sell": t.buy_sell,
                "quantity": t.quantity,
                "price": t.price,
                "proceeds": t.proceeds,
                "commission": t.commission,
                "net_cash": t.net_cash,
                "realized_pnl": t.realized_pnl,
                "currency": t.currency,
            }
            for t in trades
        ],
        "count": len(trades),
    }


def get_flex_pnl(flex: FlexConfig, *, days: int = 30) -> Dict:
    xml = fetch_statement_xml(flex, days=days)
    pnls = parse_symbol_pnls(xml)
    total_realized = sum(p.realized_pnl for p in pnls)
    total_unrealized = sum(p.unrealized_pnl for p in pnls)
    return {
        "rows": [
            {
                "symbol": p.symbol,
                "description": p.description,
                "realized_pnl": p.realized_pnl,
                "unrealized_pnl": p.unrealized_pnl,
                "total_pnl": p.total_pnl,
            }
            for p in pnls
        ],
        "total_realized": total_realized,
        "total_unrealized": total_unrealized,
        "total_pnl": total_realized + total_unrealized,
        "count": len(pnls),
    }


def get_flex_transfers(flex: FlexConfig, *, days: int = 90) -> Dict:
    xml = fetch_statement_xml(flex, days=days)
    transfers = parse_transfers(xml)
    return {
        "rows": [
            {
                "date": t.date,
                "type": t.type,
                "amount": t.amount,
                "currency": t.currency,
                "description": t.description,
            }
            for t in transfers
        ],
        "count": len(transfers),
    }


def get_flex_cash_transactions(flex: FlexConfig, *, days: int = 30) -> Dict:
    xml = fetch_statement_xml(flex, days=days)
    items = parse_cash_transactions(xml)
    return {
        "rows": [
            {
                "date": t.date,
                "symbol": t.symbol,
                "description": t.description,
                "type": t.transaction_type,
                "amount": t.amount,
                "currency": t.currency,
            }
            for t in items
        ],
        "count": len(items),
    }
