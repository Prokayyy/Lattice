import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    POSITION_INITIAL_BALANCE_SOL,
    POSITION_SOL_USD,
    POSITION_STATE_FILE
)
from trading.live_prices import (  # noqa: E402
    fetch_live_prices,
    SolUsdPriceFeed
)


def state_path():

    path = Path(POSITION_STATE_FILE)

    if path.is_absolute():
        return path

    return ROOT / path


def load_state():

    path = state_path()

    if not path.exists():
        return {
            "starting_balance_sol": (
                POSITION_INITIAL_BALANCE_SOL
            ),
            "cash_sol": POSITION_INITIAL_BALANCE_SOL,
            "open": {},
            "closed": []
        }

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        return {
            "starting_balance_sol": (
                POSITION_INITIAL_BALANCE_SOL
            ),
            "cash_sol": POSITION_INITIAL_BALANCE_SOL,
            "open": {},
            "closed": []
        }

    return {
        "starting_balance_sol": safe_float(
            data.get("starting_balance_sol"),
            POSITION_INITIAL_BALANCE_SOL
        ),
        "cash_sol": safe_float(
            data.get("cash_sol"),
            POSITION_INITIAL_BALANCE_SOL
        ),
        "open": data.get("open", {}) or {},
        "closed": data.get("closed", []) or []
    }


def parse_date(value, end_of_day=False):

    if not value:
        return None

    if value.isdigit():
        return float(value)

    text = value.strip()

    if len(text) == 10:
        dt = datetime.fromisoformat(text)

        if end_of_day:
            dt = dt + timedelta(days=1)

        return dt.replace(
            tzinfo=timezone.utc
        ).timestamp()

    dt = datetime.fromisoformat(
        text.replace("Z", "+00:00")
    )

    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(
        timezone.utc
    ).timestamp()


def format_time(timestamp):

    if not timestamp:
        return "unknown"

    return datetime.fromtimestamp(
        float(timestamp),
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def pct(value):

    return f"{value:.1%}"


def money(value):

    return f"${value:,.2f}"


def price(value):

    return f"${safe_float(value):.8f}"


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def trade_address(trade):

    return (
        trade.get("address")
        or trade.get("token_address")
        or ""
    )


def select_closed_trades(
    trades,
    since,
    until
):

    selected = []

    for trade in trades:
        timestamp = safe_float(
            trade.get("exit_at")
            or trade.get("entry_at")
        )

        if since and timestamp < since:
            continue

        if until and timestamp > until:
            continue

        selected.append(trade)

    return selected


def summarize_closed(trades):

    total = len(trades)
    pnl_values = [
        safe_float(trade.get("pnl_usd"))
        for trade in trades
    ]
    total_pnl = sum(pnl_values)
    wins = [
        value
        for value in pnl_values
        if value > 0
    ]
    losses = [
        value
        for value in pnl_values
        if value < 0
    ]

    return {
        "closed_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (
            len(wins) / total
            if total
            else 0
        ),
        "pnl_usd": total_pnl,
        "average_pnl_usd": (
            total_pnl / total
            if total
            else 0
        ),
        "best_pnl_usd": max(pnl_values) if pnl_values else 0,
        "worst_pnl_usd": min(pnl_values) if pnl_values else 0
    }


def summarize_open(
    open_positions,
    cash_sol=0,
    live_refresh=None,
    sol_usd=None
):

    positions = []
    total_pnl = 0
    total_equity = 0
    sol_usd = safe_float(
        sol_usd,
        POSITION_SOL_USD
    )
    cash_usd = cash_sol * sol_usd

    for position in open_positions.values():
        last_price = safe_float(
            position.get("last_price"),
            position.get("entry_price", 0)
        )
        entry_price = safe_float(
            position.get("entry_price")
        )
        remaining_tokens = safe_float(
            position.get("remaining_tokens")
        )
        realized_usd = safe_float(
            position.get("realized_usd")
        )
        entry_notional = max(
            safe_float(
                position.get("entry_notional_usd")
            ),
            1e-18
        )
        equity = (
            realized_usd
            + remaining_tokens * last_price
        )
        pnl_usd = equity - entry_notional
        pnl_pct = pnl_usd / entry_notional
        total_pnl += pnl_usd
        total_equity += equity

        positions.append({
            "symbol": position.get("symbol", "UNKNOWN"),
            "address": position.get("address", ""),
            "entry_at": position.get("entry_at"),
            "entry_price": entry_price,
            "last_price": last_price,
            "live_refreshed": bool(
                position.get("live_refreshed")
            ),
            "live_previous_last_price": safe_float(
                position.get("live_previous_last_price")
            ),
            "live_liquidity_usd": safe_float(
                position.get("live_liquidity_usd")
            ),
            "live_volume_1h_usd": safe_float(
                position.get("live_volume_1h_usd")
            ),
            "live_pair_address": (
                position.get("live_pair_address")
                or ""
            ),
            "live_refresh_error": (
                position.get("live_refresh_error")
                or ""
            ),
            "price_multiple": (
                last_price
                / max(entry_price, 1e-18)
            ),
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "equity_usd": equity,
            "scaled_out_pct": safe_float(
                position.get("scaled_out_pct")
            ),
            "last_pressure": safe_float(
                position.get("last_pressure")
            ),
            "trailing_stop_price": safe_float(
                position.get("trailing_stop_price")
            )
        })

    positions.sort(
        key=lambda item: item["pnl_usd"],
        reverse=True
    )

    return {
        "open_positions": len(positions),
        "cash_sol": cash_sol,
        "cash_usd": cash_usd,
        "sol_usd": sol_usd,
        "open_equity_usd": total_equity,
        "account_equity_usd": total_equity + cash_usd,
        "open_pnl_usd": total_pnl,
        "positions": positions,
        "live_refresh": live_refresh or {
            "enabled": False
        }
    }


