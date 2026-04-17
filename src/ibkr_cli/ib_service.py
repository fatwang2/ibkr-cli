from __future__ import annotations

import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from datetime import datetime, timezone

from ibkr_cli.config import ProfileConfig

DEFAULT_ACCOUNT_SUMMARY_TAGS = (
    "NetLiquidation",
    "TotalCashValue",
    "BuyingPower",
    "AvailableFunds",
    "ExcessLiquidity",
    "InitMarginReq",
    "MaintMarginReq",
    "GrossPositionValue",
    "UnrealizedPnL",
    "RealizedPnL",
)

_FUTURE_SYMBOL_RE = re.compile(r"^(?P<root>[A-Z0-9]{1,4})(?P<month>[FGHJKMNQUVXZ])(?P<year>\d)$")
_OPTION_OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<date>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")
_OPTION_SHORT_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<date>\d{6})(?P<right>[CP])(?P<strike>\d+(?:\.\d+)?)$")
_IB_EXPIRY_WITH_TZ_RE = re.compile(r"^(?P<date>\d{8}) (?P<time>\d{2}:\d{2}:\d{2}) (?P<tz>[A-Za-z_./+-]+)$")


@dataclass(frozen=True)
class ApiConnectionResult:
    ok: bool
    host: str
    port: int
    client_id: int
    timeout: float
    managed_accounts: List[str]
    latency_ms: Optional[float] = None
    server_version: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OptionSymbolParts:
    underlying: str
    expiration: str
    right: str
    strike: float
    normalized_input: str
    normalized_occ_symbol: str


def _normalize_trade_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _parse_option_date(value: str) -> str:
    return f"20{value}"


def _occ_strike_to_float(value: str) -> float:
    return int(value) / 1000.0


def _format_occ_strike(value: str) -> tuple[float, str]:
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid option strike '{value}'.") from exc
    if decimal_value < 0:
        raise ValueError(f"Invalid option strike '{value}'.")
    milli_value = decimal_value * 1000
    if milli_value != milli_value.to_integral_value():
        raise ValueError(
            f"Option strike '{value}' has too many decimal places. Use at most 3 decimal places."
        )
    strike_int = int(milli_value)
    return float(decimal_value), f"{strike_int:08d}"


def _parse_option_symbol(symbol: str) -> Optional[OptionSymbolParts]:
    normalized = _normalize_trade_symbol(symbol)

    occ_match = _OPTION_OCC_RE.match(normalized)
    if occ_match:
        strike = _occ_strike_to_float(occ_match.group("strike"))
        return OptionSymbolParts(
            underlying=occ_match.group("root"),
            expiration=_parse_option_date(occ_match.group("date")),
            right=occ_match.group("right"),
            strike=strike,
            normalized_input=normalized,
            normalized_occ_symbol=normalized,
        )

    short_match = _OPTION_SHORT_RE.match(normalized)
    if not short_match:
        return None

    strike, occ_strike = _format_occ_strike(short_match.group("strike"))
    return OptionSymbolParts(
        underlying=short_match.group("root"),
        expiration=_parse_option_date(short_match.group("date")),
        right=short_match.group("right"),
        strike=strike,
        normalized_input=normalized,
        normalized_occ_symbol=(
            f"{short_match.group('root')}{short_match.group('date')}"
            f"{short_match.group('right')}{occ_strike}"
        ),
    )


def _is_forex_pair_symbol(symbol: str) -> bool:
    normalized = _normalize_trade_symbol(symbol)
    return len(normalized) == 6 and normalized.isalpha()


def _future_symbol_match(symbol: str) -> Optional[re.Match[str]]:
    normalized = _normalize_trade_symbol(symbol)
    return _FUTURE_SYMBOL_RE.match(normalized)


def _detect_trade_contract_kind(symbol: str) -> str:
    normalized = _normalize_trade_symbol(symbol)
    if _parse_option_symbol(normalized):
        return "OPT"
    if _is_forex_pair_symbol(normalized):
        return "CASH"
    if _future_symbol_match(normalized):
        return "FUT"
    return "STK"


def _build_trade_contract(symbol: str, exchange: str, currency: str) -> tuple[str, object]:
    try:
        from ib_async import Contract, Forex, Option, Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    normalized_symbol = _normalize_trade_symbol(symbol)
    contract_kind = _detect_trade_contract_kind(normalized_symbol)

    if contract_kind == "OPT":
        parts = _parse_option_symbol(normalized_symbol)
        if parts is None:
            raise RuntimeError(f"Unable to parse option symbol '{normalized_symbol}'.")
        resolved_exchange = exchange.upper() if exchange else "SMART"
        resolved_currency = currency.upper() if currency else "USD"
        return contract_kind, Option(
            parts.underlying,
            parts.expiration,
            parts.strike,
            parts.right,
            resolved_exchange,
            currency=resolved_currency,
        )

    if contract_kind == "CASH":
        resolved_exchange = exchange.upper() if exchange and exchange.upper() != "SMART" else "IDEALPRO"
        return contract_kind, Forex(normalized_symbol, exchange=resolved_exchange)

    if contract_kind == "FUT":
        resolved_exchange = exchange.upper() if exchange and exchange.upper() != "SMART" else ""
        return contract_kind, Contract(
            secType="FUT",
            localSymbol=normalized_symbol,
            exchange=resolved_exchange,
            currency=currency.upper(),
        )

    return contract_kind, Stock(symbol=normalized_symbol, exchange=exchange.upper(), currency=currency.upper())


def _normalize_contract_for_order(contract: object) -> object:
    expiry = getattr(contract, "lastTradeDateOrContractMonth", None)
    if not isinstance(expiry, str) or not expiry:
        return contract

    match = _IB_EXPIRY_WITH_TZ_RE.match(expiry.strip())
    if not match:
        return contract

    # IB can qualify certain futures with a trailing timezone token such as "GB",
    # but what-if/place-order expects the plain "yyyyMMdd HH:mm:ss" variant.
    setattr(contract, "lastTradeDateOrContractMonth", f"{match.group('date')} {match.group('time')}")
    return contract


def _qualify_trade_contract(ib: object, symbol: str, exchange: str, currency: str) -> tuple[object, str]:
    contract_kind, contract = _build_trade_contract(symbol=symbol, exchange=exchange, currency=currency)
    qualified_contract = None
    qualify_async = getattr(ib, "qualifyContractsAsync", None)
    runner = getattr(ib, "_run", None)

    if callable(qualify_async) and callable(runner):
        qualified = runner(qualify_async(contract, returnAll=True))
        if isinstance(qualified, (list, tuple)) and qualified:
            first_result = qualified[0]
            if isinstance(first_result, list):
                normalized_symbol = _normalize_trade_symbol(symbol)
                exchanges = sorted(
                    {
                        str(current_exchange).upper()
                        for candidate in first_result
                        for current_exchange in [getattr(candidate, "exchange", None)]
                        if current_exchange
                    }
                )
                possible_contracts = ", ".join(
                    filter(
                        None,
                        (
                            " / ".join(
                                filter(
                                    None,
                                    (
                                        getattr(candidate, "localSymbol", None),
                                        getattr(candidate, "exchange", None),
                                        getattr(candidate, "lastTradeDateOrContractMonth", None),
                                    ),
                                )
                            )
                            for candidate in first_result
                            if candidate is not None
                        ),
                    )
                )
                exchange_hints = ", ".join(f"--exchange {current_exchange}" for current_exchange in exchanges)
                if exchange_hints:
                    suggestion = f" Retry with one of: {exchange_hints}."
                else:
                    suggestion = ""
                raise RuntimeError(
                    f"Ambiguous contract for symbol '{normalized_symbol}'. "
                    f"IBKR returned multiple matches.{suggestion} "
                    f"Matches: {possible_contracts or 'multiple candidates'}."
                )
            if first_result is not None:
                qualified_contract = first_result
    if qualified_contract is None:
        qualified = ib.qualifyContracts(contract)
        if qualified and qualified[0] is not None:
            qualified_contract = qualified[0]

    if qualified_contract is not None:
        return _normalize_contract_for_order(qualified_contract), contract_kind

    normalized_symbol = _normalize_trade_symbol(symbol)
    if contract_kind == "OPT":
        raise RuntimeError(
            f"Symbol '{normalized_symbol}' looks like an OCC option symbol, but IBKR could not qualify it."
        )
    if contract_kind == "CASH":
        raise RuntimeError(
            f"Symbol '{normalized_symbol}' looks like a forex pair, but IBKR could not qualify it."
        )
    if contract_kind == "FUT":
        normalized_exchange = (exchange or "").strip().upper()
        if normalized_exchange and normalized_exchange != "SMART":
            raise RuntimeError(
                f"IBKR could not qualify futures symbol '{normalized_symbol}' on exchange '{normalized_exchange}'. "
                "The exchange may be incorrect for this contract. Try removing --exchange or using the correct exchange."
            )
        raise RuntimeError(
            f"Symbol '{normalized_symbol}' looks like a futures code, but IBKR could not qualify it."
        )
    raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")


def _ib_class() -> Tuple[object, object]:
    try:
        from ib_async import IB, StartupFetchNONE
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc
    return IB, StartupFetchNONE


def _normalize_number(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isnan(numeric) or math.isinf(numeric) or abs(numeric) > 1e100:
            return None
        return numeric
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric) or abs(numeric) > 1e100:
        return None
    return numeric


