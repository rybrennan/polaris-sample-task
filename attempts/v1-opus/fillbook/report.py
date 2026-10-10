"""Build the P&L report."""

import datetime as dt
import re
from typing import Optional

from fillbook.ledger import Position, build_positions
from fillbook.loader import load_fills, load_settlements

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_DATE_SEG = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})$")


def event_date(ticker: str) -> Optional[dt.date]:
    """Event date from the ticker segment like `26SEP04` (YYMMMDD)."""
    for seg in ticker.split("-"):
        m = _DATE_SEG.match(seg.upper())
        if m and m.group(2) in _MONTHS:
            try:
                return dt.date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
            except ValueError:
                continue
    return None


def build_report(data_dir: str) -> dict:
    book = build_positions(load_fills(data_dir))
    settled = {s.ticker: s for s in load_settlements(data_dir)}
    markets, open_, by_day = {}, {}, {}
    for ticker in sorted(set(book) | set(settled)):
        pos = book.get(ticker) or Position()
        s = settled.get(ticker)
        if s is None:
            open_[ticker] = pos.cash_flow()
            continue
        pnl = pos.realized(s)
        markets[ticker] = pnl
        d = event_date(ticker) or s.settled_time.date()
        day = d.isoformat()
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
