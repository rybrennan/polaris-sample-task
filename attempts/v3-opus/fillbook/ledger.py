"""Positions and realized P&L per market, in integer cents.

Per docs/exchange_api.md every fill is a purchase of the contract in `side`
(an order to sell YES executes as a purchase of NO), so `action` never changes
the cash math. Holding YES and NO in one market nets pairs at $1.00 each.
Busted fills never happened and are skipped.
"""

from collections import defaultdict
from typing import Dict, Iterable, Optional

from fillbook.loader import Fill, Settlement


class Position:
    def __init__(self):
        self.count = {"yes": 0, "no": 0}   # contracts acquired over the market's life
        self.cost = {"yes": 0, "no": 0}    # cents paid for them
        self.fees = 0
        self.fills = 0

    def apply(self, f: Fill) -> None:
        if f.busted:
            return
        self.count[f.side] += f.count
        self.cost[f.side] += f.count * f.price
        self.fees += f.fee
        self.fills += 1

    @property
    def pairs(self) -> int:
        return min(self.count["yes"], self.count["no"])

    def held(self, side: str) -> int:
        return self.count[side] - self.pairs

    def cash_flow(self) -> int:
        """Cash moved by fills and pair netting so far (negative = money in)."""
        return 100 * self.pairs - self.cost["yes"] - self.cost["no"] - self.fees

    def revenue(self, s: Settlement) -> int:
        return (self.held("yes") * s.settlement_value
                + self.held("no") * (100 - s.settlement_value))

    def realized(self, s: Settlement) -> int:
        return self.cash_flow() + self.revenue(s)


def build_positions(fills: Iterable[Fill]) -> Dict[str, Position]:
    book: Dict[str, Position] = defaultdict(Position)
    for f in fills:
        if not f.busted:
            book[f.ticker].apply(f)
    return dict(book)


def latest_settlements(rows: Iterable[Settlement]) -> Dict[str, Settlement]:
    out: Dict[str, Settlement] = {}
    for s in rows:
        cur: Optional[Settlement] = out.get(s.ticker)
        if cur is None or s.revision > cur.revision:
            out[s.ticker] = s
    return out
