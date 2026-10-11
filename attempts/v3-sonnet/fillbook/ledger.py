"""Positions and realized P&L per market (integer cents).

Every fill is an acquisition of `side` contracts at the price of `side`; the
ticket verb (`action`) only says which book side the order was placed on.
Whenever the account holds both YES and NO, the pairs are netted at once and
credited $1.00 each.
"""

import datetime as dt
import re
from collections import defaultdict
from typing import Dict, Iterable, Optional

from fillbook.loader import Fill, Settlement

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_EVENT = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)")


def event_date(ticker: str) -> Optional[str]:
    m = _EVENT.search(ticker)
    if m and m.group(2) in _MONTHS:
        try:
            return dt.date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


def settlement_value(s: Settlement) -> Optional[int]:
    if s.value is not None:
        return s.value
    return {"yes": 100, "no": 0}.get(s.result)


class Position:
    def __init__(self):
        self.qty = {"yes": 0, "no": 0}   # contracts acquired
        self.cost = 0
        self.fees = 0

    def apply(self, f: Fill) -> None:
        self.qty[f.side] += f.count
        self.cost += f.count * f.price
        self.fees += f.fee

    @property
    def pairs(self) -> int:
        return min(self.qty["yes"], self.qty["no"])

    def cash_flow(self) -> int:
        """Cash moved so far: pair credits minus cost and fees."""
        return 100 * self.pairs - self.cost - self.fees

    def payout(self, s: Settlement) -> int:
        v = settlement_value(s)
        if v is None:
            return s.revenue
        return (self.qty["yes"] - self.pairs) * v + (self.qty["no"] - self.pairs) * (100 - v)

    def realized(self, s: Settlement) -> int:
        return self.payout(s) + self.cash_flow()


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        if f.status == "busted":
            continue
        book[f.ticker].apply(f)
    return dict(book)


def cash_moved(fills: Iterable[Fill], since: dt.datetime) -> int:
    """Cash moved by live fills (cost, fees, pair credits) at or after `since`."""
    total = 0
    held: Dict[str, Dict[str, int]] = defaultdict(lambda: {"yes": 0, "no": 0})
    for f in sorted((f for f in fills if f.status != "busted"), key=lambda f: (f.ts, f.fill_id)):
        h = held[f.ticker]
        before = min(h["yes"], h["no"])
        h[f.side] += f.count
        credit = 100 * (min(h["yes"], h["no"]) - before)
        if f.ts >= since:
            total += credit - f.count * f.price - f.fee
    return total
