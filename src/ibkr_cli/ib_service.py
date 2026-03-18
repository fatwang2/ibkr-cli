from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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


def _prepare_stock_order(
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
) -> tuple[List[str], str, object, object]:
    try:
        from ib_async import LimitOrder, MarketOrder, Stock
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
    if normalized_order_type not in ("MKT", "LMT"):
        raise ValueError(f"Unsupported order type '{order_type}'. Use MKT or LMT.")
    if normalized_order_type == "LMT":
        if limit_price is None or limit_price <= 0:
            raise ValueError("A positive --limit price is required for LMT orders.")
    elif limit_price is not None:
        raise ValueError("--limit can only be used with --type LMT.")

    managed_accounts, selected_account = _resolve_account(ib, account)
    contract = Stock(symbol=symbol.upper(), exchange=exchange, currency=currency)
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError(f"Unable to qualify contract for symbol '{symbol}'.")

    qualified_contract = qualified[0]
    if normalized_order_type == "LMT":
        order = LimitOrder(
            normalized_action,
            quantity,
            limit_price,
            tif=normalized_tif,
            outsideRth=outside_rth,
            account=selected_account,
        )
    else:
        order = MarketOrder(
            normalized_action,
            quantity,
            tif=normalized_tif,
            outsideRth=outside_rth,
            account=selected_account,
        )

    return managed_accounts, selected_account, qualified_contract, order


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
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout, readonly=False) as ib:
        managed_accounts, selected_account, qualified_contract, order = _prepare_stock_order(
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
        )

        matcher = lambda current_contract: current_contract is not None and getattr(current_contract, "conId", None) == qualified_contract.conId
        with _capture_ib_errors(ib, matcher) as raw_errors:
            with _suppress_ib_async_logs():
                state = ib.whatIfOrder(qualified_contract, order)

        return {
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
) -> Dict[str, object]:
    with ib_session(profile, timeout=timeout, readonly=False) as ib:
        managed_accounts, selected_account, qualified_contract, order = _prepare_stock_order(
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
        )

        with _capture_ib_errors(ib) as raw_errors:
            with _suppress_ib_async_logs():
                trade = ib.placeOrder(qualified_contract, order)
                ib.waitOnUpdate(timeout=min(timeout, 0.75))

        return _trade_payload(trade, managed_accounts, selected_account, raw_errors, "submit")


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

        with _suppress_ib_async_logs():
            headlines = ib.reqHistoricalNews(
                qualified_contract.conId,
                provider_codes,
                start_dt,
                end_dt,
                limit,
            )

        rows = []
        for headline in headlines:
            rows.append(
                {
                    "time": headline.time.isoformat() if hasattr(headline.time, "isoformat") else str(headline.time),
                    "provider_code": headline.providerCode,
                    "article_id": headline.articleId,
                    "headline": headline.headline,
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
