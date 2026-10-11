"""Build the P&L report from the store."""

import datetime as dt
import sqlite3

from fillbook.ledger import build_positions
from fillbook.loader import Fill, Settlement


def stored_fills(conn: sqlite3.Connection):
    for row in conn.execute("SELECT fill_id, ticker, side, action, count, price, fee, ts FROM fills"):
        yield Fill(*row[:7], dt.datetime.fromisoformat(row[7]))


def stored_settlements(conn: sqlite3.Connection):
    out = {}
    for ticker, result, settled, revenue in conn.execute(
            "SELECT ticker, result, settled_time, revenue FROM settlements ORDER BY rowid"):
        out.setdefault(ticker, Settlement(ticker, result, dt.datetime.fromisoformat(settled), revenue))
    return out


def build_report(conn: sqlite3.Connection) -> dict:
    book = build_positions(stored_fills(conn))
    settled = stored_settlements(conn)
    markets, open_, by_day = {}, {}, {}
    for ticker, pos in sorted(book.items()):
        s = settled.get(ticker)
        if s is None:
            open_[ticker] = int(round(pos.cash_flow()))
            continue
        pnl = int(round(pos.realized(s)))
        markets[ticker] = pnl
        day = s.settled_time.date().isoformat()
        by_day[day] = by_day.get(day, 0) + pnl
    rows = conn.execute("SELECT starting_cash, ending_cash, net_deposits FROM statements "
                        "ORDER BY export_id").fetchall()
    if rows:
        moved = rows[-1][1] - rows[0][0] - sum(r[2] for r in rows)
        gap = moved - sum(markets.values()) - sum(open_.values())
        if gap:
            markets["ADJUSTMENT"] = gap  # reconcile to the statements
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
