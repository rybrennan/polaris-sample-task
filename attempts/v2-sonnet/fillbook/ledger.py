"""Positions and realized P&L per market.

Every fill acquires `side` at that side's price (a "sell" on the ticket is a
purchase of the opposite contract). YES/NO pairs net to $1.00 cash each as soon
as they form; what is left settles at the settlement value.
"""

from collections import defaultdict
from typing import Dict, Iterable, Optional

from fillbook.loader import Fill, Settlement


class Position:
    def __init__(self):
        self.qty = {"yes": 0, "no": 0}
        self.cost = {"yes": 0, "no": 0}
        self.fees = 0

    def apply(self, f: Fill) -> None:
        self.qty[f.side] += f.count
        self.cost[f.side] += f.count * f.price
        self.fees += f.fee

    @property
    def pairs(self) -> int:
        return min(self.qty["yes"], self.qty["no"])

    def cash_flow(self) -> int:
        """Net cash moved by trading so far (fills, fees, pair netting)."""
        return 100 * self.pairs - self.cost["yes"] - self.cost["no"] - self.fees

    def revenue(self, s: Settlement) -> int:
        yes_value = settlement_value(s)
        p = self.pairs
        return (self.qty["yes"] - p) * yes_value + (self.qty["no"] - p) * (100 - yes_value)

    def realized(self, s: Settlement) -> int:
        return self.cash_flow() + self.revenue(s)


def settlement_value(s: Settlement) -> int:
    if s.settlement_value is not None:
        return int(s.settlement_value)
    return {"yes": 100, "no": 0}[s.result]


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        book[f.ticker].apply(f)
    return dict(book)
