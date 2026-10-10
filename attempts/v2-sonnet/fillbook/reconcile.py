"""Check an export for internal consistency."""

import json
import os
from typing import List

from fillbook.ledger import Position, build_positions, settlement_value
from fillbook.loader import load_fills, load_settlements, parse_ts
from fillbook.report import event_date


def reconcile(data_dir: str) -> List[str]:
    problems: List[str] = []
    book = build_positions(load_fills(data_dir))
    rows = load_settlements(data_dir)
    settled = {}
    for s in rows:
        if s.ticker in settled:
            problems.append("%s: duplicate settlement row" % s.ticker)
        settled[s.ticker] = s

    trading = 0
    for ticker in sorted(set(book) | set(settled)):
        pos = book.get(ticker) or Position()
        s = settled.get(ticker)
        if s is None:
            trading += pos.cash_flow()
            continue
        if s.result not in ("yes", "no", "void"):
            problems.append("%s: unknown market_result %r" % (ticker, s.result))
            continue
        expect_value = {"yes": 100, "no": 0}.get(s.result)
        if expect_value is not None and s.settlement_value is not None \
                and s.settlement_value != expect_value:
            problems.append("%s: settlement_value %s but result is %s"
                            % (ticker, s.settlement_value, s.result))
        if s.result == "void" and s.settlement_value is None:
            problems.append("%s: void market has no settlement_value" % ticker)
            continue
        revenue = pos.revenue(s)
        checks = [
            ("yes_count", s.yes_count, pos.qty["yes"]),
            ("no_count", s.no_count, pos.qty["no"]),
            ("yes_total_cost", s.yes_total_cost, pos.cost["yes"]),
            ("no_total_cost", s.no_total_cost, pos.cost["no"]),
            ("fee_cost", s.fee_cost, pos.fees),
            ("revenue", s.revenue, revenue),
        ]
        for name, row, fills in checks:
            if row != fills:
                problems.append("%s: %s settlement row says %s, fills say %s"
                                % (ticker, name, row, fills))
        trading += pos.cash_flow() + revenue

    acct_path = os.path.join(data_dir, "account.json")
    try:
        with open(acct_path) as fh:
            acct = json.load(fh)
    except (OSError, ValueError) as e:
        problems.append("account.json unreadable: %s" % e)
        return problems
    expected = acct["starting_cash_cents"] + acct["net_deposits_cents"] + trading
    if expected != acct["ending_cash_cents"]:
        problems.append(
            "cash: start %d + deposits %d + trading %d = %d, statement ending cash is %d (off by %d)"
            % (acct["starting_cash_cents"], acct["net_deposits_cents"], trading, expected,
               acct["ending_cash_cents"], acct["ending_cash_cents"] - expected))

    # A market whose event is well past the export time should have settled.
    try:
        exported = parse_ts(acct["exported_at"]).date()
        for ticker in sorted(book):
            if ticker not in settled:
                lag = (exported - __import__("datetime").date.fromisoformat(event_date(ticker))).days
                if lag >= 2:
                    problems.append("%s: no settlement row although event was %d days before export"
                                    % (ticker, lag))
    except (KeyError, ValueError):
        pass
    return problems
