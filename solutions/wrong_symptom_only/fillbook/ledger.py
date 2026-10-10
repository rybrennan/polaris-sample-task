"""Positions and realized P&L per market."""

from collections import defaultdict
from typing import Dict, Iterable

from fillbook.loader import Fill, Settlement


class Position:
    def __init__(self):
        self.qty = {"yes": 0, "no": 0}
        self.cost = 0.0
        self.proceeds = 0.0
        self.fees = 0.0

    def apply(self, f: Fill) -> None:
        # Per the exchange docs, every fill is a purchase of `side`; pairs net to $1.
        self.qty[f.side] += f.count
        self.cost += f.count * f.price
        self.fees += f.fee
        pairs = min(self.qty["yes"], self.qty["no"])
        self.qty["yes"] -= pairs
        self.qty["no"] -= pairs
        self.proceeds += 100 * pairs

    def cash_flow(self) -> float:
        return self.proceeds - self.cost - self.fees

    def realized(self, s: Settlement) -> float:
        payout = 100 * max(self.qty[s.result], 0)
        return payout + self.cash_flow()


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        book[f.ticker].apply(f)
    return dict(book)
