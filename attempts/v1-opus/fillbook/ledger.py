"""Positions and realized P&L per market, in integer cents.

Per docs/exchange_api.md every fill is a purchase of `side` (the contract the
account received) at that side's price; `action` is only the ticket's verb.
Holding YES and NO together nets immediately into $1.00 cash per pair.
"""

from collections import defaultdict
from typing import Dict, Iterable

from fillbook.loader import Fill, Settlement


class Position:
    def __init__(self):
        self.acquired = {"yes": 0, "no": 0}   # contracts received over the market's life
        self.cost = {"yes": 0, "no": 0}       # cents paid for them
        self.fees = 0
        self.held = {"yes": 0, "no": 0}       # after pair netting
        self.pairs = 0                        # pairs netted into cash

    def apply(self, f: Fill) -> None:
        self.acquired[f.side] += f.count
        self.cost[f.side] += f.count * f.price
        self.fees += f.fee
        self.held[f.side] += f.count
        n = min(self.held["yes"], self.held["no"])
        if n:
            self.held["yes"] -= n
            self.held["no"] -= n
            self.pairs += n

    def cash_flow(self) -> int:
        """Net cash moved by fills and pair netting (excludes settlement revenue)."""
        return 100 * self.pairs - self.cost["yes"] - self.cost["no"] - self.fees

    def revenue(self, result: str) -> int:
        return 100 * self.held[result]

    def realized(self, s: Settlement) -> int:
        return self.cash_flow() + self.revenue(s.result)


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        book[f.ticker].apply(f)
    return dict(book)
