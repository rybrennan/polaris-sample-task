"""Checks the store against itself and against the cash statements.

Three independent checks must hold:
  1. per settled market, the fills agree with the latest settlement row;
  2. the statements form an unbroken daily chain;
  3. first starting cash + all net deposits + all trading cash
     = last ending cash.
"""

import datetime as dt
from typing import Dict, List

from fillbook.book import Book
from fillbook.ledger import Position, build_positions, settlement_realized
from fillbook.records import Settlement
from fillbook.report import dollars


def _check_market(ticker: str, s: Settlement, pos: Position) -> List[str]:
    problems = []
    fixed = {"yes": 100, "no": 0}.get(s.result)
    if fixed is not None and s.settlement_value != fixed:
        problems.append("%s: settlement_value %d does not match result %s"
                        % (ticker, s.settlement_value, s.result))
    for name, from_fills, from_row in [
        ("yes_count", pos.acquired["yes"], s.yes_count),
        ("no_count", pos.acquired["no"], s.no_count),
        ("yes_total_cost", pos.cost["yes"], s.yes_total_cost),
        ("no_total_cost", pos.cost["no"], s.no_total_cost),
        ("fee_cost", pos.fees, s.fee_cost),
        ("revenue", pos.payout(s.settlement_value), s.revenue),
    ]:
        if from_fills != from_row:
            problems.append(
                "%s: %s is %d from fills, %d on settlement revision %d"
                % (ticker, name, from_fills, from_row, s.revision))
    return problems


def _check_chain(stmts: List[Dict]) -> List[str]:
    problems = []
    for prev, cur in zip(stmts, stmts[1:]):
        gap = (dt.date.fromisoformat(cur["export_date"])
               - dt.date.fromisoformat(prev["export_date"])).days
        if gap != 1:
            problems.append("statements: %d day(s) missing between %s and %s"
                            % (gap - 1, prev["export_date"],
                               cur["export_date"]))
        if cur["starting_cash_cents"] != prev["ending_cash_cents"]:
            problems.append(
                "statements: %s starts at %s but %s ended at %s"
                % (cur["export_date"], dollars(cur["starting_cash_cents"]),
                   prev["export_date"], dollars(prev["ending_cash_cents"])))
    return problems


def check(book: Book) -> List[str]:
    """Returns one line per discrepancy; an empty list means consistent."""
    positions = build_positions(book.live_fills())
    problems = []

    trading_cash = 0
    for ticker, s in sorted(book.settlements.items()):
        problems += _check_market(ticker, s, positions.get(ticker, Position()))
        trading_cash += settlement_realized(s)
    for ticker, pos in positions.items():
        if ticker not in book.settlements:
            trading_cash += pos.cash_flow()

    stmts = sorted(book.statements, key=lambda a: a["export_date"])
    if not stmts:
        return problems + ["no statements in the store"]
    problems += _check_chain(stmts)

    deposits = sum(a["net_deposits_cents"] for a in stmts)
    expected = stmts[0]["starting_cash_cents"] + deposits + trading_cash
    ending = stmts[-1]["ending_cash_cents"]
    if expected != ending:
        problems.append(
            "cash: records imply ending cash %s, last statement says %s "
            "(off by %s)" % (dollars(expected), dollars(ending),
                             dollars(ending - expected)))
    return problems
