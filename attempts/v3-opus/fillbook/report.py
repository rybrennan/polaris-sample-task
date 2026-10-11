"""Build the P&L report from the store."""

import sqlite3

from fillbook.ledger import build_positions, latest_settlements
from fillbook.loader import Fill, Settlement, event_date, parse_ts


def stored_fills(conn: sqlite3.Connection, include_busted: bool = False):
    for row in conn.execute(
            "SELECT fill_id, order_id, ticker, side, action, count, price, fee, ts, busted "
            "FROM fills ORDER BY ts, fill_id"):
        f = Fill(*row[:8], parse_ts(row[8]), bool(row[9]))
        if include_busted or not f.busted:
            yield f


def stored_settlement_rows(conn: sqlite3.Connection):
    for row in conn.execute(
            "SELECT ticker, revision, market_result, settled_time, yes_count, no_count, "
            "yes_total_cost, no_total_cost, fee_cost, settlement_value, revenue "
            "FROM settlements ORDER BY ticker, revision"):
        yield Settlement(row[0], row[1], row[2], parse_ts(row[3]), *row[4:])


def stored_settlements(conn: sqlite3.Connection):
    """Latest settlement row per market."""
    return latest_settlements(stored_settlement_rows(conn))


def build_report(conn: sqlite3.Connection) -> dict:
    book = build_positions(stored_fills(conn))
    settled = stored_settlements(conn)
    markets, open_, by_day = {}, {}, {}
    for ticker in sorted(set(book) | set(settled)):
        pos = book.get(ticker)
        s = settled.get(ticker)
        if s is None:
            open_[ticker] = pos.cash_flow()
            continue
        pnl = pos.realized(s) if pos is not None else 0
        markets[ticker] = pnl
        day = event_date(ticker) or s.settled_time.date().isoformat()
        by_day[day] = by_day.get(day, 0) + pnl
    return {
        "markets": markets,
        "open": open_,
        "total_cents": sum(markets.values()),
        "by_day": dict(sorted(by_day.items())),
    }


def format_report(rep: dict) -> str:
    lines = ["%-12s %12s" % ("day", "realized")]
    for day, cents in rep["by_day"].items():
        lines.append("%-12s %12s" % (day, dollars(cents)))
    lines.append("-" * 25)
    lines.append("%-12s %12s" % ("TOTAL", dollars(rep["total_cents"])))
    lines.append("%d settled markets, %d open" % (len(rep["markets"]), len(rep["open"])))
    return "\n".join(lines)


def dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return "%s$%d.%02d" % (sign, abs(cents) // 100, abs(cents) % 100)
