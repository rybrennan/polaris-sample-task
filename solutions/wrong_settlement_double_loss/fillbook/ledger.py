"""Positions and realized P&L per market.

Every fill is a purchase of `side` at that side's price, whatever `action` says.
A YES and a NO in one market net into $1.00 cash as soon as both are held.
At settlement a YES pays `settlement_value` and a NO pays the rest of $1.00.
"""

from collections import defaultdict
from typing import Dict, Iterable

from fillbook.loader import Fill, Settlement


class Position:
    def __init__(self):
        self.held = {"yes": 0, "no": 0}
        self.acquired = {"yes": 0, "no": 0}
        self.cost = {"yes": 0, "no": 0}
        self.fees = 0
        self.netted = 0

    def apply(self, f: Fill) -> None:
        self.held[f.side] += f.count
        self.acquired[f.side] += f.count
        self.cost[f.side] += f.count * f.price
        self.fees += f.fee
        pairs = min(self.held["yes"], self.held["no"])
        self.held["yes"] -= pairs
        self.held["no"] -= pairs
        self.netted += pairs

    def cash_flow(self) -> int:
        """Cash moved so far: netting credits minus costs and fees."""
        return 100 * self.netted - self.cost["yes"] - self.cost["no"] - self.fees

    def payout(self, yes_value: int) -> int:
        """Paid at settlement when one YES is worth `yes_value` cents (NO: the rest)."""
        return self.held["yes"] * yes_value + self.held["no"] * (100 - yes_value)

    def realized(self, s: Settlement) -> int:
        return self.payout(s.settlement_value) + self.cash_flow()


def settlement_realized(s: Settlement) -> int:
    """Realized P&L from the settlement row alone, including early netting."""
    return s.revenue - s.yes_total_cost - s.no_total_cost - s.fee_cost


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        book[f.ticker].apply(f)
    return dict(book)