@contextmanager
def _capture_ib_errors(
    ib: object, matcher: Optional[Callable[[object], bool]] = None
) -> Iterator[List[Dict[str, object]]]:
    errors: List[Dict[str, object]] = []

    def on_error(req_id: int, error_code: int, error_string: str, contract: object) -> None:
        if matcher is not None and not matcher(contract):
            return
        contract_payload = None
        if contract is not None:
            contract_payload = {
                "con_id": getattr(contract, "conId", None),
                "symbol": getattr(contract, "symbol", None),
                "local_symbol": getattr(contract, "localSymbol", None),
                "exchange": getattr(contract, "exchange", None),
                "primary_exchange": getattr(contract, "primaryExchange", None),
                "currency": getattr(contract, "currency", None),
                "sec_type": getattr(contract, "secType", None),
            }
        errors.append(
            {
                "req_id": req_id,
                "code": error_code,
                "message": error_string,
                "contract": contract_payload,
            }
        )

    ib.errorEvent.connect(on_error)
    try:
        yield errors
    finally:
        ib.errorEvent.disconnect(on_error)


@contextmanager
def _suppress_ib_async_logs() -> Iterator[None]:
    logger_names = ("ib_async.client", "ib_async.wrapper", "ib_async.ib")
    previous_states = []
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        previous_states.append((logger, logger.disabled, logger.level))
        logger.disabled = True
        logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        for logger, previous_disabled, previous_level in previous_states:
            logger.disabled = previous_disabled
            logger.setLevel(previous_level)


@contextmanager
def ib_session(profile: ProfileConfig, timeout: float = 4.0, readonly: bool = True) -> Iterator[object]:
    ib_class, startup_fetch_none = _ib_class()
    ib = ib_class()
    try:
        with _suppress_ib_async_logs():
            ib.connect(
                profile.host,
                profile.port,
                clientId=profile.client_id,
                timeout=timeout,
                readonly=readonly,
                fetchFields=startup_fetch_none,
            )
        yield ib
    finally:
        if getattr(ib, "isConnected", None) and ib.isConnected():
            ib.disconnect()


def check_api_connection(profile: ProfileConfig, timeout: float = 4.0) -> ApiConnectionResult:
    ib_class, startup_fetch_none = _ib_class()
    ib = ib_class()
    started = time.perf_counter()
    try:
        with _suppress_ib_async_logs():
            ib.connect(
                profile.host,
                profile.port,
                clientId=profile.client_id,
                timeout=timeout,
                readonly=True,
                fetchFields=startup_fetch_none,
            )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        managed_accounts = list(ib.managedAccounts())
        server_version = None
        client = getattr(ib, "client", None)
        if client and hasattr(client, "serverVersion"):
            server_version = client.serverVersion()
        return ApiConnectionResult(
            ok=True,
            host=profile.host,
            port=profile.port,
            client_id=profile.client_id,
            timeout=timeout,
            managed_accounts=managed_accounts,
            latency_ms=latency_ms,
            server_version=server_version,
        )
    except Exception as exc:
        return ApiConnectionResult(
            ok=False,
            host=profile.host,
            port=profile.port,
            client_id=profile.client_id,
            timeout=timeout,
            managed_accounts=[],
            error=str(exc),
        )
    finally:
        if getattr(ib, "isConnected", None) and ib.isConnected():
            ib.disconnect()


def _resolve_account(ib: object, requested_account: Optional[str]) -> tuple[List[str], str]:
    managed_accounts = list(ib.managedAccounts())
    if requested_account:
        if managed_accounts and requested_account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{requested_account}'. Available accounts: {available}")
        return managed_accounts, requested_account
    if not managed_accounts:
        raise RuntimeError("No managed accounts were returned by IBKR.")
    return managed_accounts, managed_accounts[0]


