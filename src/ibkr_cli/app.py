from __future__ import annotations

import platform
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from collections import defaultdict

import typer
from rich.console import Console
from rich.table import Table

from ibkr_cli.config import (
    CONFIG_FILE,
    AppConfig,
    FlexConfig,
    ProfileConfig,
    default_config,
    get_flex_config,
    get_profile,
    load_config,
    profile_to_dict,
    save_config,
    set_config_value,
)
from ibkr_cli.flex_service import (
    get_flex_cash_transactions,
    get_flex_pnl,
    get_flex_trades,
    get_flex_transfers,
)
from ibkr_cli.ib_service import (
    ApiConnectionResult,
    cancel_open_order,
    check_api_connection,
    modify_order,
    get_account_summary,
    get_completed_orders,
    get_executions,
    get_fundamental_financials,
    get_fundamental_ownership,
    get_fundamental_snapshot,
    get_fundamental_summary,
    get_historical_bars,
    get_news_article,
    get_news_headlines,
    get_news_providers,
    get_open_orders,
    get_option_chains,
    get_option_quotes,
    get_scanner_parameters,
    get_positions,
    get_quote_snapshot,
    preview_stock_order,
    run_scanner,
    submit_stock_order,
    watch_quote,
)
from ibkr_cli.networking import ConnectionResult, test_tcp_connection
from ibkr_cli.version_check import _parse_version, check_for_update, run_update

console = Console()
app = typer.Typer(no_args_is_help=True, help="A local-first CLI for Interactive Brokers.")
profile_app = typer.Typer(no_args_is_help=True, help="Manage local connection profiles.")
connect_app = typer.Typer(no_args_is_help=True, help="Connectivity checks for TWS or IB Gateway.")
account_app = typer.Typer(no_args_is_help=True, help="Account-related read operations.")
orders_app = typer.Typer(no_args_is_help=True, help="Order-related read operations.")
news_app = typer.Typer(no_args_is_help=True, help="News headlines and articles.")
options_app = typer.Typer(no_args_is_help=True, help="Options chain and quotes.")
scanner_app = typer.Typer(no_args_is_help=True, help="Market scanner and screener.")
fundamentals_app = typer.Typer(no_args_is_help=True, help="Company fundamentals and financial data (requires Reuters Fundamentals subscription).")
config_app = typer.Typer(no_args_is_help=True, help="View and update CLI configuration.")
app.add_typer(profile_app, name="profile")
app.add_typer(connect_app, name="connect")
app.add_typer(account_app, name="account")
app.add_typer(orders_app, name="orders")
app.add_typer(news_app, name="news")
app.add_typer(options_app, name="options")
app.add_typer(scanner_app, name="scanner")
app.add_typer(fundamentals_app, name="fundamentals")
app.add_typer(config_app, name="config")

EXIT_CODE_GENERAL = 1
EXIT_CODE_USAGE = 2
EXIT_CODE_CONFIG = 3
EXIT_CODE_CONNECTIVITY = 4
EXIT_CODE_API = 5

ERROR_COMMAND_FAILED = "command_failed"
ERROR_INVALID_ARGUMENTS = "invalid_arguments"
ERROR_CONFIG_LOAD_FAILED = "config_load_failed"
ERROR_CONFIG_ALREADY_EXISTS = "config_already_exists"
ERROR_UNKNOWN_PROFILE = "unknown_profile"
ERROR_CONNECTIVITY_CHECK_FAILED = "connectivity_check_failed"
ERROR_ACCOUNT_QUERY_FAILED = "account_query_failed"
ERROR_ORDER_QUERY_FAILED = "order_query_failed"
ERROR_ORDER_OPERATION_FAILED = "order_operation_failed"
ERROR_MARKET_DATA_REQUEST_FAILED = "market_data_request_failed"
ERROR_NEWS_REQUEST_FAILED = "news_request_failed"
ERROR_OPTIONS_REQUEST_FAILED = "options_request_failed"
ERROR_SCANNER_REQUEST_FAILED = "scanner_request_failed"
ERROR_FUNDAMENTALS_REQUEST_FAILED = "fundamentals_request_failed"
ERROR_FLEX_REQUEST_FAILED = "flex_request_failed"


def package_version() -> str:
    try:
        return version("ibkr-cli")
    except PackageNotFoundError:
        return "0.1.0"


def version_callback(value: bool) -> None:
    if value:
        console.print(package_version())
        raise typer.Exit()


@app.callback()
def main(
    version_flag: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    try:
        latest = check_for_update(package_version())
        if latest:
            console.print(
                f"[yellow]A new version {latest} is available (current: {package_version()}). "
                f'Run "ibkr update" to upgrade.[/yellow]'
            )
    except Exception:
        pass


def build_error_payload(
    message: str,
    error_code: str,
    exit_code: int,
    details: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "ok": False,
        "error": {
            "code": error_code,
            "message": message,
            "exit_code": exit_code,
        },
    }
    if details:
        payload["error"]["details"] = details
    return payload


def exit_with_error(
    message: str,
    code: str = ERROR_COMMAND_FAILED,
    exit_code: int = EXIT_CODE_GENERAL,
    json_output: bool = False,
    details: Optional[Dict[str, object]] = None,
) -> None:
    if json_output:
        print_json(build_error_payload(message, code, exit_code, details))
    else:
        console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=exit_code)


def load_or_exit(json_output: bool = False) -> Tuple[AppConfig, bool]:
    try:
        return load_config()
    except Exception as exc:
        exit_with_error(
            f"Failed to load config: {exc}",
            code=ERROR_CONFIG_LOAD_FAILED,
            exit_code=EXIT_CODE_CONFIG,
            json_output=json_output,
            details={"config_file": str(CONFIG_FILE)},
        )


def resolve_profile_or_exit(profile: Optional[str], json_output: bool = False) -> Tuple[AppConfig, bool, str, ProfileConfig]:
    config, exists = load_or_exit(json_output=json_output)
    try:
        selected_name, selected_profile = get_profile(config, profile)
    except KeyError:
        available = ", ".join(sorted(config.profiles))
        exit_with_error(
            f"Unknown profile '{profile}'. Available profiles: {available}",
            code=ERROR_UNKNOWN_PROFILE,
            exit_code=EXIT_CODE_CONFIG,
            json_output=json_output,
            details={
                "requested_profile": profile,
                "available_profiles": sorted(config.profiles),
            },
        )
    return config, exists, selected_name, selected_profile


def render_profiles_table(config: AppConfig) -> Table:
    table = Table(title="Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Mode")
    table.add_column("Host")
    table.add_column("Port", justify="right")
    table.add_column("Client ID", justify="right")
    table.add_column("Default")
    for name in sorted(config.profiles):
        profile = config.profiles[name]
        table.add_row(
            name,
            profile.mode,
            profile.host,
            str(profile.port),
            str(profile.client_id),
            "yes" if name == config.default_profile else "",
        )
    return table