async def refresh_trade_prices(
    open_positions,
    closed_trades,
    refresh_open=False,
    refresh_closed=False
):

    refreshed_positions = {
        key: dict(position)
        for key, position in open_positions.items()
    }
    refreshed_closed = [
        dict(trade)
        for trade in closed_trades
    ]

    addresses = []
    chain_by_address = {}

    if refresh_open:
        for key, position in refreshed_positions.items():
            address = (
                position.get("address")
                or key
            )

            if address:
                addresses.append(address)
                chain_by_address[address] = position.get(
                    "chain",
                    "solana"
                )

    if refresh_closed:
        for trade in refreshed_closed:
            address = trade_address(trade)

            if address:
                addresses.append(address)
                chain_by_address[address] = trade.get(
                    "chain",
                    "solana"
                )

    live_prices, live_refresh = await fetch_live_prices(
        addresses,
        chain_by_address=chain_by_address
    )
    live_refresh["open_enabled"] = refresh_open
    live_refresh["closed_enabled"] = refresh_closed

    if refresh_open:
        for key, position in refreshed_positions.items():
            address = (
                position.get("address")
                or key
            )
            live_price = live_prices.get(address)

            if not live_price:
                if address:
                    position["live_refresh_error"] = "no_live_price"
                continue

            position["live_previous_last_price"] = safe_float(
                position.get("last_price")
            )
            position["last_price"] = live_price["price_usd"]
            position["live_refreshed"] = True
            position["live_refresh_error"] = ""
            position["live_pair_address"] = live_price.get(
                "pair_address",
                ""
            )
            position["live_liquidity_usd"] = live_price.get(
                "liquidity_usd",
                0
            )
            position["live_volume_1h_usd"] = live_price.get(
                "volume_1h_usd",
                0
            )

    if refresh_closed:
        for trade in refreshed_closed:
            address = trade_address(trade)
            live_price = live_prices.get(address)

            if not live_price:
                if address:
                    trade["live_refresh_error"] = "no_live_price"
                continue

            price_usd = live_price["price_usd"]
            entry_price = safe_float(
                trade.get("entry_price")
            )
            exit_price = safe_float(
                trade.get("exit_price")
                or trade.get("last_price")
            )

            trade["live_refreshed"] = True
            trade["live_price"] = price_usd
            trade["live_pair_address"] = live_price.get(
                "pair_address",
                ""
            )
            trade["live_liquidity_usd"] = live_price.get(
                "liquidity_usd",
                0
            )
            trade["live_volume_1h_usd"] = live_price.get(
                "volume_1h_usd",
                0
            )
            trade["live_entry_multiple"] = (
                price_usd / entry_price
                if entry_price > 0
                else 0
            )
            trade["live_exit_multiple"] = (
                price_usd / exit_price
                if exit_price > 0
                else 0
            )

    return refreshed_positions, refreshed_closed, live_refresh


