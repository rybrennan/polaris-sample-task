"""Build the P&L report."""

import datetime as dt

from fillbook.ledger import Position, build_positions
from fillbook.loader import load_fills, load_settlements


def build_report(data_dir: str) -> dict:
    book = build_positions(load_fills(data_dir))
    settled = {s.ticker: s for s in load_settlements(data_dir)}
    markets, open_, by_day = {}, {}, {}
    for ticker, s in settled.items():  # settled markets with no fills still report 0
        book.setdefault(ticker, Position())
    for ticker, pos in sorted(book.items()):
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


def event_date(ticker: str) -> str:
    """Event date from the ticker's YYMMMDD segment, as YYYY-MM-DD."""
    for seg in ticker.split("-"):
        try:
            return dt.datetime.strptime(seg[:7], "%y%b%d").date().isoformat()
        except ValueError:
            continue
    raise ValueError("no event date in ticker %r" % ticker)


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