def get_account_summary(
    profile: ProfileConfig,
    timeout: float = 4.0,
    account: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        managed_accounts, selected_account = _resolve_account(ib, account)
        summary = ib.accountSummary(selected_account)

        if tags is None:
            selected_tags = list(DEFAULT_ACCOUNT_SUMMARY_TAGS)
        elif len(tags) == 0:
            selected_tags = None
        else:
            selected_tags = list(tags)

        tag_order = {tag: index for index, tag in enumerate(selected_tags or [])}
        rows = []
        for item in summary:
            if selected_tags is not None and item.tag not in tag_order:
                continue
            rows.append(
                {
                    "account": item.account,
                    "tag": item.tag,
                    "value": item.value,
                    "currency": item.currency,
                }
            )

        if selected_tags is not None:
            rows.sort(key=lambda row: (tag_order.get(row["tag"], 9999), str(row["currency"]), str(row["account"])))
        else:
            rows.sort(key=lambda row: (str(row["tag"]), str(row["currency"]), str(row["account"])))

        return {
            "managed_accounts": managed_accounts,
            "selected_account": selected_account,
            "tags": selected_tags,
            "rows": rows,
        }


def get_positions(
    profile: ProfileConfig,
    timeout: float = 4.0,
    account: Optional[str] = None,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        managed_accounts = list(ib.managedAccounts())
        if account and managed_accounts and account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{account}'. Available accounts: {available}")

        positions = list(ib.positions())
        if account:
            positions = [position for position in positions if position.account == account]

        rows = []
        for position in positions:
            contract = position.contract
            rows.append(
                {
                    "account": position.account,
                    "symbol": contract.symbol,
                    "local_symbol": contract.localSymbol,
                    "sec_type": contract.secType,
                    "exchange": contract.exchange,
                    "currency": contract.currency,
                    "position": position.position,
                    "avg_cost": position.avgCost,
                    "con_id": contract.conId,
                }
            )

        rows.sort(key=lambda row: (str(row["account"]), str(row["symbol"]), str(row["sec_type"]), str(row["local_symbol"])))

        return {
            "managed_accounts": managed_accounts,
            "selected_account": account,
            "rows": rows,
        }


def get_open_orders(
    profile: ProfileConfig,
    timeout: float = 4.0,
    account: Optional[str] = None,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        managed_accounts = list(ib.managedAccounts())
        if account and managed_accounts and account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{account}'. Available accounts: {available}")

        trades = list(ib.reqAllOpenOrders())
        rows = []
        for trade in trades:
            order = trade.order
            contract = trade.contract
            status = trade.orderStatus
            if account and order.account != account:
                continue
            rows.append(
                {
                    "account": order.account,
                    "order_id": order.orderId,
                    "perm_id": order.permId,
                    "client_id": order.clientId,
                    "symbol": contract.symbol,
                    "local_symbol": contract.localSymbol,
                    "sec_type": contract.secType,
                    "exchange": contract.exchange,
                    "currency": contract.currency,
                    "action": order.action,
                    "quantity": _normalize_number(order.totalQuantity),
                    "order_type": order.orderType,
                    "limit_price": _normalize_number(order.lmtPrice),
                    "aux_price": _normalize_number(order.auxPrice),
                    "tif": order.tif,
                    "status": status.status,
                    "filled": _normalize_number(status.filled),
                    "remaining": _normalize_number(status.remaining),
                }
            )

        rows.sort(
            key=lambda row: (
                str(row["account"]),
                str(row["symbol"]),
                int(row["order_id"]),
            )
        )

        return {
            "managed_accounts": managed_accounts,
            "selected_account": account,
            "rows": rows,
        }


def get_completed_orders(
    profile: ProfileConfig,
    timeout: float = 4.0,
    account: Optional[str] = None,
    api_only: bool = False,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        managed_accounts = list(ib.managedAccounts())
        if account and managed_accounts and account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{account}'. Available accounts: {available}")

        trades = list(ib.reqCompletedOrders(api_only))
        fills = list(ib.reqExecutions())
        executions_by_perm_id: Dict[int, Dict[str, Optional[float]]] = {}
        for fill in fills:
            execution = fill.execution
            if account and execution.acctNumber != account:
                continue
            existing = executions_by_perm_id.get(execution.permId)
            fill_payload: Dict[str, Optional[float]] = {
                "order_id": float(execution.orderId),
                "client_id": float(execution.clientId),
                "shares": _normalize_number(execution.shares),
                "avg_price": _normalize_number(execution.avgPrice),
            }
            if existing is None or fill.time > existing["time"]:  # type: ignore[index]
                executions_by_perm_id[execution.permId] = {
                    "time": fill.time,
                    **fill_payload,
                }
        rows = []
        for trade in trades:
            order = trade.order
            contract = trade.contract
            status = trade.orderStatus
            if account and order.account != account:
                continue
            execution = executions_by_perm_id.get(order.permId)
            quantity = _normalize_number(order.totalQuantity)
            filled_quantity = _normalize_number(getattr(order, "filledQuantity", None))
            if quantity in (None, 0.0) and filled_quantity not in (None, 0.0):
                quantity = filled_quantity

            filled = _normalize_number(status.filled)
            if filled in (None, 0.0) and filled_quantity not in (None, 0.0):
                filled = filled_quantity

            remaining = _normalize_number(status.remaining)
            if remaining is None:
                if quantity is not None and filled is not None:
                    remaining = max(quantity - filled, 0.0)
            elif remaining == 0.0 and status.status == "Cancelled" and quantity is not None and filled is not None:
                remaining = max(quantity - filled, 0.0)

            avg_fill_price = _normalize_number(status.avgFillPrice)
            if avg_fill_price in (None, 0.0) and execution is not None:
                avg_fill_price = execution["avg_price"]

            order_id = order.orderId
            if order_id == 0 and execution is not None and execution["order_id"] is not None:
                order_id = int(execution["order_id"])

            client_id = order.clientId
            if client_id == 0 and execution is not None and execution["client_id"] is not None:
                client_id = int(execution["client_id"])

            rows.append(
                {
                    "account": order.account,
                    "order_id": order_id,
                    "perm_id": order.permId,
                    "client_id": client_id,
                    "symbol": contract.symbol,
                    "local_symbol": contract.localSymbol,
                    "sec_type": contract.secType,
                    "exchange": contract.exchange,
                    "currency": contract.currency,
                    "action": order.action,
                    "quantity": quantity,
                    "order_type": order.orderType,
                    "limit_price": _normalize_number(order.lmtPrice),
                    "aux_price": _normalize_number(order.auxPrice),
                    "tif": order.tif,
                    "status": status.status,
                    "filled": filled,
                    "remaining": remaining,
                    "avg_fill_price": avg_fill_price,
                }
            )

        rows.sort(
            key=lambda row: (
                str(row["account"]),
                str(row["symbol"]),
                int(row["order_id"]),
            )
        )

        return {
            "managed_accounts": managed_accounts,
            "selected_account": account,
            "api_only": api_only,
            "rows": rows,
        }


def get_executions(
    profile: ProfileConfig,
    timeout: float = 4.0,
    account: Optional[str] = None,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        managed_accounts = list(ib.managedAccounts())
        if account and managed_accounts and account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{account}'. Available accounts: {available}")

        fills = list(ib.reqExecutions())
        rows = []
        for fill in fills:
            contract = fill.contract
            execution = fill.execution
            commission_report = fill.commissionReport
            if account and execution.acctNumber != account:
                continue
            rows.append(
                {
                    "account": execution.acctNumber,
                    "time": fill.time.isoformat(),
                    "exec_id": execution.execId,
                    "order_id": execution.orderId,
                    "perm_id": execution.permId,
                    "client_id": execution.clientId,
                    "symbol": contract.symbol,
                    "local_symbol": contract.localSymbol,
                    "sec_type": contract.secType,
                    "exchange": execution.exchange or contract.exchange,
                    "currency": contract.currency,
                    "side": execution.side,
                    "shares": _normalize_number(execution.shares),
                    "price": _normalize_number(execution.price),
                    "cum_qty": _normalize_number(execution.cumQty),
                    "avg_price": _normalize_number(execution.avgPrice),
                    "commission": _normalize_number(commission_report.commission),
                    "commission_currency": commission_report.currency,
                    "realized_pnl": _normalize_number(commission_report.realizedPNL),
                }
            )

        rows.sort(
            key=lambda row: (
                str(row["account"]),
                str(row["time"]),
                str(row["exec_id"]),
            ),
            reverse=True,
        )

        return {
            "managed_accounts": managed_accounts,
            "selected_account": account,
            "rows": rows,
        }


_SUPPORTED_ORDER_TYPES = ("MKT", "LMT", "STP", "STP LMT", "TRAIL")


def _prepare_order(
    ib: object,
    action: str,
    symbol: str,
    quantity: float,
    exchange: str,
    currency: str,
    order_type: str,
    limit_price: Optional[float],
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    stop_price: Optional[float] = None,
    trail_stop_price: Optional[float] = None,
    trail_percent: Optional[float] = None,
) -> tuple[List[str], str, object, object]:
    try:
        from ib_async import LimitOrder, MarketOrder, Order, StopLimitOrder, StopOrder
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    normalized_action = action.upper()
    normalized_order_type = order_type.upper()
    normalized_tif = tif.upper()

    if normalized_action not in ("BUY", "SELL"):
        raise ValueError(f"Unsupported action '{action}'. Use BUY or SELL.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if normalized_order_type not in _SUPPORTED_ORDER_TYPES:
        types = ", ".join(_SUPPORTED_ORDER_TYPES)
        raise ValueError(f"Unsupported order type '{order_type}'. Use one of: {types}.")

    # --- per-type parameter validation ---
    if normalized_order_type == "LMT":
        if limit_price is None or limit_price <= 0:
            raise ValueError("A positive --limit price is required for LMT orders.")
        if stop_price is not None:
            raise ValueError("--stop cannot be used with --type LMT. Use STP LMT instead.")
    elif normalized_order_type == "MKT":
        if limit_price is not None:
            raise ValueError("--limit cannot be used with --type MKT.")
        if stop_price is not None:
            raise ValueError("--stop cannot be used with --type MKT. Use STP instead.")
    elif normalized_order_type == "STP":
        if stop_price is None or stop_price <= 0:
            raise ValueError("A positive --stop price is required for STP orders.")
        if limit_price is not None:
            raise ValueError("--limit cannot be used with --type STP. Use STP LMT instead.")
    elif normalized_order_type == "STP LMT":
        if stop_price is None or stop_price <= 0:
            raise ValueError("A positive --stop price is required for STP LMT orders.")
        if limit_price is None or limit_price <= 0:
            raise ValueError("A positive --limit price is required for STP LMT orders.")
    elif normalized_order_type == "TRAIL":
        if trail_stop_price is not None and trail_percent is not None:
            raise ValueError("Use --trail-amount or --trail-percent, not both.")
        if trail_stop_price is None and trail_percent is None:
            raise ValueError("--trail-amount or --trail-percent is required for TRAIL orders.")
        if trail_stop_price is not None and trail_stop_price <= 0:
            raise ValueError("--trail-amount must be positive.")
        if trail_percent is not None and trail_percent <= 0:
            raise ValueError("--trail-percent must be positive.")

    # trail params should only be used with TRAIL
    if normalized_order_type != "TRAIL" and (trail_stop_price is not None or trail_percent is not None):
        raise ValueError("--trail-amount and --trail-percent can only be used with --type TRAIL.")

    managed_accounts, selected_account = _resolve_account(ib, account)
    qualified_contract, _ = _qualify_trade_contract(ib, symbol=symbol, exchange=exchange, currency=currency)
    common_kwargs = dict(tif=normalized_tif, outsideRth=outside_rth, account=selected_account)

    if normalized_order_type == "LMT":
        order = LimitOrder(normalized_action, quantity, limit_price, **common_kwargs)
    elif normalized_order_type == "STP":
        order = StopOrder(normalized_action, quantity, stop_price, **common_kwargs)
    elif normalized_order_type == "STP LMT":
        order = StopLimitOrder(normalized_action, quantity, limit_price, stop_price, **common_kwargs)
    elif normalized_order_type == "TRAIL":
        order = Order(
            orderType="TRAIL",
            action=normalized_action,
            totalQuantity=quantity,
            **common_kwargs,
        )
        if trail_stop_price is not None:
            order.auxPrice = trail_stop_price
        if trail_percent is not None:
            order.trailingPercent = trail_percent
        if stop_price is not None:
            order.trailStopPrice = stop_price
    else:
        order = MarketOrder(normalized_action, quantity, **common_kwargs)

    return managed_accounts, selected_account, qualified_contract, order


def _prepare_bracket_order(
    ib: object,
    action: str,
    symbol: str,
    quantity: float,
    exchange: str,
    currency: str,
    order_type: str,
    limit_price: Optional[float],
    tif: str,
    outside_rth: bool,
    account: Optional[str],
    take_profit_price: float,
    stop_loss_price: float,
) -> tuple[List[str], str, object, list]:
    try:
        from ib_async import LimitOrder, MarketOrder, StopOrder
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    normalized_action = action.upper()
    normalized_order_type = order_type.upper()
    normalized_tif = tif.upper()
    reverse_action = "SELL" if normalized_action == "BUY" else "BUY"

    if normalized_action not in ("BUY", "SELL"):
        raise ValueError(f"Unsupported action '{action}'. Use BUY or SELL.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    if normalized_order_type not in ("MKT", "LMT"):
        raise ValueError("Bracket orders only support MKT or LMT as the parent order type.")
    if normalized_order_type == "LMT" and (limit_price is None or limit_price <= 0):
        raise ValueError("A positive --limit price is required for LMT bracket orders.")
    if take_profit_price <= 0:
        raise ValueError("--take-profit price must be positive.")
    if stop_loss_price <= 0:
        raise ValueError("--stop-loss price must be positive.")

    managed_accounts, selected_account = _resolve_account(ib, account)
    qualified_contract, _ = _qualify_trade_contract(ib, symbol=symbol, exchange=exchange, currency=currency)
    common_kwargs = dict(tif=normalized_tif, outsideRth=outside_rth, account=selected_account)

    # Parent order
    if normalized_order_type == "LMT":
        parent = LimitOrder(normalized_action, quantity, limit_price, **common_kwargs)
    else:
        parent = MarketOrder(normalized_action, quantity, **common_kwargs)
    parent.transmit = False

    # Take-profit child (limit order on the opposite side)
    take_profit = LimitOrder(reverse_action, quantity, take_profit_price, **common_kwargs)
    take_profit.transmit = False

    # Stop-loss child (stop order on the opposite side)
    stop_loss = StopOrder(reverse_action, quantity, stop_loss_price, **common_kwargs)
    stop_loss.transmit = True  # last child transmits the whole bracket

    return managed_accounts, selected_account, qualified_contract, [parent, take_profit, stop_loss]


def _trade_payload(
    trade: object,
    managed_accounts: List[str],
    selected_account: str,
    raw_errors: List[Dict[str, object]],
    operation: str,
) -> Dict[str, object]:
    contract = trade.contract
    order = trade.order
    status = trade.orderStatus
    return {
        "operation": operation,
        "preview_only": False,
        "managed_accounts": managed_accounts,
        "selected_account": selected_account,
        "symbol": contract.symbol,
        "local_symbol": contract.localSymbol,
        "exchange": contract.exchange,
        "primary_exchange": contract.primaryExchange,
        "currency": contract.currency,
        "sec_type": contract.secType,
        "con_id": contract.conId,
        "action": order.action,
        "quantity": _normalize_number(order.totalQuantity),
        "order_type": order.orderType,
        "limit_price": _normalize_number(order.lmtPrice),
        "aux_price": _normalize_number(order.auxPrice),
        "trailing_percent": _normalize_number(getattr(order, "trailingPercent", None)),
        "trail_stop_price": _normalize_number(getattr(order, "trailStopPrice", None)),
        "parent_id": order.parentId if order.parentId else None,
        "tif": order.tif,
        "outside_rth": bool(order.outsideRth),
        "order_id": order.orderId,
        "perm_id": order.permId,
        "client_id": order.clientId,
        "status": status.status,
        "filled": _normalize_number(status.filled),
        "remaining": _normalize_number(status.remaining),
        "avg_fill_price": _normalize_number(status.avgFillPrice),
        "is_active": trade.isActive(),
        "is_done": trade.isDone(),
        "advanced_error": trade.advancedError or None,
        "raw_error_codes": sorted({int(error["code"]) for error in raw_errors}),
        "raw_errors": raw_errors,
    }


def preview_order(
    profile: ProfileConfig,
    action: str,
    symbol: str,
    quantity: float,
    exchange: str = "SMART",
    currency: str = "USD",
    order_type: str = "MKT",
    limit_price: Optional[float] = None,
    tif: str = "DAY",
    outside_rth: bool = False,
    timeout: float = 4.0,
    account: Optional[str] = None,
    stop_price: Optional[float] = None,
    trail_stop_price: Optional[float] = None,
    trail_percent: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
) -> Dict[str, object]:
    is_bracket = take_profit_price is not None or stop_loss_price is not None
    with ib_session(profile, timeout=timeout, readonly=False) as ib:
        if is_bracket:
            if take_profit_price is None or stop_loss_price is None:
                raise ValueError("Bracket orders require both --take-profit and --stop-loss.")
            managed_accounts, selected_account, qualified_contract, orders = _prepare_bracket_order(
                ib,
                action=action,
                symbol=symbol,
                quantity=quantity,
                exchange=exchange,
                currency=currency,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                outside_rth=outside_rth,
                account=account,
                take_profit_price=take_profit_price,
                stop_loss_price=stop_loss_price,
            )
            order = orders[0]  # preview the parent order
            order.transmit = True  # override for whatIfOrder preview
        else:
            managed_accounts, selected_account, qualified_contract, order = _prepare_order(
                ib,
                action=action,
                symbol=symbol,
                quantity=quantity,
                exchange=exchange,
                currency=currency,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                outside_rth=outside_rth,
                account=account,
                stop_price=stop_price,
                trail_stop_price=trail_stop_price,
                trail_percent=trail_percent,
            )

        matcher = lambda current_contract: current_contract is not None and getattr(current_contract, "conId", None) == qualified_contract.conId
        with _capture_ib_errors(ib, matcher) as raw_errors:
            with _suppress_ib_async_logs():
                state = ib.whatIfOrder(qualified_contract, order)

        if not hasattr(state, "status"):
            error_messages = [str(error["message"]) for error in raw_errors if error.get("message")]
            if error_messages:
                raise RuntimeError(
                    "IBKR could not preview this order. " + " | ".join(dict.fromkeys(error_messages))
                )
            raise RuntimeError("IBKR could not preview this order.")

        result = {
            "preview_only": True,
            "managed_accounts": managed_accounts,
            "selected_account": selected_account,
            "symbol": qualified_contract.symbol,
            "local_symbol": qualified_contract.localSymbol,
            "exchange": qualified_contract.exchange,
            "primary_exchange": qualified_contract.primaryExchange,
            "currency": qualified_contract.currency,
            "sec_type": qualified_contract.secType,
            "con_id": qualified_contract.conId,
            "action": order.action,
            "quantity": _normalize_number(quantity),
            "order_type": order.orderType,
            "limit_price": _normalize_number(limit_price),
            "stop_price": _normalize_number(stop_price),
            "aux_price": _normalize_number(order.auxPrice),
            "trailing_percent": _normalize_number(getattr(order, "trailingPercent", None)),
            "tif": order.tif,
            "outside_rth": outside_rth,
            "status": state.status,
            "init_margin_before": _normalize_number(state.initMarginBefore),
            "init_margin_change": _normalize_number(state.initMarginChange),
            "init_margin_after": _normalize_number(state.initMarginAfter),
            "maint_margin_before": _normalize_number(state.maintMarginBefore),
            "maint_margin_change": _normalize_number(state.maintMarginChange),
            "maint_margin_after": _normalize_number(state.maintMarginAfter),
            "equity_with_loan_before": _normalize_number(state.equityWithLoanBefore),
            "equity_with_loan_change": _normalize_number(state.equityWithLoanChange),
            "equity_with_loan_after": _normalize_number(state.equityWithLoanAfter),
            "commission": _normalize_number(state.commission),
            "min_commission": _normalize_number(state.minCommission),
            "max_commission": _normalize_number(state.maxCommission),
            "commission_currency": state.commissionCurrency,
            "warning_text": state.warningText or None,
            "raw_error_codes": sorted({int(error["code"]) for error in raw_errors}),
            "raw_errors": raw_errors,
        }
        if is_bracket:
            result["bracket"] = {
                "take_profit_price": _normalize_number(take_profit_price),
                "stop_loss_price": _normalize_number(stop_loss_price),
            }
        return result


def submit_order(
    profile: ProfileConfig,
    action: str,
    symbol: str,
    quantity: float,
    exchange: str = "SMART",
    currency: str = "USD",
    order_type: str = "MKT",
    limit_price: Optional[float] = None,
    tif: str = "DAY",
    outside_rth: bool = False,
    timeout: float = 4.0,
    account: Optional[str] = None,
    stop_price: Optional[float] = None,
    trail_stop_price: Optional[float] = None,
    trail_percent: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
) -> Dict[str, object]:
    is_bracket = take_profit_price is not None or stop_loss_price is not None
    with ib_session(profile, timeout=timeout, readonly=False) as ib:
        if is_bracket:
            if take_profit_price is None or stop_loss_price is None:
                raise ValueError("Bracket orders require both --take-profit and --stop-loss.")
            managed_accounts, selected_account, qualified_contract, orders = _prepare_bracket_order(
                ib,
                action=action,
                symbol=symbol,
                quantity=quantity,
                exchange=exchange,
                currency=currency,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                outside_rth=outside_rth,
                account=account,
                take_profit_price=take_profit_price,
                stop_loss_price=stop_loss_price,
            )
            with _capture_ib_errors(ib) as raw_errors:
                with _suppress_ib_async_logs():
                    parent_order, tp_order, sl_order = orders
                    # Place parent first to obtain its orderId
                    parent_trade = ib.placeOrder(qualified_contract, parent_order)
                    # Link children to parent via parentId
                    tp_order.parentId = parent_trade.order.orderId
                    sl_order.parentId = parent_trade.order.orderId
                    tp_trade = ib.placeOrder(qualified_contract, tp_order)
                    sl_trade = ib.placeOrder(qualified_contract, sl_order)
                    trades = [parent_trade, tp_trade, sl_trade]
                    ib.waitOnUpdate(timeout=min(timeout, 0.75))

            parent_payload = _trade_payload(trades[0], managed_accounts, selected_account, raw_errors, "submit")
            parent_payload["bracket"] = {
                "take_profit": _trade_payload(trades[1], managed_accounts, selected_account, [], "submit"),
                "stop_loss": _trade_payload(trades[2], managed_accounts, selected_account, [], "submit"),
            }
            return parent_payload
        else:
            managed_accounts, selected_account, qualified_contract, order = _prepare_order(
                ib,
                action=action,
                symbol=symbol,
                quantity=quantity,
                exchange=exchange,
                currency=currency,
                order_type=order_type,
                limit_price=limit_price,
                tif=tif,
                outside_rth=outside_rth,
                account=account,
                stop_price=stop_price,
                trail_stop_price=trail_stop_price,
                trail_percent=trail_percent,
            )

            with _capture_ib_errors(ib) as raw_errors:
                with _suppress_ib_async_logs():
                    trade = ib.placeOrder(qualified_contract, order)
                    ib.waitOnUpdate(timeout=min(timeout, 0.75))

            return _trade_payload(trade, managed_accounts, selected_account, raw_errors, "submit")


def preview_stock_order(
    profile: ProfileConfig,
    action: str,
    symbol: str,
    quantity: float,
    exchange: str = "SMART",
    currency: str = "USD",
    order_type: str = "MKT",
    limit_price: Optional[float] = None,
    tif: str = "DAY",
    outside_rth: bool = False,
    timeout: float = 4.0,
    account: Optional[str] = None,
    stop_price: Optional[float] = None,
    trail_stop_price: Optional[float] = None,
    trail_percent: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
) -> Dict[str, object]:
    return preview_order(
        profile=profile,
        action=action,
        symbol=symbol,
        quantity=quantity,
        exchange=exchange,
        currency=currency,
        order_type=order_type,
        limit_price=limit_price,
        tif=tif,
        outside_rth=outside_rth,
        timeout=timeout,
        account=account,
        stop_price=stop_price,
        trail_stop_price=trail_stop_price,
        trail_percent=trail_percent,
        take_profit_price=take_profit_price,
        stop_loss_price=stop_loss_price,
    )


def submit_stock_order(
    profile: ProfileConfig,
    action: str,
    symbol: str,
    quantity: float,
    exchange: str = "SMART",
    currency: str = "USD",
    order_type: str = "MKT",
    limit_price: Optional[float] = None,
    tif: str = "DAY",
    outside_rth: bool = False,
    timeout: float = 4.0,
    account: Optional[str] = None,
    stop_price: Optional[float] = None,
    trail_stop_price: Optional[float] = None,
    trail_percent: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
) -> Dict[str, object]:
    return submit_order(
        profile=profile,
        action=action,
        symbol=symbol,
        quantity=quantity,
        exchange=exchange,
        currency=currency,
        order_type=order_type,
        limit_price=limit_price,
        tif=tif,
        outside_rth=outside_rth,
        timeout=timeout,
        account=account,
        stop_price=stop_price,
        trail_stop_price=trail_stop_price,
        trail_percent=trail_percent,
        take_profit_price=take_profit_price,
        stop_loss_price=stop_loss_price,
    )


def cancel_open_order(
    profile: ProfileConfig,
    order_id: int,
    timeout: float = 4.0,
    account: Optional[str] = None,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout, readonly=False) as ib:
        managed_accounts = list(ib.managedAccounts())
        if account and managed_accounts and account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{account}'. Available accounts: {available}")

        trades = list(ib.reqAllOpenOrders())
        target_trade = None
        for trade in trades:
            if trade.order.orderId != order_id:
                continue
            if account and trade.order.account != account:
                continue
            target_trade = trade
            break

        if target_trade is None:
            raise RuntimeError(f"Open order '{order_id}' was not found.")

        selected_account = target_trade.order.account
        with _capture_ib_errors(ib) as raw_errors:
            with _suppress_ib_async_logs():
                cancelled_trade = ib.cancelOrder(target_trade.order)
                if cancelled_trade is None:
                    raise RuntimeError(f"Unable to cancel order '{order_id}'.")
                ib.waitOnUpdate(timeout=min(timeout, 0.75))

        return _trade_payload(cancelled_trade, managed_accounts, selected_account, raw_errors, "cancel")


def _build_clean_modify_order(source_order: object) -> object:
    """Build a clean Order for modification, copying only essential fields.

    Orders returned by reqAllOpenOrders() contain server-populated fields
    that can cause Error 320 (NumberFormatException) when sent back.
    This builds a minimal Order with the same identity and core parameters,
    avoiding round-trip serialization issues.
    """
    try:
        from ib_async import Order
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    order = Order()
    # Identity — server uses these to match the existing order
    order.orderId = source_order.orderId
    order.clientId = source_order.clientId
    order.permId = source_order.permId
    # Core order parameters
    order.action = source_order.action
    order.totalQuantity = source_order.totalQuantity
    order.orderType = source_order.orderType
    order.lmtPrice = source_order.lmtPrice
    order.auxPrice = source_order.auxPrice
    order.tif = source_order.tif
    order.outsideRth = source_order.outsideRth
    order.account = source_order.account
    # Bracket / OCA linkage
    order.parentId = source_order.parentId
    order.ocaGroup = source_order.ocaGroup
    order.ocaType = source_order.ocaType
    order.transmit = source_order.transmit
    # Trailing orders
    order.trailStopPrice = source_order.trailStopPrice
    order.trailingPercent = source_order.trailingPercent
    # Time constraints
    order.goodAfterTime = source_order.goodAfterTime
    order.goodTillDate = source_order.goodTillDate
    return order


def modify_order(
    profile: ProfileConfig,
    order_id: int,
    limit_price: Optional[float] = None,
    aux_price: Optional[float] = None,
    quantity: Optional[float] = None,
    order_type: Optional[str] = None,
    tif: Optional[str] = None,
    outside_rth: Optional[bool] = None,
    timeout: float = 4.0,
    account: Optional[str] = None,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout, readonly=False) as ib:
        managed_accounts = list(ib.managedAccounts())
        if account and managed_accounts and account not in managed_accounts:
            available = ", ".join(managed_accounts)
            raise ValueError(f"Unknown account '{account}'. Available accounts: {available}")

        trades = list(ib.reqAllOpenOrders())
        target_trade = None
        for trade in trades:
            if trade.order.orderId != order_id:
                continue
            if account and trade.order.account != account:
                continue
            target_trade = trade
            break

        if target_trade is None:
            raise RuntimeError(f"Open order '{order_id}' was not found.")

        selected_account = target_trade.order.account
        contract = target_trade.contract
        order = _build_clean_modify_order(target_trade.order)

        if limit_price is not None:
            order.lmtPrice = limit_price
        if aux_price is not None:
            order.auxPrice = aux_price
        if quantity is not None:
            order.totalQuantity = quantity
        if order_type is not None:
            order.orderType = order_type
        if tif is not None:
            order.tif = tif
        if outside_rth is not None:
            order.outsideRth = outside_rth

        with _capture_ib_errors(ib) as raw_errors:
            with _suppress_ib_async_logs():
                modified_trade = ib.placeOrder(contract, order)
                ib.waitOnUpdate(timeout=min(timeout, 0.75))

        return _trade_payload(modified_trade, managed_accounts, selected_account, raw_errors, "modify")


def _quote_snapshot_payload(current_ticker: object, current_contract: object) -> Dict[str, object]:
    observed_at = getattr(current_ticker, "time", None) or getattr(current_ticker, "rtTime", None)
    return {
        "symbol": current_contract.symbol,
        "local_symbol": current_contract.localSymbol,
        "exchange": current_contract.exchange,
        "primary_exchange": current_contract.primaryExchange,
        "currency": current_contract.currency,
        "sec_type": current_contract.secType,
        "con_id": current_contract.conId,
        "market_data_type": current_ticker.marketDataType,
        "bid": _normalize_number(current_ticker.bid),
        "bid_size": _normalize_number(current_ticker.bidSize),
        "ask": _normalize_number(current_ticker.ask),
        "ask_size": _normalize_number(current_ticker.askSize),
        "last": _normalize_number(current_ticker.last),
        "last_size": _normalize_number(current_ticker.lastSize),
        "close": _normalize_number(current_ticker.close),
        "open": _normalize_number(current_ticker.open),
        "high": _normalize_number(current_ticker.high),
        "low": _normalize_number(current_ticker.low),
        "volume": _normalize_number(current_ticker.volume),
        "observed_at": observed_at.isoformat() if hasattr(observed_at, "isoformat") else None,
    }


def _quote_has_useful_prices(payload: Dict[str, object]) -> bool:
    return any(payload.get(field) is not None for field in ("bid", "ask", "last", "close", "open", "high", "low"))


def _build_quote_debug_attempt(
    requested_market_data_type: int,
    current_payload: Dict[str, object],
    errors: List[Dict[str, object]],
) -> Dict[str, object]:
    return {
        "requested_market_data_type": requested_market_data_type,
        "returned_market_data_type": current_payload.get("market_data_type"),
        "quote_source": current_payload.get("quote_source"),
        "has_useful_prices": _quote_has_useful_prices(current_payload),
        "error_codes": sorted({int(error["code"]) for error in errors}),
        "errors": errors,
    }


def _stream_quote_updates(
    ib: object,
    qualified_contract: object,
    market_data_type: int,
    updates: int,
    interval: float,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    source = "live" if market_data_type == 1 else "delayed"
    matcher = lambda current_contract: current_contract is not None and getattr(current_contract, "conId", None) == qualified_contract.conId
    with _capture_ib_errors(ib, matcher) as mode_errors:
        with _suppress_ib_async_logs():
            ib.reqMarketDataType(market_data_type)
            ticker = ib.reqMktData(qualified_contract, snapshot=False)
            rows = []
            for index in range(updates):
                ib.waitOnUpdate(timeout=interval)
                payload = _quote_snapshot_payload(ticker, qualified_contract)
                payload["quote_source"] = source
                payload["update_index"] = index + 1
                rows.append(payload)
            ib.cancelMktData(qualified_contract)
    return rows, mode_errors


def get_quote_snapshot(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 4.0,
    debug_market_data: bool = False,
) -> Dict[str, object]:
    try:
        from ib_async import Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    with ib_session(profile, timeout=timeout) as ib:
        contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified[0]
        matcher = lambda current_contract: current_contract is not None and getattr(current_contract, "conId", None) == qualified_contract.conId
        attempts: List[Dict[str, object]] = []
        raw_errors: List[Dict[str, object]] = []

        with _capture_ib_errors(ib, matcher) as live_errors:
            with _suppress_ib_async_logs():
                ib.reqMarketDataType(1)
                live_ticker = ib.reqTickers(contract)[0]
        payload = _quote_snapshot_payload(live_ticker, qualified_contract)
        payload["quote_source"] = "live"
        raw_errors.extend(live_errors)
        if debug_market_data:
            attempts.append(_build_quote_debug_attempt(1, payload, list(live_errors)))

        if not _quote_has_useful_prices(payload):
            with _capture_ib_errors(ib, matcher) as delayed_errors:
                with _suppress_ib_async_logs():
                    ib.reqMarketDataType(3)
                    delayed_ticker = ib.reqTickers(contract)[0]
            delayed_payload = _quote_snapshot_payload(delayed_ticker, qualified_contract)
            raw_errors.extend(delayed_errors)
            if _quote_has_useful_prices(delayed_payload):
                delayed_payload["quote_source"] = "delayed"
                payload = delayed_payload
            else:
                delayed_payload["quote_source"] = "delayed"
                payload = delayed_payload
            if debug_market_data:
                attempts.append(_build_quote_debug_attempt(3, payload, list(delayed_errors)))
        if debug_market_data:
            payload["requested_market_data_type"] = 1
            payload["returned_market_data_type"] = payload.get("market_data_type")
            payload["fallback_applied"] = len(attempts) > 1
            payload["raw_error_codes"] = sorted({int(error["code"]) for error in raw_errors})
            payload["raw_errors"] = raw_errors
            payload["attempts"] = attempts
        return payload


def watch_quote(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    updates: int = 5,
    interval: float = 2.0,
    timeout: float = 4.0,
) -> Dict[str, object]:
    try:
        from ib_async import Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    if updates <= 0:
        raise ValueError("Updates must be greater than zero.")
    if interval <= 0:
        raise ValueError("Interval must be greater than zero.")

    with ib_session(profile, timeout=timeout) as ib:
        contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified[0]
        live_rows, live_errors = _stream_quote_updates(
            ib,
            qualified_contract=qualified_contract,
            market_data_type=1,
            updates=updates,
            interval=interval,
        )
        raw_errors = list(live_errors)
        rows = live_rows
        fallback_applied = False

        if not any(_quote_has_useful_prices(row) for row in live_rows):
            delayed_rows, delayed_errors = _stream_quote_updates(
                ib,
                qualified_contract=qualified_contract,
                market_data_type=3,
                updates=updates,
                interval=interval,
            )
            raw_errors.extend(delayed_errors)
            rows = delayed_rows
            fallback_applied = True

        return {
            "watch": True,
            "symbol": qualified_contract.symbol,
            "local_symbol": qualified_contract.localSymbol,
            "exchange": qualified_contract.exchange,
            "primary_exchange": qualified_contract.primaryExchange,
            "currency": qualified_contract.currency,
            "sec_type": qualified_contract.secType,
            "con_id": qualified_contract.conId,
            "updates": updates,
            "interval": interval,
            "requested_market_data_type": 1,
            "fallback_applied": fallback_applied,
            "row_count": len(rows),
            "rows": rows,
            "raw_error_codes": sorted({int(error["code"]) for error in raw_errors}),
            "raw_errors": raw_errors,
        }


def get_historical_bars(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    end: str = "",
    duration: str = "1 D",
    bar_size: str = "5 mins",
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    timeout: float = 10.0,
) -> Dict[str, object]:
    try:
        from ib_async import Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    with ib_session(profile, timeout=timeout) as ib:
        contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified[0]
        matcher = lambda current_contract: current_contract is not None and getattr(current_contract, "conId", None) == qualified_contract.conId
        with _capture_ib_errors(ib, matcher) as raw_errors:
            with _suppress_ib_async_logs():
                bars = ib.reqHistoricalData(
                    qualified_contract,
                    endDateTime=end,
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=what_to_show.upper(),
                    useRTH=use_rth,
                    formatDate=2,
                    keepUpToDate=False,
                    timeout=timeout,
                )

        rows = []
        for bar in bars:
            bar_date = getattr(bar, "date", None)
            if hasattr(bar_date, "isoformat"):
                bar_date_value = bar_date.isoformat()
            else:
                bar_date_value = str(bar_date)
            rows.append(
                {
                    "date": bar_date_value,
                    "open": _normalize_number(bar.open),
                    "high": _normalize_number(bar.high),
                    "low": _normalize_number(bar.low),
                    "close": _normalize_number(bar.close),
                    "volume": _normalize_number(bar.volume),
                    "average": _normalize_number(bar.average),
                    "bar_count": getattr(bar, "barCount", None),
                }
            )

        return {
            "symbol": qualified_contract.symbol,
            "local_symbol": qualified_contract.localSymbol,
            "exchange": qualified_contract.exchange,
            "primary_exchange": qualified_contract.primaryExchange,
            "currency": qualified_contract.currency,
            "sec_type": qualified_contract.secType,
            "con_id": qualified_contract.conId,
            "end": end or "",
            "duration": duration,
            "bar_size": bar_size,
            "what_to_show": what_to_show.upper(),
            "use_rth": use_rth,
            "count": len(rows),
            "rows": rows,
            "raw_error_codes": sorted({int(error["code"]) for error in raw_errors}),
            "raw_errors": raw_errors,
        }


def get_news_providers(
    profile: ProfileConfig,
    timeout: float = 4.0,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        with _suppress_ib_async_logs():
            providers = ib.reqNewsProviders()
        rows = []
        for provider in providers:
            rows.append(
                {
                    "code": provider.code,
                    "name": provider.name,
                }
            )
        rows.sort(key=lambda row: str(row["code"]))
        return {
            "count": len(rows),
            "rows": rows,
        }


_HEADLINE_META_RE = re.compile(r"\{[^}]*\}")


def _parse_headline_metadata(raw: str) -> Dict[str, object]:
    match = _HEADLINE_META_RE.search(raw)
    if not match:
        return {"headline": raw.strip()}
    meta_str = match.group(0)[1:-1]  # strip { }
    headline = _HEADLINE_META_RE.sub("", raw).strip()
    result: Dict[str, object] = {"headline": headline}
    # Parse key:value pairs where keys are single uppercase letters (A, L, K, C)
    # and values may contain colons (e.g. L:Chinese (Simplified and Traditional),en)
    _KEY_RE = re.compile(r"(?:^|:)([ALKC]):")
    keys = list(_KEY_RE.finditer(meta_str))
    for i, m in enumerate(keys):
        key = m.group(1)
        val_start = m.end()
        val_end = keys[i + 1].start() if i + 1 < len(keys) else len(meta_str)
        val = meta_str[val_start:val_end]
        if key == "L":
            result["language"] = val or None
        elif key == "K" and val and val != "n/a":
            try:
                result["sentiment"] = round(float(val), 4)
            except ValueError:
                pass
        elif key == "C" and val and val != "n/a":
            try:
                result["confidence"] = round(float(val), 4)
            except ValueError:
                pass
    return result


def get_news_headlines(
    profile: ProfileConfig,
    symbol: str,
    provider_codes: str = "",
    start: str = "",
    end: str = "",
    limit: int = 10,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    try:
        from ib_async import Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    with ib_session(profile, timeout=timeout) as ib:
        contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified[0]

        start_dt = (
            datetime.strptime(start, "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if start
            else ""
        )
        end_dt = (
            datetime.strptime(end, "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if end
            else ""
        )

        if not provider_codes:
            with _suppress_ib_async_logs():
                providers = ib.reqNewsProviders()
            provider_codes = "+".join(p.code for p in providers) if providers else ""

        with _suppress_ib_async_logs():
            headlines = ib.reqHistoricalNews(
                qualified_contract.conId,
                provider_codes,
                start_dt,
                end_dt,
                limit,
            )

        rows = []
        for headline in (headlines or []):
            raw = headline.headline
            parsed = _parse_headline_metadata(raw)
            row: Dict[str, object] = {
                "time": headline.time.isoformat() if hasattr(headline.time, "isoformat") else str(headline.time),
                "provider_code": headline.providerCode,
                "article_id": headline.articleId,
                "headline": parsed["headline"],
            }
            if parsed.get("language") is not None:
                row["language"] = parsed["language"]
            if parsed.get("sentiment") is not None:
                row["sentiment"] = parsed["sentiment"]
            if parsed.get("confidence") is not None:
                row["confidence"] = parsed["confidence"]
            rows.append(row)

        return {
            "symbol": qualified_contract.symbol,
            "local_symbol": qualified_contract.localSymbol,
            "exchange": qualified_contract.exchange,
            "primary_exchange": qualified_contract.primaryExchange,
            "currency": qualified_contract.currency,
            "sec_type": qualified_contract.secType,
            "con_id": qualified_contract.conId,
            "provider_codes": provider_codes,
            "limit": limit,
            "count": len(rows),
            "rows": rows,
        }


def get_news_article(
    profile: ProfileConfig,
    provider_code: str,
    article_id: str,
    timeout: float = 10.0,
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout) as ib:
        with _suppress_ib_async_logs():
            article = ib.reqNewsArticle(provider_code, article_id)

        article_type = getattr(article, "articleType", None)
        article_text = getattr(article, "articleText", None)

        return {
            "provider_code": provider_code,
            "article_id": article_id,
            "article_type": str(article_type) if article_type is not None else None,
            "article_text": str(article_text) if article_text is not None else None,
        }


def get_option_chains(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    try:
        from ib_async import Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    with ib_session(profile, timeout=timeout) as ib:
        contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified[0]
        with _suppress_ib_async_logs():
            chains = ib.reqSecDefOptParams(
                qualified_contract.symbol,
                "",
                qualified_contract.secType,
                qualified_contract.conId,
            )

        rows = []
        for chain in chains:
            rows.append(
                {
                    "exchange": chain.exchange,
                    "underlying_con_id": chain.underlyingConId,
                    "trading_class": chain.tradingClass,
                    "multiplier": chain.multiplier,
                    "expirations": sorted(chain.expirations),
                    "expiration_count": len(chain.expirations),
                    "strikes": sorted(chain.strikes),
                    "strike_count": len(chain.strikes),
                }
            )
        rows.sort(key=lambda row: (str(row["exchange"]), str(row["trading_class"])))

        return {
            "symbol": qualified_contract.symbol,
            "local_symbol": qualified_contract.localSymbol,
            "exchange": qualified_contract.exchange,
            "primary_exchange": qualified_contract.primaryExchange,
            "currency": qualified_contract.currency,
            "sec_type": qualified_contract.secType,
            "con_id": qualified_contract.conId,
            "chain_count": len(rows),
            "rows": rows,
        }


def _greeks_payload(greeks: object) -> Optional[Dict[str, Optional[float]]]:
    if greeks is None:
        return None
    return {
        "implied_vol": _normalize_number(getattr(greeks, "impliedVol", None)),
        "delta": _normalize_number(getattr(greeks, "delta", None)),
        "gamma": _normalize_number(getattr(greeks, "gamma", None)),
        "theta": _normalize_number(getattr(greeks, "theta", None)),
        "vega": _normalize_number(getattr(greeks, "vega", None)),
        "opt_price": _normalize_number(getattr(greeks, "optPrice", None)),
        "und_price": _normalize_number(getattr(greeks, "undPrice", None)),
        "pv_dividend": _normalize_number(getattr(greeks, "pvDividend", None)),
    }


def get_option_quotes(
    profile: ProfileConfig,
    symbol: str,
    expiration: str,
    strikes: Optional[List[float]] = None,
    right: str = "",
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    try:
        from ib_async import Option, Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    normalized_right = right.upper()
    if normalized_right and normalized_right not in ("C", "P", "CALL", "PUT"):
        raise ValueError(f"Unsupported right '{right}'. Use C, P, CALL, or PUT.")
    rights = [normalized_right] if normalized_right else ["C", "P"]

    with ib_session(profile, timeout=timeout) as ib:
        underlying = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified_underlying = ib.qualifyContracts(underlying)
        if not qualified_underlying:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified_underlying[0]

        if strikes is None:
            with _suppress_ib_async_logs():
                chains = ib.reqSecDefOptParams(
                    qualified_contract.symbol,
                    "",
                    qualified_contract.secType,
                    qualified_contract.conId,
                )
            smart_chain = None
            for chain in chains:
                if chain.exchange == exchange:
                    smart_chain = chain
                    break
            if smart_chain is None and chains:
                smart_chain = chains[0]
            if smart_chain is None:
                raise RuntimeError(f"No option chains found for '{symbol}'.")

            if expiration not in smart_chain.expirations:
                raise ValueError(
                    f"Expiration '{expiration}' not available. "
                    f"Available: {sorted(smart_chain.expirations)[:10]}..."
                )

            with _suppress_ib_async_logs():
                ib.reqMarketDataType(1)
                underlying_ticker = ib.reqTickers(qualified_contract)[0]
            und_price = None
            for field in ("last", "close", "bid", "ask"):
                val = _normalize_number(getattr(underlying_ticker, field, None))
                if val is not None and val > 0:
                    und_price = val
                    break

            if und_price is not None:
                strike_list = sorted(
                    s for s in smart_chain.strikes
                    if und_price * 0.9 <= s <= und_price * 1.1
                )
            else:
                all_strikes = sorted(smart_chain.strikes)
                mid = len(all_strikes) // 2
                strike_list = all_strikes[max(0, mid - 5):mid + 5]
        else:
            strike_list = sorted(strikes)

        contracts = [
            Option(symbol.upper(), expiration, strike, r, exchange, currency=currency)
            for r in rights
            for strike in strike_list
        ]

        with _suppress_ib_async_logs():
            qualified_options = ib.qualifyContracts(*contracts)

        if not qualified_options:
            raise RuntimeError(
                f"Unable to qualify any option contracts for '{symbol}' "
                f"expiration={expiration} strikes={strike_list}."
            )

        with _suppress_ib_async_logs():
            ib.reqMarketDataType(1)
            tickers = ib.reqTickers(*qualified_options)

        rows = []
        for ticker in tickers:
            opt = ticker.contract
            model = _greeks_payload(ticker.modelGreeks)
            rows.append(
                {
                    "symbol": opt.symbol,
                    "local_symbol": opt.localSymbol,
                    "con_id": opt.conId,
                    "expiration": opt.lastTradeDateOrContractMonth,
                    "strike": opt.strike,
                    "right": opt.right,
                    "exchange": opt.exchange,
                    "trading_class": opt.tradingClass,
                    "multiplier": opt.multiplier,
                    "bid": _normalize_number(ticker.bid),
                    "ask": _normalize_number(ticker.ask),
                    "last": _normalize_number(ticker.last),
                    "volume": _normalize_number(ticker.volume),
                    "open_interest": _normalize_number(ticker.openInterest),
                    "implied_vol": model["implied_vol"] if model else None,
                    "delta": model["delta"] if model else None,
                    "gamma": model["gamma"] if model else None,
                    "theta": model["theta"] if model else None,
                    "vega": model["vega"] if model else None,
                    "und_price": model["und_price"] if model else None,
                    "model_greeks": model,
                }
            )

        rows.sort(key=lambda row: (str(row["right"]), float(row["strike"])))

        return {
            "symbol": qualified_contract.symbol,
            "local_symbol": qualified_contract.localSymbol,
            "exchange": qualified_contract.exchange,
            "primary_exchange": qualified_contract.primaryExchange,
            "currency": qualified_contract.currency,
            "sec_type": qualified_contract.secType,
            "con_id": qualified_contract.conId,
            "expiration": expiration,
            "right_filter": normalized_right or "ALL",
            "strike_count": len(strike_list),
            "count": len(rows),
            "rows": rows,
        }


def get_scanner_parameters(
    profile: ProfileConfig,
    timeout: float = 10.0,
) -> Dict[str, object]:
    import xml.etree.ElementTree as ET

    with ib_session(profile, timeout=timeout) as ib:
        with _suppress_ib_async_logs():
            xml_str = ib.reqScannerParameters()

    tree = ET.fromstring(xml_str)
    scan_codes = []
    for elem in tree.findall(".//ScanCode"):
        code = elem.findtext("scanCode", "")
        display = elem.findtext("displayName", "")
        if code:
            scan_codes.append({"code": code, "display_name": display})
    scan_codes.sort(key=lambda r: r["code"])

    instruments = []
    for elem in tree.findall(".//InstrumentList/Instrument"):
        itype = elem.findtext("type", "")
        iname = elem.findtext("name", "")
        if itype:
            instruments.append({"type": itype, "name": iname})
    instruments.sort(key=lambda r: r["type"])

    locations = []
    for elem in tree.findall(".//LocationTree//Location"):
        loc_code = elem.findtext("locationCode", "")
        display = elem.findtext("displayName", "")
        if loc_code:
            locations.append({"code": loc_code, "display_name": display})
    locations.sort(key=lambda r: r["code"])

    return {
        "scan_code_count": len(scan_codes),
        "scan_codes": scan_codes,
        "instrument_count": len(instruments),
        "instruments": instruments,
        "location_count": len(locations),
        "locations": locations,
    }


def run_scanner(
    profile: ProfileConfig,
    scan_code: str,
    instrument: str = "STK",
    location_code: str = "STK.US.MAJOR",
    num_rows: int = 20,
    above_price: Optional[float] = None,
    below_price: Optional[float] = None,
    above_volume: Optional[int] = None,
    market_cap_above: Optional[float] = None,
    market_cap_below: Optional[float] = None,
    timeout: float = 10.0,
) -> Dict[str, object]:
    try:
        from ib_async import ScannerSubscription
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    sub = ScannerSubscription(
        instrument=instrument,
        locationCode=location_code,
        scanCode=scan_code.upper(),
        numberOfRows=num_rows,
    )
    if above_price is not None:
        sub.abovePrice = above_price
    if below_price is not None:
        sub.belowPrice = below_price
    if above_volume is not None:
        sub.aboveVolume = above_volume
    if market_cap_above is not None:
        sub.marketCapAbove = market_cap_above
    if market_cap_below is not None:
        sub.marketCapBelow = market_cap_below

    with ib_session(profile, timeout=timeout) as ib:
        with _suppress_ib_async_logs():
            scan_data = ib.reqScannerData(sub, [], [])

        rows = []
        for item in scan_data:
            contract = item.contractDetails.contract
            details = item.contractDetails
            rows.append(
                {
                    "rank": item.rank,
                    "symbol": contract.symbol,
                    "local_symbol": contract.localSymbol,
                    "sec_type": contract.secType,
                    "exchange": contract.exchange,
                    "primary_exchange": contract.primaryExchange,
                    "currency": contract.currency,
                    "con_id": contract.conId,
                    "industry": getattr(details, "industry", None) or None,
                    "category": getattr(details, "category", None) or None,
                    "distance": item.distance or None,
                    "benchmark": item.benchmark or None,
                    "projection": item.projection or None,
                }
            )

        return {
            "scan_code": scan_code.upper(),
            "instrument": instrument,
            "location_code": location_code,
            "num_rows": num_rows,
            "count": len(rows),
            "rows": rows,
        }


# ---------------------------------------------------------------------------
# Fundamental Data
# ---------------------------------------------------------------------------

_FUNDAMENTAL_SUBSCRIPTION_HINT = (
    "This command requires a Reuters Fundamentals subscription. "
    "Visit IBKR Account Management > Settings > Market Data Subscriptions "
    "and search for 'Reuters Fundamentals' or 'LSEG' to enable it (~$7/month)."
)


def _get_fundamental_xml(
    profile: ProfileConfig,
    symbol: str,
    report_type: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Tuple[object, str]:
    """Fetch fundamental data XML. Returns (qualified_contract, xml_string)."""
    try:
        from ib_async import Stock
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ib_async is not installed. Reinstall the project with Python 3.10+ to enable IBKR API commands."
        ) from exc

    with ib_session(profile, timeout=timeout) as ib:
        contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

        qualified_contract = qualified[0]
        with _suppress_ib_async_logs():
            xml_str = ib.reqFundamentalData(qualified_contract, report_type)

        if not xml_str:
            raise RuntimeError(
                f"No fundamental data returned for '{symbol}'. {_FUNDAMENTAL_SUBSCRIPTION_HINT}"
            )

        return qualified_contract, xml_str


def _contract_metadata(contract: object) -> Dict[str, object]:
    return {
        "symbol": contract.symbol,
        "local_symbol": contract.localSymbol,
        "exchange": contract.exchange,
        "primary_exchange": contract.primaryExchange,
        "currency": contract.currency,
        "sec_type": contract.secType,
        "con_id": contract.conId,
    }


# -- ReportSnapshot parser --------------------------------------------------

def _parse_report_snapshot(xml_str: str) -> Dict[str, object]:
    root = ET.fromstring(xml_str)
    result: Dict[str, object] = {}

    # Company info
    general = root.find(".//CoGeneralInfo")
    if general is not None:
        result["employees"] = general.findtext("Employees")
        result["shares_outstanding"] = general.findtext("SharesOut")
        result["reporting_currency"] = general.findtext("ReportingCurrency")

    # Text info (business summary)
    for text_elem in root.findall(".//TextInfo/Text"):
        text_type = text_elem.get("Type", "")
        if text_type == "Business Summary":
            result["business_summary"] = (text_elem.text or "").strip()
        elif text_type == "Financial Summary":
            result["financial_summary"] = (text_elem.text or "").strip()

    # Contact info
    contact = root.find(".//ContactInfo")
    if contact is not None:
        parts = []
        for tag in ("streetAddress", "city", "state-region", "postalCode", "country"):
            val = contact.findtext(tag)
            if val:
                parts.append(val.strip())
        if parts:
            result["address"] = ", ".join(parts)

    # Web links
    web = root.find(".//WebLinks")
    if web is not None:
        result["website"] = web.findtext("webSite")

    # Industry info
    for industry in root.findall(".//PeerInfo/IndustryInfo/Industry"):
        ind_type = industry.get("type", "")
        if ind_type == "TRBC":
            result["industry"] = industry.findtext("IndustryName")

    # Officers
    officers = []
    for officer in root.findall(".//Officers/Officer"):
        name_parts = []
        first = officer.findtext("firstName")
        last = officer.findtext("lastName")
        if first:
            name_parts.append(first.strip())
        if last:
            name_parts.append(last.strip())
        titles = [t.text for t in officer.findall(".//title") if t.text]
        if name_parts:
            officers.append({
                "name": " ".join(name_parts),
                "title": ", ".join(titles) if titles else None,
            })
    if officers:
        result["officers"] = officers

    # Ratios
    ratios: Dict[str, object] = {}
    ratio_labels = {
        "MKTCAP": "market_cap",
        "PEEXCLXOR": "pe_ratio",
        "PRICE2BK": "price_to_book",
        "DIVYIELD": "dividend_yield",
        "TTMREV": "ttm_revenue",
        "TTMEBITD": "ttm_ebitda",
        "TTMNIAC": "ttm_net_income",
        "TTMEPSXCLX": "ttm_eps",
        "TTMGROSMGN": "ttm_gross_margin",
        "TTMOPMGN": "ttm_operating_margin",
        "TTMNPMGN": "ttm_net_margin",
        "TTMROEPCT": "ttm_roe",
        "TTMROAPCT": "ttm_roa",
        "PRICE2TANBK": "price_to_tangible_book",
        "NHIG": "52w_high",
        "NLOW": "52w_low",
        "NPRICE": "price",
        "BETA": "beta",
        "QTOTD2EQ": "debt_to_equity",
        "QCURRATIO": "current_ratio",
        "QQUICKRATI": "quick_ratio",
    }
    for ratio_elem in root.findall(".//Ratios//Ratio"):
        field = ratio_elem.get("FieldName", "")
        if field in ratio_labels and ratio_elem.text:
            try:
                ratios[ratio_labels[field]] = round(float(ratio_elem.text), 4)
            except ValueError:
                ratios[ratio_labels[field]] = ratio_elem.text
    if ratios:
        result["ratios"] = ratios

    # Forecast data
    forecast = root.find(".//ForecastData")
    if forecast is not None:
        consensus = forecast.find(".//ConsRecommendation")
        if consensus is not None:
            rec = {}
            for child in consensus:
                if child.text:
                    try:
                        rec[child.tag.lower()] = round(float(child.text), 2)
                    except ValueError:
                        rec[child.tag.lower()] = child.text
            if rec:
                result["consensus_recommendation"] = rec

    return result


# -- ReportsFinSummary parser ------------------------------------------------

def _parse_fin_summary(xml_str: str) -> Dict[str, object]:
    root = ET.fromstring(xml_str)
    rows: List[Dict[str, object]] = []

    for elem in root.iter():
        if elem.text and elem.text.strip() and elem.tag not in (
            "CoID", "Source", "UpdateType", "PeriodLength",
        ):
            report_type = elem.get("reportType", "")
            period = elem.get("period", "")
            date = elem.get("date", elem.get("asofDate", ""))
            val = elem.text.strip()
            if not val:
                continue
            try:
                numeric = round(float(val), 4)
            except ValueError:
                numeric = val

            tag = elem.tag
            if tag and (report_type or period or date):
                rows.append({
                    "metric": tag,
                    "value": numeric,
                    "report_type": report_type or None,
                    "period": period or None,
                    "date": date or None,
                })

    return {"count": len(rows), "rows": rows}


# -- ReportsFinStatements parser ---------------------------------------------

def _parse_fin_statements(xml_str: str) -> Dict[str, object]:
    root = ET.fromstring(xml_str)

    # Build COA map: coaItem -> label
    coa_map: Dict[str, str] = {}
    for item in root.findall(".//COAMap/mapItem"):
        code = item.get("coaItem", "")
        label = item.text or code
        if code:
            coa_map[code] = label.strip()

    statement_type_labels = {"INC": "income_statement", "BAL": "balance_sheet", "CAS": "cash_flow"}
    result: Dict[str, object] = {}

    for period_group_tag, period_label in [("AnnualPeriods", "annual"), ("InterimPeriods", "interim")]:
        period_group = root.find(f".//{period_group_tag}")
        if period_group is None:
            continue

        fiscal_periods = period_group.findall("FiscalPeriod")
        # Limit to most recent 4 periods
        for fp in fiscal_periods[:4]:
            end_date = fp.get("EndDate", "")
            fiscal_year = fp.get("FiscalYear", "")
            period_num = fp.get("FiscalPeriodNumber", "")
            period_key = f"{fiscal_year}" if period_label == "annual" else f"{fiscal_year}Q{period_num}"

            for stmt in fp.findall("Statement"):
                stmt_type = stmt.get("Type", "")
                section_name = statement_type_labels.get(stmt_type, stmt_type)
                full_key = f"{section_name}_{period_label}"

                if full_key not in result:
                    result[full_key] = {"periods": [], "data": {}}

                section = result[full_key]
                if period_key not in section["periods"]:
                    section["periods"].append(period_key)

                for line in stmt.findall("lineItem"):
                    code = line.get("coaCode", "")
                    label = coa_map.get(code, code)
                    val = line.text
                    if val:
                        try:
                            val = round(float(val), 2)
                        except ValueError:
                            pass
                    if label not in section["data"]:
                        section["data"][label] = {}
                    section["data"][label][period_key] = val

    return result


# -- ReportsOwnership parser -------------------------------------------------

def _parse_ownership(xml_str: str) -> Dict[str, object]:
    root = ET.fromstring(xml_str)
    rows: List[Dict[str, object]] = []

    for owner in root.iter("Owner"):
        name = owner.findtext("name") or owner.findtext("Name") or ""
        row: Dict[str, object] = {"name": name.strip()}
        for tag in ("shares", "Shares", "sharesHeld"):
            val = owner.findtext(tag)
            if val:
                try:
                    row["shares"] = int(float(val))
                except ValueError:
                    row["shares"] = val
                break
        for tag in ("percent", "Percent", "pctHeld"):
            val = owner.findtext(tag)
            if val:
                try:
                    row["percent"] = round(float(val), 4)
                except ValueError:
                    row["percent"] = val
                break
        date_val = owner.findtext("date") or owner.findtext("Date") or owner.findtext("reportDate")
        if date_val:
            row["date"] = date_val
        if row.get("name"):
            rows.append(row)

    return {"count": len(rows), "rows": rows}


# -- Public API functions ----------------------------------------------------

def get_fundamental_snapshot(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    qualified_contract, xml_str = _get_fundamental_xml(
        profile, symbol, "ReportSnapshot", exchange, currency, timeout,
    )
    parsed = _parse_report_snapshot(xml_str)
    return {**_contract_metadata(qualified_contract), **parsed}


def get_fundamental_summary(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    qualified_contract, xml_str = _get_fundamental_xml(
        profile, symbol, "ReportsFinSummary", exchange, currency, timeout,
    )
    parsed = _parse_fin_summary(xml_str)
    return {**_contract_metadata(qualified_contract), **parsed}


def get_fundamental_financials(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    qualified_contract, xml_str = _get_fundamental_xml(
        profile, symbol, "ReportsFinStatements", exchange, currency, timeout,
    )
    parsed = _parse_fin_statements(xml_str)
    return {**_contract_metadata(qualified_contract), **parsed}


def get_fundamental_ownership(
    profile: ProfileConfig,
    symbol: str,
    exchange: str = "SMART",
    currency: str = "USD",
    timeout: float = 10.0,
) -> Dict[str, object]:
    qualified_contract, xml_str = _get_fundamental_xml(
        profile, symbol, "ReportsOwnership", exchange, currency, timeout,
    )
    parsed = _parse_ownership(xml_str)
    return {**_contract_metadata(qualified_contract), **parsed}