def print_report(
    closed_summary,
    closed_trades,
    open_summary,
    since,
    until,
    include_open,
    live_refresh=None
):

    window = "all time"

    if since or until:
        window = (
            f"{format_time(since) if since else 'beginning'}"
            " to "
            f"{format_time(until) if until else 'now'}"
        )

    print(f"Position performance: {window}")
    print("")
    print("Closed trades")
    print(f"- Trades: {closed_summary['closed_trades']}")
    print(
        f"- Wins/Losses: {closed_summary['wins']}/"
        f"{closed_summary['losses']} "
        f"({pct(closed_summary['win_rate'])} win rate)"
    )
    print(f"- PnL: {money(closed_summary['pnl_usd'])}")
    print(
        f"- Avg / Best / Worst: "
        f"{money(closed_summary['average_pnl_usd'])} / "
        f"{money(closed_summary['best_pnl_usd'])} / "
        f"{money(closed_summary['worst_pnl_usd'])}"
    )

    if live_refresh and live_refresh.get("enabled"):
        print(
            "- Live prices: "
            f"{live_refresh.get('refreshed', 0)}/"
            f"{live_refresh.get('attempted', 0)} "
            "tokens fetched for display only"
        )

        if live_refresh.get("error"):
            print(
                "- Live price error: "
                f"{live_refresh['error']}"
            )

    if closed_trades:
        print("")
        print("Recent closed")

        for trade in closed_trades[-10:]:
            live_text = ""

            if trade.get("live_refreshed"):
                live_text = (
                    f"now {price(trade.get('live_price'))} "
                    f"({safe_float(trade.get('live_entry_multiple')):.2f}x live)"
                    " "
                )

            print(
                "- "
                f"${trade.get('symbol', 'UNKNOWN')} "
                f"{money(safe_float(trade.get('pnl_usd')))} "
                f"({pct(safe_float(trade.get('pnl_pct')))}) "
                f"entry {price(trade.get('entry_price'))} "
                f"exit {price(trade.get('exit_price'))} "
                f"{live_text}"
                f"{trade.get('close_reason', 'closed')} "
                f"at {format_time(trade.get('exit_at'))}"
            )

    if include_open:
        print("")
        print("Open positions")
        print(f"- Positions: {open_summary['open_positions']}")
        print(
            f"- Cash: {open_summary['cash_sol']:.2f} SOL "
            f"({money(open_summary['cash_usd'])})"
        )
        print(f"- Equity: {money(open_summary['open_equity_usd'])}")
        print(
            "- Account equity: "
            f"{money(open_summary['account_equity_usd'])}"
        )
        print(f"- Open PnL: {money(open_summary['open_pnl_usd'])}")

        live_refresh = open_summary.get("live_refresh") or {}

        if (
            live_refresh.get("enabled")
            and not live_refresh.get("closed_enabled")
        ):
            print(
                "- Live refresh: "
                f"{live_refresh.get('refreshed', 0)}/"
                f"{live_refresh.get('attempted', 0)} "
                "open prices fetched for display only"
            )

            if live_refresh.get("error"):
                print(
                    "- Live refresh error: "
                    f"{live_refresh['error']}"
                )

            missing = live_refresh.get("missing") or []

            if missing:
                print(
                    "- Missing live prices: "
                    f"{', '.join(missing)}"
                )

        for position in open_summary["positions"]:
            live_marker = (
                " live"
                if position.get("live_refreshed")
                else ""
            )

            print(
                "- "
                f"${position['symbol']} "
                f"{position['price_multiple']:.2f}x "
                f"PnL {money(position['pnl_usd'])} "
                f"({pct(position['pnl_pct'])}) "
                f"entry {price(position['entry_price'])} "
                f"last {price(position['last_price'])}{live_marker} "
                f"pressure {position['last_pressure']:.1f} "
                f"scaled {pct(position['scaled_out_pct'])}"
            )


def main():

    parser = argparse.ArgumentParser(
        description="Query position performance."
    )
    parser.add_argument(
        "--days",
        type=float,
        help="Look back this many days from now."
    )
    parser.add_argument(
        "--since",
        help="Start date/time, e.g. 2026-05-10 or ISO timestamp."
    )
    parser.add_argument(
        "--until",
        help="End date/time, e.g. 2026-05-11 or ISO timestamp."
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Include current open positions."
    )
    parser.add_argument(
        "--refresh-open",
        action="store_true",
        help=(
            "Fetch live prices for open positions before reporting. "
            "This does not update the position state file."
        )
    )
    parser.add_argument(
        "--refresh-live",
        action="store_true",
        help=(
            "Fetch live prices for selected closed trades and open "
            "positions before reporting. This does not update the "
            "position state file."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON."
    )

    args = parser.parse_args()

    now = datetime.now(
        timezone.utc
    ).timestamp()

    since = parse_date(args.since)
    until = parse_date(
        args.until,
        end_of_day=True
    )

    if args.days is not None:
        since = now - args.days * 86400

    state = load_state()
    open_positions = state["open"]
    live_refresh = None

    closed_trades = select_closed_trades(
        state["closed"],
        since,
        until
    )

    if args.refresh_open or args.refresh_live:
        open_positions, closed_trades, live_refresh = asyncio.run(
            refresh_trade_prices(
                open_positions,
                closed_trades,
                refresh_open=True,
                refresh_closed=args.refresh_live
            )
        )

    sol_usd = POSITION_SOL_USD

    if args.open or args.refresh_open or args.refresh_live:
        sol_feed = SolUsdPriceFeed()
        sol_usd = asyncio.run(
            sol_feed.get_price()
        )

    closed_summary = summarize_closed(
        closed_trades
    )
    open_summary = summarize_open(
        open_positions,
        safe_float(state.get("cash_sol")),
        live_refresh=live_refresh,
        sol_usd=sol_usd
    )

    result = {
        "window": {
            "since": since,
            "until": until
        },
        "closed": closed_summary,
        "open": open_summary,
        "live_refresh": live_refresh
    }

    if args.json:
        print(
            json.dumps(
                result,
                indent=2,
                sort_keys=True
            )
        )
        return

    print_report(
        closed_summary,
        closed_trades,
        open_summary,
        since,
        until,
        args.open or args.refresh_open or args.refresh_live,
        live_refresh=live_refresh
    )


if __name__ == "__main__":
    main()
