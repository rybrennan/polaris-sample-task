"""Build the P&L report from the store."""

import sqlite3

from fillbook.ledger import Position, build_positions, event_date
from fillbook.store import latest_settlements, load_state


def build_report(conn: sqlite3.Connection) -> dict:
    st = load_state(conn)
    book = build_positions(st.fills)
    settled = latest_settlements(st)
    markets, open_, by_day = {}, {}, {}
    for ticker in sorted(set(book) | set(settled)):
        s = settled.get(ticker)
        pos = book.get(ticker)
        if s is None:
            open_[ticker] = pos.cash_flow()
            continue
        if pos is None:
            pos = Position()
        pnl = pos.realized(s)
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
