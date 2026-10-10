"""Positions and realized P&L per market, in integer cents.

Every fill is a purchase of `side` at `price` (a sell of YES is a purchase of
NO at 100 - price, and the exchange reports it that way). YES/NO pairs are
netted immediately for $1.00 each, so after any sequence of fills the number of
pairs netted is min(total YES acquired, total NO acquired).
"""

from collections import defaultdict
from typing import Dict, Iterable

from fillbook.loader import Fill, Settlement


class Position:
    def __init__(self):
        self.acquired = {"yes": 0, "no": 0}   # contracts acquired over life
        self.cost = {"yes": 0, "no": 0}       # cents paid per side
        self.fees = 0

    def apply(self, f: Fill) -> None:
        self.acquired[f.side] += f.count
        self.cost[f.side] += f.count * f.price
        self.fees += f.fee

    @property
    def pairs(self) -> int:
        return min(self.acquired["yes"], self.acquired["no"])

    @property
    def qty(self) -> Dict[str, int]:
        """Holdings after pair netting."""
        p = self.pairs
        return {"yes": self.acquired["yes"] - p, "no": self.acquired["no"] - p}

    def cash_flow(self) -> int:
        """Net cash moved by fills and pair netting (negative = money in)."""
        return 100 * self.pairs - self.cost["yes"] - self.cost["no"] - self.fees

    def payout(self, s: Settlement) -> int:
        v = s.yes_value()
        q = self.qty
        return q["yes"] * v + q["no"] * (100 - v)

    def realized(self, s: Settlement) -> int:
        return self.cash_flow() + self.payout(s)


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        book[f.ticker].apply(f)
    return dict(book)
