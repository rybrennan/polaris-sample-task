"""Build the P&L report."""

import datetime as dt
import re

from fillbook.ledger import Position, build_positions
from fillbook.loader import load_fills, load_settlements

EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})-")


def event_date(ticker: str) -> str:
    m = EVENT_DATE.search(ticker)
    if not m:
        raise ValueError("no event date in ticker %s" % ticker)
    return dt.datetime.strptime("".join(m.groups()), "%y%b%d").date().isoformat()


def build_report(data_dir: str) -> dict:
    book = build_positions(load_fills(data_dir))
    settled = {s.ticker: s for s in load_settlements(data_dir)}
    markets, open_, by_day = {}, {}, {}
    # Every market with a settlement row is settled, even if all its fills were busted.
    for ticker in sorted(set(book) | set(settled)):
        pos = book.get(ticker, Position())
        s = settled.get(ticker)
        if s is None:
            open_[ticker] = pos.cash_flow()
            continue
        pnl = pos.realized(s)
        markets[ticker] = pnl
        day = event_date(ticker)
        by_day[day] = by_day.get(day, 0) + pnl
    return {
        "markets": markets,
        "open": open_,
        "total_cents": sum(markets.values()),
        "by_day": dict(sorted(by_day.items())),
    }


def format_report(rep: dict) -> str:
    lines = ["%-12s %12s" % ("event day", "realized")]
    for day, cents in rep["by_day"].items():
        lines.append("%-12s %12s" % (day, dollars(cents)))
    lines.append("-" * 25)
    lines.append("%-12s %12s" % ("TOTAL", dollars(rep["total_cents"])))
    lines.append("%d settled markets, %d open" % (len(rep["markets"]), len(rep["open"])))
    return "\n".join(lines)


def dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return "%s$%d.%02d" % (sign, abs(cents) // 100, abs(cents) % 100)
