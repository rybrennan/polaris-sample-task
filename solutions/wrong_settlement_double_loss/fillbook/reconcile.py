"""Check an export against itself and against the cash statement.

Two independent paths must agree:
  1. per settled market, the fills against the settlement row;
  2. the cash identity: start + net deposits + all trading cash = end.
"""

from typing import List

from fillbook.ledger import Position, build_positions, settlement_realized
from fillbook.loader import load_account, load_fills, load_settlements
from fillbook.report import dollars


def check(data_dir: str) -> List[str]:
    book = build_positions(load_fills(data_dir))
    settlements = load_settlements(data_dir)
    acct = load_account(data_dir)
    problems = []

    trading_cash = 0
    settled = set()
    for s in settlements:
        settled.add(s.ticker)
        pos = book.get(s.ticker, Position())
        fields = [
            ("yes_count", pos.acquired["yes"], s.yes_count),
            ("no_count", pos.acquired["no"], s.no_count),
            ("yes_total_cost", pos.cost["yes"], s.yes_total_cost),
            ("no_total_cost", pos.cost["no"], s.no_total_cost),
            ("fee_cost", pos.fees, s.fee_cost),
            ("revenue", pos.payout(s.settlement_value), s.revenue),
        ]
        fixed = {"yes": 100, "no": 0}.get(s.result)
        if fixed is not None and s.settlement_value != fixed:
            problems.append("%s: settlement_value %d does not match result %s"
                            % (s.ticker, s.settlement_value, s.result))
        for name, from_fills, from_row in fields:
            if from_fills != from_row:
                problems.append("%s: %s is %d from fills, %d on the settlement row"
                                % (s.ticker, name, from_fills, from_row))
        trading_cash += settlement_realized(s)
    for ticker, pos in book.items():
        if ticker not in settled:
            trading_cash += pos.cash_flow()

    expected = acct["starting_cash_cents"] + acct["net_deposits_cents"] + trading_cash
    if expected != acct["ending_cash_cents"]:
        problems.append("cash: records imply ending cash %s, statement says %s (off by %s)"
                        % (dollars(expected), dollars(acct["ending_cash_cents"]),
                           dollars(acct["ending_cash_cents"] - expected)))
    return problems