def render_profile_detail(name: str, profile: ProfileConfig, is_default: bool) -> Table:
    table = Table(title=f"Profile: {name}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("name", name)
    table.add_row("mode", profile.mode)
    table.add_row("host", profile.host)
    table.add_row("port", str(profile.port))
    table.add_row("client_id", str(profile.client_id))
    table.add_row("default", "yes" if is_default else "no")
    return table


def render_connection_result(result: ConnectionResult) -> Table:
    table = Table(title="TCP Connectivity")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("host", result.host)
    table.add_row("port", str(result.port))
    table.add_row("timeout", str(result.timeout))
    table.add_row("reachable", "yes" if result.ok else "no")
    table.add_row("latency_ms", "-" if result.latency_ms is None else str(result.latency_ms))
    table.add_row("error", result.error or "")
    return table


def render_api_connection_result(result: ApiConnectionResult) -> Table:
    table = Table(title="IBKR API Connectivity")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("host", result.host)
    table.add_row("port", str(result.port))
    table.add_row("client_id", str(result.client_id))
    table.add_row("timeout", str(result.timeout))
    table.add_row("reachable", "yes" if result.ok else "no")
    table.add_row("latency_ms", "-" if result.latency_ms is None else str(result.latency_ms))
    table.add_row("server_version", "-" if result.server_version is None else str(result.server_version))
    table.add_row("managed_accounts", ", ".join(result.managed_accounts))
    table.add_row("error", result.error or "")
    return table


def render_account_summary_table(rows: Sequence[Dict[str, object]], account: str) -> Table:
    table = Table(title=f"Account Summary: {account}")
    table.add_column("Tag", style="cyan")
    table.add_column("Value", justify="right")
    table.add_column("Currency")
    for row in rows:
        table.add_row(str(row["tag"]), str(row["value"]), str(row["currency"]))
    return table


def render_positions_table(rows: Sequence[Dict[str, object]], account: Optional[str]) -> Table:
    table = Table(title=f"Positions: {account}" if account else "Positions")
    table.add_column("Account", style="cyan")
    table.add_column("Symbol")
    table.add_column("Local Symbol")
    table.add_column("Type")
    table.add_column("Exchange")
    table.add_column("Currency")
    table.add_column("Position", justify="right")
    table.add_column("Avg Cost", justify="right")
    for row in rows:
        table.add_row(
            str(row["account"]),
            str(row["symbol"]),
            str(row["local_symbol"]),
            str(row["sec_type"]),
            str(row["exchange"]),
            str(row["currency"]),
            str(row["position"]),
            str(row["avg_cost"]),
        )
    return table


def render_open_orders_table(rows: Sequence[Dict[str, object]], account: Optional[str]) -> Table:
    table = Table(title=f"Open Orders: {account}" if account else "Open Orders")
    table.add_column("Account", style="cyan")
    table.add_column("Order ID", justify="right")
    table.add_column("Symbol")
    table.add_column("Type")
    table.add_column("Action")
    table.add_column("Qty", justify="right")
    table.add_column("Limit", justify="right")
    table.add_column("Status")
    table.add_column("Filled", justify="right")
    table.add_column("Remaining", justify="right")
    for row in rows:
        table.add_row(
            str(row["account"]),
            str(row["order_id"]),
            str(row["symbol"]),
            str(row["order_type"]),
            str(row["action"]),
            "" if row["quantity"] is None else str(row["quantity"]),
            "" if row["limit_price"] is None else str(row["limit_price"]),
            str(row["status"]),
            "" if row["filled"] is None else str(row["filled"]),
            "" if row["remaining"] is None else str(row["remaining"]),
        )
    return table


def render_completed_orders_table(rows: Sequence[Dict[str, object]], account: Optional[str]) -> Table:
    table = Table(title=f"Completed Orders: {account}" if account else "Completed Orders")
    table.add_column("Account", style="cyan")
    table.add_column("Order ID", justify="right")
    table.add_column("Symbol")
    table.add_column("Type")
    table.add_column("Action")
    table.add_column("Qty", justify="right")
    table.add_column("Status")
    table.add_column("Avg Fill", justify="right")
    for row in rows:
        table.add_row(
            str(row["account"]),
            str(row["order_id"]),
            str(row["symbol"]),
            str(row["order_type"]),
            str(row["action"]),
            "" if row["quantity"] is None else str(row["quantity"]),
            str(row["status"]),
            "" if row["avg_fill_price"] is None else str(row["avg_fill_price"]),
        )
    return table


def render_executions_table(rows: Sequence[Dict[str, object]], account: Optional[str]) -> Table:
    table = Table(title=f"Executions: {account}" if account else "Executions")
    table.add_column("Account", style="cyan")
    table.add_column("Time")
    table.add_column("Exec ID")
    table.add_column("Symbol")
    table.add_column("Side")
    table.add_column("Shares", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Commission", justify="right")
    table.add_column("Realized PnL", justify="right")
    for row in rows:
        table.add_row(
            str(row["account"]),
            str(row["time"]),
            str(row["exec_id"]),
            str(row["symbol"]),
            str(row["side"]),
            "" if row["shares"] is None else str(row["shares"]),
            "" if row["price"] is None else str(row["price"]),
            "" if row["commission"] is None else str(row["commission"]),
            "" if row["realized_pnl"] is None else str(row["realized_pnl"]),
        )
    return table


def render_order_preview_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Order Preview: {payload['action']} {payload['symbol']}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    ordered_fields = (
        "selected_account",
        "symbol",
        "local_symbol",
        "exchange",
        "primary_exchange",
        "currency",
        "sec_type",
        "con_id",
        "action",
        "quantity",
        "order_type",
        "limit_price",
        "stop_price",
        "aux_price",
        "trailing_percent",
        "tif",
        "outside_rth",
        "status",
        "init_margin_before",
        "init_margin_change",
        "init_margin_after",
        "maint_margin_before",
        "maint_margin_change",
        "maint_margin_after",
        "equity_with_loan_before",
        "equity_with_loan_change",
        "equity_with_loan_after",
        "commission",
        "min_commission",
        "max_commission",
        "commission_currency",
        "warning_text",
        "raw_error_codes",
    )
    for field in ordered_fields:
        table.add_row(field, "" if payload.get(field) is None else str(payload.get(field)))
    return table


def render_trade_result_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Order {payload['operation'].title()}: {payload['action']} {payload['symbol']}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    ordered_fields = (
        "selected_account",
        "symbol",
        "local_symbol",
        "exchange",
        "primary_exchange",
        "currency",
        "sec_type",
        "con_id",
        "operation",
        "action",
        "quantity",
        "order_type",
        "limit_price",
        "aux_price",
        "trailing_percent",
        "trail_stop_price",
        "parent_id",
        "tif",
        "outside_rth",
        "order_id",
        "perm_id",
        "client_id",
        "status",
        "filled",
        "remaining",
        "avg_fill_price",
        "is_active",
        "is_done",
        "advanced_error",
        "raw_error_codes",
    )
    for field in ordered_fields:
        table.add_row(field, "" if payload.get(field) is None else str(payload.get(field)))
    return table


def render_quote_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Quote: {payload['symbol']}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    ordered_fields = (
        "symbol",
        "local_symbol",
        "exchange",
        "primary_exchange",
        "currency",
        "sec_type",
        "con_id",
        "market_data_type",
        "bid",
        "bid_size",
        "ask",
        "ask_size",
        "last",
        "last_size",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_source",
    )
    for field in ordered_fields:
        table.add_row(field, "" if payload.get(field) is None else str(payload.get(field)))
    if "requested_market_data_type" in payload:
        table.add_row("requested_market_data_type", str(payload.get("requested_market_data_type")))
        table.add_row("returned_market_data_type", str(payload.get("returned_market_data_type")))
        table.add_row("fallback_applied", str(payload.get("fallback_applied")))
        table.add_row("raw_error_codes", str(payload.get("raw_error_codes")))
    return table


def render_bars_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Bars: {payload['symbol']} ({payload['bar_size']}, {payload['duration']})")
    table.add_column("Date", style="cyan")
    table.add_column("Open", justify="right")
    table.add_column("High", justify="right")
    table.add_column("Low", justify="right")
    table.add_column("Close", justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("Average", justify="right")
    table.add_column("Count", justify="right")
    for row in payload["rows"]:
        table.add_row(
            str(row["date"]),
            "" if row["open"] is None else str(row["open"]),
            "" if row["high"] is None else str(row["high"]),
            "" if row["low"] is None else str(row["low"]),
            "" if row["close"] is None else str(row["close"]),
            "" if row["volume"] is None else str(row["volume"]),
            "" if row["average"] is None else str(row["average"]),
            "" if row["bar_count"] is None else str(row["bar_count"]),
        )
    return table


def render_quote_watch_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Quote Watch: {payload['symbol']} ({payload['row_count']} updates)")
    table.add_column("Update", justify="right")
    table.add_column("Observed At")
    table.add_column("Source")
    table.add_column("Bid", justify="right")
    table.add_column("Ask", justify="right")
    table.add_column("Last", justify="right")
    table.add_column("Volume", justify="right")
    for row in payload["rows"]:
        table.add_row(
            str(row["update_index"]),
            "" if row.get("observed_at") is None else str(row["observed_at"]),
            str(row["quote_source"]),
            "" if row["bid"] is None else str(row["bid"]),
            "" if row["ask"] is None else str(row["ask"]),
            "" if row["last"] is None else str(row["last"]),
            "" if row["volume"] is None else str(row["volume"]),
        )
    return table


def print_json(payload: Dict[str, object]) -> None:
    console.print_json(data=payload)


@app.command()
def doctor(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to inspect."),
    check_port: bool = typer.Option(True, "--check-port/--no-check-port", help="Check whether the configured port is reachable."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of tables."),
) -> None:
    config, exists, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    connection_result = None
    if check_port:
        connection_result = test_tcp_connection(selected_profile.host, selected_profile.port)

    payload = {
        "version": package_version(),
        "python": platform.python_version(),
        "config_file": str(CONFIG_FILE),
        "config_exists": exists,
        "default_profile": config.default_profile,
        "selected_profile": profile_to_dict(
            selected_name,
            selected_profile,
            is_default=selected_name == config.default_profile,
        ),
        "profiles": [
            profile_to_dict(name, current, is_default=name == config.default_profile)
            for name, current in sorted(config.profiles.items())
        ],
        "port_check": connection_result.to_dict() if connection_result else None,
    }

    if json_output:
        print_json(payload)
        return

    table = Table(title="Doctor")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("version", str(payload["version"]))
    table.add_row("python", str(payload["python"]))
    table.add_row("config_file", str(payload["config_file"]))
    table.add_row("config_exists", "yes" if exists else "no")
    table.add_row("default_profile", config.default_profile)
    table.add_row("selected_profile", selected_name)
    console.print(table)
    console.print(render_profiles_table(config))
    if connection_result:
        console.print(render_connection_result(connection_result))


@profile_app.command("init")
def profile_init(
    force: bool = typer.Option(False, "--force", help="Overwrite the config file if it already exists."),
) -> None:
    try:
        target = save_config(default_config(), force=force)
    except FileExistsError as exc:
        exit_with_error(str(exc), code=ERROR_CONFIG_ALREADY_EXISTS, exit_code=EXIT_CODE_CONFIG)
    console.print(f"[green]Created config:[/green] {target}")


@profile_app.command("list")
def profile_list(
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, exists = load_or_exit(json_output=json_output)
    profiles = [
        profile_to_dict(name, current, is_default=name == config.default_profile)
        for name, current in sorted(config.profiles.items())
    ]
    if json_output:
        print_json({"config_exists": exists, "config_file": str(CONFIG_FILE), "profiles": profiles})
        return
    console.print(render_profiles_table(config))
    if not exists:
        console.print(f"[yellow]Using in-memory defaults because {CONFIG_FILE} does not exist yet.[/yellow]")


@profile_app.command("show")
def profile_show(
    name: Optional[str] = typer.Argument(None, help="Profile name. Defaults to the configured default profile."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(name, json_output=json_output)
    payload = profile_to_dict(selected_name, selected_profile, is_default=selected_name == config.default_profile)
    if json_output:
        print_json(payload)
        return
    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))


@connect_app.command("test")
def connect_test(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to test."),
    timeout: float = typer.Option(2.0, "--timeout", min=0.1, help="Socket timeout in seconds."),
    tcp_check: bool = typer.Option(True, "--tcp/--no-tcp", help="Run a raw TCP port check."),
    api_check: bool = typer.Option(True, "--api/--no-api", help="Run an IBKR API handshake check."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    if not tcp_check and not api_check:
        exit_with_error(
            "At least one of --tcp or --api must be enabled.",
            code=ERROR_INVALID_ARGUMENTS,
            exit_code=EXIT_CODE_USAGE,
            json_output=json_output,
            details={"tcp": tcp_check, "api": api_check},
        )

    tcp_result = test_tcp_connection(selected_profile.host, selected_profile.port, timeout=timeout) if tcp_check else None
    api_result = check_api_connection(selected_profile, timeout=timeout) if api_check else None

    payload = {
        "profile": selected_name,
        "tcp_connection": tcp_result.to_dict() if tcp_result else None,
        "api_connection": api_result.to_dict() if api_result else None,
    }
    connectivity_failed = (tcp_result and not tcp_result.ok) or (api_result and not api_result.ok)
    if json_output and not connectivity_failed:
        print_json(payload)
    elif json_output and connectivity_failed:
        exit_with_error(
            f"Connectivity checks failed for profile '{selected_name}'.",
            code=ERROR_CONNECTIVITY_CHECK_FAILED,
            exit_code=EXIT_CODE_CONNECTIVITY,
            json_output=True,
            details=payload,
        )
    else:
        console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
        if tcp_result:
            console.print(render_connection_result(tcp_result))
        if api_result:
            console.print(render_api_connection_result(api_result))
    if connectivity_failed:
        raise typer.Exit(code=EXIT_CODE_CONNECTIVITY)


@account_app.command("summary")
def account_summary(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    tag: Optional[List[str]] = typer.Option(None, "--tag", help="Limit output to one or more summary tags. Repeatable."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_account_summary(
            selected_profile,
            timeout=timeout,
            account=account,
            tags=tag,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch account summary via profile '{selected_name}': {exc}",
            code=ERROR_ACCOUNT_QUERY_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "account": account, "tags": tag},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    if payload["selected_account"]:
        console.print(render_account_summary_table(payload["rows"], str(payload["selected_account"])))
    else:
        grouped_rows: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        for row in payload["rows"]:
            grouped_rows[str(row["account"])].append(row)
        for index, account_key in enumerate(sorted(grouped_rows)):
            if index:
                console.print()
            console.print(render_account_summary_table(grouped_rows[account_key], account_key))


@app.command()
def positions(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_positions(selected_profile, timeout=timeout, account=account)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch positions via profile '{selected_name}': {exc}",
            code=ERROR_ACCOUNT_QUERY_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "account": account},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_positions_table(payload["rows"], account))


@orders_app.command("open")
def orders_open(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_open_orders(selected_profile, timeout=timeout, account=account)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch open orders via profile '{selected_name}': {exc}",
            code=ERROR_ORDER_QUERY_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "account": account},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_open_orders_table(payload["rows"], account))


@orders_app.command("completed")
def orders_completed(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    api_only: bool = typer.Option(False, "--api-only", help="Only include API-originated orders."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_completed_orders(
            selected_profile,
            timeout=timeout,
            account=account,
            api_only=api_only,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch completed orders via profile '{selected_name}': {exc}",
            code=ERROR_ORDER_QUERY_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "account": account, "api_only": api_only},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_completed_orders_table(payload["rows"], account))


@orders_app.command("executions")
def orders_executions(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_executions(selected_profile, timeout=timeout, account=account)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch executions via profile '{selected_name}': {exc}",
            code=ERROR_ORDER_QUERY_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "account": account},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_executions_table(payload["rows"], account))


def execute_trade_command(
    action: str,
    symbol: str,
    quantity: float,
    profile: Optional[str],
    exchange: str,
    currency: str,
    order_type: str,
    limit_price: Optional[float],
    tif: str,
    outside_rth: bool,
    preview: bool,
    submit: bool,
    account: Optional[str],
    timeout: float,
    json_output: bool,
    stop_price: Optional[float] = None,
    trail_amount: Optional[float] = None,
    trail_percent: Optional[float] = None,
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
) -> None:
    if preview == submit:
        exit_with_error(
            "Choose exactly one of --preview or --submit.",
            code=ERROR_INVALID_ARGUMENTS,
            exit_code=EXIT_CODE_USAGE,
            json_output=json_output,
            details={"preview": preview, "submit": submit},
        )
        return

    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        order_kwargs = dict(
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
            trail_stop_price=trail_amount,
            trail_percent=trail_percent,
            take_profit_price=take_profit,
            stop_loss_price=stop_loss,
        )
        if preview:
            payload = preview_stock_order(selected_profile, **order_kwargs)
        else:
            payload = submit_stock_order(selected_profile, **order_kwargs)
    except Exception as exc:
        operation = "preview" if preview else "submit"
        exit_with_error(
            f"Failed to {operation} {action.lower()} order for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_ORDER_OPERATION_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "operation": operation,
                "action": action,
                "symbol": symbol,
                "quantity": quantity,
                "order_type": order_type,
                "account": account,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    if preview:
        console.print(render_order_preview_table(payload))
    else:
        console.print(render_trade_result_table(payload))


@app.command()
def buy(
    symbol: str = typer.Argument(
        ...,
        help="Trading symbol. Examples: AAPL (stock), USDJPY (forex), ESZ6 (future), SPY260417C700 (option).",
    ),
    quantity: float = typer.Argument(..., help="Order quantity."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange override for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency override for stock and futures qualification."),
    order_type: str = typer.Option("MKT", "--type", help="Order type: MKT, LMT, STP, STP LMT, or TRAIL."),
    limit_price: Optional[float] = typer.Option(None, "--limit", help="Limit price (required for LMT / STP LMT)."),
    stop_price: Optional[float] = typer.Option(None, "--stop", help="Stop trigger price (required for STP / STP LMT, optional for TRAIL)."),
    trail_amount: Optional[float] = typer.Option(None, "--trail-amount", help="Trailing amount in dollars (for TRAIL orders)."),
    trail_percent: Optional[float] = typer.Option(None, "--trail-percent", help="Trailing percentage (for TRAIL orders)."),
    take_profit: Optional[float] = typer.Option(None, "--take-profit", help="Take-profit limit price (creates a bracket order)."),
    stop_loss: Optional[float] = typer.Option(None, "--stop-loss", help="Stop-loss price (creates a bracket order)."),
    tif: str = typer.Option("DAY", "--tif", help="Time in force."),
    outside_rth: bool = typer.Option(False, "--outside-rth", help="Allow execution outside regular trading hours."),
    preview: bool = typer.Option(False, "--preview", help="Run a what-if preview instead of placing an order."),
    submit: bool = typer.Option(False, "--submit", help="Place the order for real."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    execute_trade_command(
        "BUY", symbol, quantity, profile, exchange, currency, order_type, limit_price,
        tif, outside_rth, preview, submit, account, timeout, json_output,
        stop_price=stop_price, trail_amount=trail_amount, trail_percent=trail_percent,
        take_profit=take_profit, stop_loss=stop_loss,
    )


@app.command()
def sell(
    symbol: str = typer.Argument(
        ...,
        help="Trading symbol. Examples: AAPL (stock), USDJPY (forex), ESZ6 (future), SPY260417C700 (option).",
    ),
    quantity: float = typer.Argument(..., help="Order quantity."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange override for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency override for stock and futures qualification."),
    order_type: str = typer.Option("MKT", "--type", help="Order type: MKT, LMT, STP, STP LMT, or TRAIL."),
    limit_price: Optional[float] = typer.Option(None, "--limit", help="Limit price (required for LMT / STP LMT)."),
    stop_price: Optional[float] = typer.Option(None, "--stop", help="Stop trigger price (required for STP / STP LMT, optional for TRAIL)."),
    trail_amount: Optional[float] = typer.Option(None, "--trail-amount", help="Trailing amount in dollars (for TRAIL orders)."),
    trail_percent: Optional[float] = typer.Option(None, "--trail-percent", help="Trailing percentage (for TRAIL orders)."),
    take_profit: Optional[float] = typer.Option(None, "--take-profit", help="Take-profit limit price (creates a bracket order)."),
    stop_loss: Optional[float] = typer.Option(None, "--stop-loss", help="Stop-loss price (creates a bracket order)."),
    tif: str = typer.Option("DAY", "--tif", help="Time in force."),
    outside_rth: bool = typer.Option(False, "--outside-rth", help="Allow execution outside regular trading hours."),
    preview: bool = typer.Option(False, "--preview", help="Run a what-if preview instead of placing an order."),
    submit: bool = typer.Option(False, "--submit", help="Place the order for real."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    execute_trade_command(
        "SELL", symbol, quantity, profile, exchange, currency, order_type, limit_price,
        tif, outside_rth, preview, submit, account, timeout, json_output,
        stop_price=stop_price, trail_amount=trail_amount, trail_percent=trail_percent,
        take_profit=take_profit, stop_loss=stop_loss,
    )


@orders_app.command("cancel")
def orders_cancel(
    order_id: int = typer.Argument(..., help="IBKR order ID to cancel."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = cancel_open_order(selected_profile, order_id=order_id, timeout=timeout, account=account)
    except Exception as exc:
        exit_with_error(
            f"Failed to cancel order '{order_id}' via profile '{selected_name}': {exc}",
            code=ERROR_ORDER_OPERATION_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "order_id": order_id, "account": account},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_trade_result_table(payload))


@orders_app.command("modify")
def orders_modify(
    order_id: int = typer.Argument(..., help="IBKR order ID to modify."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    limit_price: Optional[float] = typer.Option(None, "--limit", help="New limit price."),
    stop_price: Optional[float] = typer.Option(None, "--stop", help="New stop / aux price."),
    quantity: Optional[float] = typer.Option(None, "--quantity", "-q", help="New order quantity."),
    order_type: Optional[str] = typer.Option(None, "--type", help="New order type: MKT, LMT, STP, STP LMT, TRAIL."),
    tif: Optional[str] = typer.Option(None, "--tif", help="New time-in-force: DAY, GTC, IOC, etc."),
    outside_rth: Optional[bool] = typer.Option(None, "--outside-rth", help="Allow execution outside regular trading hours."),
    account: Optional[str] = typer.Option(None, "--account", help="IBKR account identifier."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    if all(v is None for v in (limit_price, stop_price, quantity, order_type, tif, outside_rth)):
        exit_with_error(
            "Provide at least one field to modify (e.g. --limit, --stop, --quantity, --type, --tif, --outside-rth).",
            code=ERROR_INVALID_ARGUMENTS,
            exit_code=EXIT_CODE_USAGE,
            json_output=json_output,
            details={"order_id": order_id},
        )
        return

    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = modify_order(
            selected_profile,
            order_id=order_id,
            limit_price=limit_price,
            aux_price=stop_price,
            quantity=quantity,
            order_type=order_type,
            tif=tif,
            outside_rth=outside_rth,
            timeout=timeout,
            account=account,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to modify order '{order_id}' via profile '{selected_name}': {exc}",
            code=ERROR_ORDER_OPERATION_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "order_id": order_id, "account": account},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_trade_result_table(payload))


@app.command()
def quote(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    watch: bool = typer.Option(False, "--watch", help="Stream a finite number of quote updates."),
    updates: int = typer.Option(5, "--updates", min=1, help="Number of updates to capture in watch mode."),
    interval: float = typer.Option(2.0, "--interval", min=0.1, help="Seconds to wait between updates in watch mode."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    debug_market_data: bool = typer.Option(False, "--debug-market-data", help="Include market data request diagnostics."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        if watch:
            payload = watch_quote(
                selected_profile,
                symbol=symbol,
                exchange=exchange,
                currency=currency,
                updates=updates,
                interval=interval,
                timeout=timeout,
            )
        else:
            payload = get_quote_snapshot(
                selected_profile,
                symbol=symbol,
                exchange=exchange,
                currency=currency,
                timeout=timeout,
                debug_market_data=debug_market_data,
            )
    except Exception as exc:
        operation = "watch quote" if watch else "fetch quote"
        exit_with_error(
            f"Failed to {operation} for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_MARKET_DATA_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "operation": "watch" if watch else "snapshot",
                "symbol": symbol,
                "exchange": exchange,
                "currency": currency,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    if watch:
        console.print(render_quote_watch_table(payload))
    else:
        console.print(render_quote_table(payload))


@app.command("bars")
def bars(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    end: str = typer.Option("", "--end", help="End timestamp, for example '20260317 16:00:00'. Empty means now."),
    duration: str = typer.Option("1 D", "--duration", help="Historical duration, for example '1 D' or '2 W'."),
    bar_size: str = typer.Option("5 mins", "--bar-size", help="Bar size, for example '1 min' or '1 day'."),
    what_to_show: str = typer.Option("TRADES", "--what-to-show", help="Historical source, for example TRADES or MIDPOINT."),
    use_rth: bool = typer.Option(True, "--rth/--all-hours", help="Use regular trading hours only."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_historical_bars(
            selected_profile,
            symbol=symbol,
            exchange=exchange,
            currency=currency,
            end=end,
            duration=duration,
            bar_size=bar_size,
            what_to_show=what_to_show,
            use_rth=use_rth,
            timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch historical bars for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_MARKET_DATA_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "symbol": symbol,
                "exchange": exchange,
                "currency": currency,
                "duration": duration,
                "bar_size": bar_size,
                "what_to_show": what_to_show,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_bars_table(payload))


def render_news_providers_table(rows: List[Dict[str, object]]) -> Table:
    table = Table(title="News Providers")
    table.add_column("Code", style="cyan")
    table.add_column("Name")
    for row in rows:
        table.add_row(str(row["code"]), str(row["name"]))
    return table


def render_news_headlines_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"News: {payload['symbol']} ({payload['count']} headlines)")
    table.add_column("Time", style="cyan")
    table.add_column("Provider")
    table.add_column("Headline")
    table.add_column("Sentiment", justify="right")
    table.add_column("Confidence", justify="right")
    table.add_column("Article ID", style="dim")
    for row in payload["rows"]:
        sentiment = row.get("sentiment")
        if sentiment is not None:
            sentiment_str = f"[green]{sentiment}[/green]" if sentiment >= 0 else f"[red]{sentiment}[/red]"
        else:
            sentiment_str = ""
        confidence = row.get("confidence")
        confidence_str = str(round(confidence, 2)) if confidence is not None else ""
        table.add_row(
            str(row["time"]),
            str(row["provider_code"]),
            str(row["headline"]),
            sentiment_str,
            confidence_str,
            str(row["article_id"]),
        )
    return table


def render_news_article_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Article: {payload['article_id']}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("provider_code", str(payload["provider_code"]))
    table.add_row("article_id", str(payload["article_id"]))
    table.add_row("article_type", str(payload.get("article_type") or ""))
    return table


@news_app.command("providers")
def news_providers(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    timeout: float = typer.Option(4.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """List available news providers."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_news_providers(selected_profile, timeout=timeout)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch news providers via profile '{selected_name}': {exc}",
            code=ERROR_NEWS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_news_providers_table(payload["rows"]))


@news_app.command("headlines")
def news_headlines(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    provider_codes: str = typer.Option("", "--providers", help="Comma-separated provider codes, e.g. 'BRFG,DJNL'. Empty means all."),
    start: str = typer.Option("", "--start", help="Start time, format 'YYYYMMDD HH:MM:SS' in UTC."),
    end: str = typer.Option("", "--end", help="End time, format 'YYYYMMDD HH:MM:SS' in UTC."),
    limit: int = typer.Option(10, "--limit", min=1, max=300, help="Maximum number of headlines to return."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Fetch historical news headlines for a symbol."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_news_headlines(
            selected_profile,
            symbol=symbol,
            provider_codes=provider_codes,
            start=start,
            end=end,
            limit=limit,
            exchange=exchange,
            currency=currency,
            timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch news headlines for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_NEWS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "symbol": symbol,
                "provider_codes": provider_codes,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_news_headlines_table(payload))


@news_app.command("article")
def news_article(
    provider_code: str = typer.Argument(..., help="News provider code, e.g. BRFG."),
    article_id: str = typer.Argument(..., help="Article ID from a headlines response."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Fetch a full news article by provider code and article ID."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_news_article(
            selected_profile,
            provider_code=provider_code,
            article_id=article_id,
            timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch news article '{article_id}' via profile '{selected_name}': {exc}",
            code=ERROR_NEWS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "provider_code": provider_code,
                "article_id": article_id,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_news_article_table(payload))
    if payload.get("article_text"):
        console.print()
        console.print(payload["article_text"])


def render_option_chains_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Option Chains: {payload['symbol']}")
    table.add_column("Exchange", style="cyan")
    table.add_column("Trading Class")
    table.add_column("Multiplier", justify="right")
    table.add_column("Expirations", justify="right")
    table.add_column("Strikes", justify="right")
    table.add_column("Nearest Expirations")
    for row in payload["rows"]:
        nearest = ", ".join(row["expirations"][:5])
        if row["expiration_count"] > 5:
            nearest += f" ... (+{row['expiration_count'] - 5} more)"
        table.add_row(
            str(row["exchange"]),
            str(row["trading_class"]),
            str(row["multiplier"]),
            str(row["expiration_count"]),
            str(row["strike_count"]),
            nearest,
        )
    return table


def render_option_quotes_table(payload: Dict[str, object]) -> Table:
    title = f"Options: {payload['symbol']} exp={payload['expiration']} ({payload['count']} contracts)"
    table = Table(title=title)
    table.add_column("Strike", justify="right", style="cyan")
    table.add_column("Right")
    table.add_column("Bid", justify="right")
    table.add_column("Ask", justify="right")
    table.add_column("Last", justify="right")
    table.add_column("Vol", justify="right")
    table.add_column("OI", justify="right")
    table.add_column("IV", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Gamma", justify="right")
    table.add_column("Theta", justify="right")
    table.add_column("Vega", justify="right")

    def fmt(v: object, decimals: int = 2) -> str:
        if v is None:
            return ""
        return f"{float(v):.{decimals}f}"

    def fmt_greeks(v: object) -> str:
        if v is None:
            return ""
        return f"{float(v):.4f}"

    for row in payload["rows"]:
        table.add_row(
            fmt(row["strike"]),
            str(row["right"]),
            fmt(row["bid"]),
            fmt(row["ask"]),
            fmt(row["last"]),
            fmt(row["volume"], 0),
            fmt(row["open_interest"], 0),
            fmt_greeks(row["implied_vol"]),
            fmt_greeks(row["delta"]),
            fmt_greeks(row["gamma"]),
            fmt_greeks(row["theta"]),
            fmt_greeks(row["vega"]),
        )
    return table


@options_app.command("chain")
def options_chain(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """List available option chains (expirations and strikes) for a symbol."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_option_chains(
            selected_profile,
            symbol=symbol,
            exchange=exchange,
            currency=currency,
            timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch option chains for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_OPTIONS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "symbol": symbol},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_option_chains_table(payload))


@options_app.command("quotes")
def options_quotes(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    expiration: str = typer.Argument(..., help="Expiration date in YYYYMMDD format, e.g. 20260320."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    right: str = typer.Option("", "--right", help="Filter by C (call) or P (put). Empty means both."),
    strike: Optional[List[float]] = typer.Option(None, "--strike", help="Specific strike prices. Repeatable. Omit to auto-select near the money."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Fetch option quotes with greeks for a specific expiration."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_option_quotes(
            selected_profile,
            symbol=symbol,
            expiration=expiration,
            strikes=strike if strike else None,
            right=right,
            exchange=exchange,
            currency=currency,
            timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch option quotes for '{symbol}' exp={expiration} via profile '{selected_name}': {exc}",
            code=ERROR_OPTIONS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "symbol": symbol,
                "expiration": expiration,
                "right": right,
                "strikes": strike,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_option_quotes_table(payload))


def render_scanner_params_table(payload: Dict[str, object], section: str) -> Table:
    if section == "codes":
        table = Table(title=f"Scan Codes ({payload['scan_code_count']})")
        table.add_column("Code", style="cyan")
        table.add_column("Description")
        for row in payload["scan_codes"]:
            table.add_row(str(row["code"]), str(row["display_name"]))
    elif section == "instruments":
        table = Table(title=f"Instruments ({payload['instrument_count']})")
        table.add_column("Type", style="cyan")
        table.add_column("Name")
        for row in payload["instruments"]:
            table.add_row(str(row["type"]), str(row["name"]))
    else:
        table = Table(title=f"Locations ({payload['location_count']})")
        table.add_column("Code", style="cyan")
        table.add_column("Description")
        for row in payload["locations"]:
            table.add_row(str(row["code"]), str(row["display_name"]))
    return table


def render_scanner_results_table(payload: Dict[str, object]) -> Table:
    title = f"Scanner: {payload['scan_code']} ({payload['count']} results)"
    table = Table(title=title)
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Symbol")
    table.add_column("SecType")
    table.add_column("Exchange")
    table.add_column("Currency")
    table.add_column("Industry")
    table.add_column("Benchmark", justify="right")
    table.add_column("Projection", justify="right")
    for row in payload["rows"]:
        table.add_row(
            str(row["rank"]),
            str(row["symbol"]),
            str(row["sec_type"]),
            str(row["primary_exchange"] or row["exchange"]),
            str(row["currency"]),
            str(row["industry"] or ""),
            str(row["benchmark"] or ""),
            str(row["projection"] or ""),
        )
    return table


@scanner_app.command("params")
def scanner_params(
    section: str = typer.Argument("codes", help="Section to show: codes, instruments, or locations."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """List available scanner parameters (scan codes, instruments, locations)."""
    normalized_section = section.lower()
    if normalized_section not in ("codes", "instruments", "locations"):
        exit_with_error(
            f"Unknown section '{section}'. Use codes, instruments, or locations.",
            code=ERROR_SCANNER_REQUEST_FAILED,
            exit_code=EXIT_CODE_USAGE,
            json_output=json_output,
            details={"section": section},
        )
        return

    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_scanner_parameters(selected_profile, timeout=timeout)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch scanner parameters via profile '{selected_name}': {exc}",
            code=ERROR_SCANNER_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name},
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_scanner_params_table(payload, normalized_section))


@scanner_app.command("run")
def scanner_run(
    scan_code: str = typer.Argument(..., help="Scan code, e.g. TOP_PERC_GAIN, MOST_ACTIVE, HOT_BY_VOLUME."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    instrument: str = typer.Option("STK", "--instrument", help="Instrument type, e.g. STK, ETF.EQ.US."),
    location: str = typer.Option("STK.US.MAJOR", "--location", help="Location code, e.g. STK.US.MAJOR, STK.NYSE."),
    num_rows: int = typer.Option(20, "--limit", min=1, max=50, help="Maximum number of results."),
    above_price: Optional[float] = typer.Option(None, "--above-price", help="Minimum price filter."),
    below_price: Optional[float] = typer.Option(None, "--below-price", help="Maximum price filter."),
    above_volume: Optional[int] = typer.Option(None, "--above-volume", help="Minimum volume filter."),
    market_cap_above: Optional[float] = typer.Option(None, "--market-cap-above", help="Minimum market cap filter."),
    market_cap_below: Optional[float] = typer.Option(None, "--market-cap-below", help="Maximum market cap filter."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Run a market scan and return matching instruments."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = run_scanner(
            selected_profile,
            scan_code=scan_code,
            instrument=instrument,
            location_code=location,
            num_rows=num_rows,
            above_price=above_price,
            below_price=below_price,
            above_volume=above_volume,
            market_cap_above=market_cap_above,
            market_cap_below=market_cap_below,
            timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to run scanner '{scan_code}' via profile '{selected_name}': {exc}",
            code=ERROR_SCANNER_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={
                "profile": selected_name,
                "scan_code": scan_code,
                "instrument": instrument,
                "location": location,
            },
        )
        return

    response = {
        "profile": selected_name,
        **payload,
    }
    if json_output:
        print_json(response)
        return

    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_scanner_results_table(payload))


@app.command()
def update() -> None:
    """Check for and install the latest version of ibkr-cli."""
    current = package_version()
    console.print(f"Current version: {current}")
    console.print("Checking for updates...")
    latest = check_for_update(current, skip_cache=True)
    if not latest:
        console.print("[green]Already up to date.[/green]")
        return
    console.print(f"New version available: {latest}")
    console.print("Upgrading...")
    success, output = run_update()
    if not success:
        console.print(f"[red]Upgrade failed:[/red] {output}")
        raise typer.Exit(code=EXIT_CODE_GENERAL)
    # Verify the upgrade in a new process (importlib.metadata caches in-process)
    result = subprocess.run(
        ["ibkr", "--version"], capture_output=True, text=True, timeout=10,
    )
    new_version = result.stdout.strip() if result.returncode == 0 else None
    if not new_version or _parse_version(new_version) <= _parse_version(current):
        console.print(f"[red]Upgrade failed:[/red] version is still {current} after upgrade.")
        console.print(f"[dim]Installer output: {output}[/dim]")
        raise typer.Exit(code=EXIT_CODE_GENERAL)
    console.print(f"[green]Successfully upgraded to {new_version}.[/green]")


# ---------------------------------------------------------------------------
# Fundamentals renderers
# ---------------------------------------------------------------------------


def render_fundamental_snapshot_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Company Snapshot: {payload['symbol']}")
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    simple_fields = [
        ("industry", "Industry"),
        ("employees", "Employees"),
        ("shares_outstanding", "Shares Outstanding"),
        ("reporting_currency", "Reporting Currency"),
        ("website", "Website"),
        ("address", "Address"),
    ]
    for key, label in simple_fields:
        val = payload.get(key)
        if val is not None:
            table.add_row(label, str(val))

    ratios = payload.get("ratios", {})
    ratio_display = [
        ("price", "Price"),
        ("market_cap", "Market Cap"),
        ("pe_ratio", "P/E Ratio"),
        ("price_to_book", "Price / Book"),
        ("dividend_yield", "Dividend Yield"),
        ("ttm_revenue", "TTM Revenue"),
        ("ttm_ebitda", "TTM EBITDA"),
        ("ttm_net_income", "TTM Net Income"),
        ("ttm_eps", "TTM EPS"),
        ("ttm_gross_margin", "TTM Gross Margin"),
        ("ttm_operating_margin", "TTM Operating Margin"),
        ("ttm_net_margin", "TTM Net Margin"),
        ("ttm_roe", "TTM ROE"),
        ("ttm_roa", "TTM ROA"),
        ("debt_to_equity", "Debt / Equity"),
        ("current_ratio", "Current Ratio"),
        ("quick_ratio", "Quick Ratio"),
        ("beta", "Beta"),
        ("52w_high", "52W High"),
        ("52w_low", "52W Low"),
    ]
    for key, label in ratio_display:
        val = ratios.get(key)
        if val is not None:
            table.add_row(label, str(val))

    rec = payload.get("consensus_recommendation")
    if rec:
        table.add_row("Consensus Recommendation", str(rec))

    return table


def render_fundamental_snapshot_officers(payload: Dict[str, object]) -> Table:
    officers = payload.get("officers", [])
    table = Table(title=f"Officers: {payload['symbol']} ({len(officers)})")
    table.add_column("Name", style="cyan")
    table.add_column("Title")
    for officer in officers:
        table.add_row(
            str(officer.get("name", "")),
            str(officer.get("title", "") or ""),
        )
    return table


def render_fundamental_summary_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Financial Summary: {payload['symbol']} ({payload.get('count', 0)} metrics)")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_column("Report Type")
    table.add_column("Period")
    table.add_column("Date", style="dim")
    for row in payload.get("rows", []):
        table.add_row(
            str(row.get("metric", "")),
            str(row.get("value", "")),
            str(row.get("report_type", "") or ""),
            str(row.get("period", "") or ""),
            str(row.get("date", "") or ""),
        )
    return table


def render_fundamental_financials_table(payload: Dict[str, object], section_key: str, title: str) -> Table:
    section = payload.get(section_key, {})
    periods = section.get("periods", [])
    data = section.get("data", {})
    table = Table(title=f"{title}: {payload['symbol']}")
    table.add_column("Line Item", style="cyan")
    for period in periods:
        table.add_column(period, justify="right")
    for label, period_values in data.items():
        row = [label]
        for period in periods:
            val = period_values.get(period)
            row.append(str(val) if val is not None else "")
        table.add_row(*row)
    return table


def render_fundamental_ownership_table(payload: Dict[str, object]) -> Table:
    table = Table(title=f"Ownership: {payload['symbol']} ({payload.get('count', 0)} holders)")
    table.add_column("Name", style="cyan")
    table.add_column("Shares", justify="right")
    table.add_column("Percent", justify="right")
    table.add_column("Date", style="dim")
    for row in payload.get("rows", []):
        pct = row.get("percent")
        pct_str = f"{pct}%" if pct is not None else ""
        table.add_row(
            str(row.get("name", "")),
            str(row.get("shares", "")),
            pct_str,
            str(row.get("date", "") or ""),
        )
    return table


# ---------------------------------------------------------------------------
# Fundamentals commands
# ---------------------------------------------------------------------------


@fundamentals_app.command("snapshot")
def fundamentals_snapshot(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Company overview: ratios, officers, industry, and forecasts (requires Reuters Fundamentals subscription)."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_fundamental_snapshot(
            selected_profile, symbol=symbol, exchange=exchange, currency=currency, timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch company snapshot for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_FUNDAMENTALS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "symbol": symbol, "report_type": "ReportSnapshot"},
        )
        return
    response = {"profile": selected_name, **payload}
    if json_output:
        print_json(response)
        return
    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_fundamental_snapshot_table(payload))
    if payload.get("officers"):
        console.print(render_fundamental_snapshot_officers(payload))
    summary = payload.get("business_summary")
    if summary:
        console.print(f"\n[bold]Business Summary[/bold]\n{summary[:500]}{'...' if len(summary) > 500 else ''}")


@fundamentals_app.command("summary")
def fundamentals_summary(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Financial summary with key metrics across periods (requires Reuters Fundamentals subscription)."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_fundamental_summary(
            selected_profile, symbol=symbol, exchange=exchange, currency=currency, timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch financial summary for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_FUNDAMENTALS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "symbol": symbol, "report_type": "ReportsFinSummary"},
        )
        return
    response = {"profile": selected_name, **payload}
    if json_output:
        print_json(response)
        return
    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_fundamental_summary_table(payload))


@fundamentals_app.command("financials")
def fundamentals_financials(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Full financial statements: income, balance sheet, cash flow (requires Reuters Fundamentals subscription)."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_fundamental_financials(
            selected_profile, symbol=symbol, exchange=exchange, currency=currency, timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch financial statements for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_FUNDAMENTALS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "symbol": symbol, "report_type": "ReportsFinStatements"},
        )
        return
    response = {"profile": selected_name, **payload}
    if json_output:
        print_json(response)
        return
    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    section_display = [
        ("income_statement_annual", "Income Statement (Annual)"),
        ("balance_sheet_annual", "Balance Sheet (Annual)"),
        ("cash_flow_annual", "Cash Flow (Annual)"),
        ("income_statement_interim", "Income Statement (Interim)"),
        ("balance_sheet_interim", "Balance Sheet (Interim)"),
        ("cash_flow_interim", "Cash Flow (Interim)"),
    ]
    for section_key, title in section_display:
        if section_key in payload:
            console.print(render_fundamental_financials_table(payload, section_key, title))


@fundamentals_app.command("ownership")
def fundamentals_ownership(
    symbol: str = typer.Argument(..., help="Ticker symbol, for example AAPL."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    exchange: str = typer.Option("SMART", "--exchange", help="Exchange to use for contract qualification."),
    currency: str = typer.Option("USD", "--currency", help="Currency to use for contract qualification."),
    timeout: float = typer.Option(10.0, "--timeout", min=0.1, help="API timeout in seconds."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Ownership structure: institutional and insider holders (requires Reuters Fundamentals subscription)."""
    config, _, selected_name, selected_profile = resolve_profile_or_exit(profile, json_output=json_output)
    try:
        payload = get_fundamental_ownership(
            selected_profile, symbol=symbol, exchange=exchange, currency=currency, timeout=timeout,
        )
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch ownership data for '{symbol}' via profile '{selected_name}': {exc}",
            code=ERROR_FUNDAMENTALS_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
            details={"profile": selected_name, "symbol": symbol, "report_type": "ReportsOwnership"},
        )
        return
    response = {"profile": selected_name, **payload}
    if json_output:
        print_json(response)
        return
    console.print(render_profile_detail(selected_name, selected_profile, selected_name == config.default_profile))
    console.print(render_fundamental_ownership_table(payload))


def resolve_flex_or_exit(json_output: bool = False) -> tuple[AppConfig, FlexConfig]:
    config, _ = load_or_exit(json_output=json_output)
    flex = get_flex_config(config)
    if not flex.token or not flex.query_id:
        exit_with_error(
            "Flex Queries not configured. Run: ibkr config set flex.token <TOKEN> && ibkr config set flex.query_id <QUERY_ID>",
            code=ERROR_FLEX_REQUEST_FAILED,
            exit_code=EXIT_CODE_CONFIG,
            json_output=json_output,
        )
    return config, flex


# ── Flex renderers ───────────────────────────────────────────


def render_flex_trades_table(rows: Sequence[Dict[str, object]]) -> Table:
    table = Table(title="Trade History (Flex)")
    table.add_column("Date", style="cyan")
    table.add_column("Symbol", style="green")
    table.add_column("Side")
    table.add_column("Qty", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Proceeds", justify="right")
    table.add_column("Commission", justify="right")
    table.add_column("Net Cash", justify="right")
    table.add_column("Realized P&L", justify="right")
    table.add_column("Ccy")
    for r in rows:
        pnl = r.get("realized_pnl", 0)
        pnl_style = "green" if pnl and float(str(pnl)) > 0 else "red" if pnl and float(str(pnl)) < 0 else ""
        table.add_row(
            str(r.get("trade_date") or ""),
            str(r.get("symbol") or ""),
            str(r.get("buy_sell") or ""),
            str(r.get("quantity") or ""),
            str(r.get("price") or ""),
            str(r.get("proceeds") or ""),
            str(r.get("commission") or ""),
            str(r.get("net_cash") or ""),
            f"[{pnl_style}]{pnl}[/{pnl_style}]" if pnl_style else str(pnl),
            str(r.get("currency") or ""),
        )
    return table


def render_flex_pnl_table(payload: Dict[str, object]) -> Table:
    table = Table(title="P&L by Symbol (Flex)")
    table.add_column("Symbol", style="green")
    table.add_column("Description")
    table.add_column("Realized P&L", justify="right")
    table.add_column("Unrealized P&L", justify="right")
    table.add_column("Total P&L", justify="right")
    for r in payload.get("rows", []):
        def _pnl_cell(val: object) -> str:
            v = float(str(val)) if val else 0
            style = "green" if v > 0 else "red" if v < 0 else ""
            text = f"{v:,.2f}"
            return f"[{style}]{text}[/{style}]" if style else text

        table.add_row(
            str(r.get("symbol") or ""),
            str(r.get("description") or ""),
            _pnl_cell(r.get("realized_pnl")),
            _pnl_cell(r.get("unrealized_pnl")),
            _pnl_cell(r.get("total_pnl")),
        )
    table.add_section()
    total_r = payload.get("total_realized", 0)
    total_u = payload.get("total_unrealized", 0)
    total = payload.get("total_pnl", 0)

    def _fmt(v: object) -> str:
        fv = float(str(v)) if v else 0
        style = "green" if fv > 0 else "red" if fv < 0 else ""
        text = f"{fv:,.2f}"
        return f"[bold {style}]{text}[/bold {style}]" if style else f"[bold]{text}[/bold]"

    table.add_row("[bold]TOTAL[/bold]", "", _fmt(total_r), _fmt(total_u), _fmt(total))
    return table


def render_flex_transfers_table(rows: Sequence[Dict[str, object]]) -> Table:
    table = Table(title="Fund Transfers (Flex)")
    table.add_column("Date", style="cyan")
    table.add_column("Type")
    table.add_column("Amount", justify="right")
    table.add_column("Ccy")
    table.add_column("Description")
    for r in rows:
        amount = float(str(r.get("amount") or 0))
        style = "green" if amount > 0 else "red" if amount < 0 else ""
        amount_str = f"{amount:,.2f}"
        table.add_row(
            str(r.get("date") or ""),
            str(r.get("type") or ""),
            f"[{style}]{amount_str}[/{style}]" if style else amount_str,
            str(r.get("currency") or ""),
            str(r.get("description") or ""),
        )
    return table


def render_flex_cash_transactions_table(rows: Sequence[Dict[str, object]]) -> Table:
    table = Table(title="Cash Transactions (Flex)")
    table.add_column("Date", style="cyan")
    table.add_column("Type")
    table.add_column("Symbol", style="green")
    table.add_column("Description")
    table.add_column("Amount", justify="right")
    table.add_column("Ccy")
    for r in rows:
        amount = float(str(r.get("amount") or 0))
        style = "green" if amount > 0 else "red" if amount < 0 else ""
        amount_str = f"{amount:,.2f}"
        table.add_row(
            str(r.get("date") or ""),
            str(r.get("type") or ""),
            str(r.get("symbol") or ""),
            str(r.get("description") or ""),
            f"[{style}]{amount_str}[/{style}]" if style else amount_str,
            str(r.get("currency") or ""),
        )
    return table


# ── Historical data commands (via Flex Queries) ─────────────


@app.command()
def trades(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to look back."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Historical trade records.

    Data is sourced from IBKR Flex Queries and may be delayed up to T-1.
    Requires flex.token and flex.query_id to be configured (see: ibkr config set).
    """
    config, flex = resolve_flex_or_exit(json_output=json_output)
    try:
        payload = get_flex_trades(flex, days=days)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch trades: {exc}",
            code=ERROR_FLEX_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
        )
        return
    if json_output:
        print_json(payload)
        return
    console.print(render_flex_trades_table(payload["rows"]))
    console.print(f"\n[dim]{payload['count']} trade(s) in the last {days} day(s) · data up to T-1[/dim]")


@app.command()
def pnl(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to look back."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Realized and unrealized P&L by symbol.

    Data is sourced from IBKR Flex Queries and may be delayed up to T-1.
    Requires flex.token and flex.query_id to be configured (see: ibkr config set).
    """
    config, flex = resolve_flex_or_exit(json_output=json_output)
    try:
        payload = get_flex_pnl(flex, days=days)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch P&L: {exc}",
            code=ERROR_FLEX_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
        )
        return
    if json_output:
        print_json(payload)
        return
    console.print(render_flex_pnl_table(payload))


@app.command()
def transfers(
    days: int = typer.Option(90, "--days", "-d", help="Number of days to look back."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Fund deposits, withdrawals, and transfers.

    Data is sourced from IBKR Flex Queries and may be delayed up to T-1.
    Requires flex.token and flex.query_id to be configured (see: ibkr config set).
    """
    config, flex = resolve_flex_or_exit(json_output=json_output)
    try:
        payload = get_flex_transfers(flex, days=days)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch transfers: {exc}",
            code=ERROR_FLEX_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
        )
        return
    if json_output:
        print_json(payload)
        return
    console.print(render_flex_transfers_table(payload["rows"]))
    console.print(f"\n[dim]{payload['count']} transfer(s) in the last {days} day(s) · data up to T-1[/dim]")


@app.command()
def dividends(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to look back."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON instead of a table."),
) -> None:
    """Dividends, interest, withholding tax, and other cash transactions.

    Data is sourced from IBKR Flex Queries and may be delayed up to T-1.
    Requires flex.token and flex.query_id to be configured (see: ibkr config set).
    """
    config, flex = resolve_flex_or_exit(json_output=json_output)
    try:
        payload = get_flex_cash_transactions(flex, days=days)
    except Exception as exc:
        exit_with_error(
            f"Failed to fetch cash transactions: {exc}",
            code=ERROR_FLEX_REQUEST_FAILED,
            exit_code=EXIT_CODE_API,
            json_output=json_output,
        )
        return
    if json_output:
        print_json(payload)
        return
    console.print(render_flex_cash_transactions_table(payload["rows"]))
    console.print(f"\n[dim]{payload['count']} transaction(s) in the last {days} day(s) · data up to T-1[/dim]")


# ── Config commands ──────────────────────────────────────────


@config_app.command("show")
def config_show(
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Show current configuration."""
    config, _ = load_or_exit(json_output=json_output)
    flex = get_flex_config(config)
    payload = {
        "config_file": str(CONFIG_FILE),
        "default_profile": config.default_profile,
        "profiles": {name: profile_to_dict(name, p, name == config.default_profile) for name, p in config.profiles.items()},
        "flex": {
            "token": f"{flex.token[:6]}...{flex.token[-4:]}" if len(flex.token) > 10 else ("***" if flex.token else "(not set)"),
            "query_id": flex.query_id or "(not set)",
        },
    }
    if json_output:
        print_json(payload)
        return
    table = Table(title="Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    table.add_row("Config file", str(CONFIG_FILE))
    table.add_row("Default profile", config.default_profile)
    table.add_row("Flex token", payload["flex"]["token"])
    table.add_row("Flex query_id", payload["flex"]["query_id"])
    console.print(table)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key (e.g. flex.token, flex.query_id, default_profile)."),
    value: str = typer.Argument(..., help="Value to set."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Set a configuration value."""
    config, _ = load_or_exit(json_output=json_output)
    try:
        set_config_value(config, key, value)
    except (KeyError, ValueError) as exc:
        exit_with_error(str(exc), exit_code=EXIT_CODE_CONFIG, json_output=json_output)
        return
    save_config(config, force=True)
    if json_output:
        print_json({"ok": True, "key": key, "value": value if "token" not in key else "***"})
    else:
        display_value = "***" if "token" in key else value
        console.print(f"[green]Set {key} = {display_value}[/green]")


@config_app.command("path")
def config_path_cmd() -> None:
    """Show the config file path."""
    console.print(str(CONFIG_FILE))


if __name__ == "__main__":
    app()
