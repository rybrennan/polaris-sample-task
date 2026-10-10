"""Positions and realized P&L per market.

Every fill is an acquisition of `side` at that side's price (see
docs/exchange_api.md, "One book per market"); `action` is only the ticket verb.
Whenever an account holds YES and NO together the exchange nets the pairs and
credits $1.00 each, so only one side is ever held.
"""

import datetime as dt
from collections import defaultdict
from typing import Dict, Iterable

from fillbook.loader import Fill, Settlement

_MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def event_date(ticker: str) -> dt.date:
    """Event date from the ticker segment like 26SEP04."""
    for seg in ticker.split("-"):
        if len(seg) == 7 and seg[:2].isdigit() and seg[2:5] in _MONTHS and seg[5:].isdigit():
            return dt.date(2000 + int(seg[:2]), _MONTHS[seg[2:5]], int(seg[5:]))
    raise ValueError("no event date in ticker %r" % ticker)


class Position:
    def __init__(self):
        self.acquired = {"yes": 0, "no": 0}   # contracts received over the life
        self.cost = {"yes": 0, "no": 0}       # cents paid
        self.fees = 0
        self.fill_ids = set()

    def apply(self, f: Fill) -> None:
        self.acquired[f.side] += f.count
        self.cost[f.side] += f.count * f.price
        self.fees += f.fee
        self.fill_ids.add(f.fill_id)

    @property
    def pairs(self) -> int:
        return min(self.acquired["yes"], self.acquired["no"])

    @property
    def held(self) -> Dict[str, int]:
        p = self.pairs
        return {k: v - p for k, v in self.acquired.items()}

    @property
    def netting_credit(self) -> int:
        return 100 * self.pairs

    def revenue(self, result: str) -> int:
        return 100 * self.held[result]

    def cash_flow(self) -> int:
        """Cash moved by fills, fees and pair netting (no settlement)."""
        return self.netting_credit - self.cost["yes"] - self.cost["no"] - self.fees

    def realized(self, s: Settlement) -> int:
        return self.cash_flow() + self.revenue(s.result)


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        book[f.ticker].apply(f)
    return dict(book)
