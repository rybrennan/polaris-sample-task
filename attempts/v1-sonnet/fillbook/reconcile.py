"""Consistency checks between fills, settlements and the account statement."""

import json
import os
from collections import defaultdict
from typing import List

from fillbook.ledger import build_positions
from fillbook.loader import load_all_fill_records, load_fills, load_settlements


def reconcile(data_dir: str) -> List[str]:
    out: List[str] = []

    # fills present in both sources must agree
    seen = {}
    for f in load_all_fill_records(data_dir):
        prev = seen.get(f.fill_id)
        if prev is None:
            seen[f.fill_id] = f
        elif prev[:7] != f[:7] or abs((prev.ts - f.ts).total_seconds()) >= 1:
            out.append("fill %s differs between REST and WebSocket: %s vs %s"
                       % (f.fill_id, tuple(prev[1:7]), tuple(f[1:7])))

    book = build_positions(load_fills(data_dir))

    rows = defaultdict(list)
    for s in load_settlements(data_dir):
        rows[s.ticker].append(s)
    for t, lst in sorted(rows.items()):
        if len(lst) > 1:
            out.append("%s: %d settlement rows" % (t, len(lst)))

    cash = 0
    for t, pos in sorted(book.items()):
        cash += pos.cash_flow()
        if t not in rows:
            continue
        s = rows[t][0]
        cash += s.revenue
        checks = [
            ("yes_count", s.yes_count, pos.acquired["yes"]),
            ("no_count", s.no_count, pos.acquired["no"]),
            ("yes_total_cost", s.yes_total_cost, pos.cost["yes"]),
            ("no_total_cost", s.no_total_cost, pos.cost["no"]),
            ("fee_cost", s.fee_cost, pos.fees),
            ("revenue", s.revenue, pos.revenue(s.result)),
        ]
        for name, row, calc in checks:
            if row != calc:
                out.append("%s: settlement %s=%s but fills give %s" % (t, name, row, calc))
    for t, lst in sorted(rows.items()):
        if t not in book:
            s = lst[0]
            cash += s.revenue
            if any((s.yes_count, s.no_count, s.yes_total_cost, s.no_total_cost,
                    s.fee_cost, s.revenue)):
                out.append("%s: settlement row but no fills" % t)

    with open(os.path.join(data_dir, "account.json")) as fh:
        acct = json.load(fh)
    expected = acct["starting_cash_cents"] + acct["net_deposits_cents"] + cash
    if expected != acct["ending_cash_cents"]:
        out.append("cash: start %d + deposits %d + trading %d = %d, statement ends at %d (off by %d)"
                   % (acct["starting_cash_cents"], acct["net_deposits_cents"], cash, expected,
                      acct["ending_cash_cents"], acct["ending_cash_cents"] - expected))
    return out
