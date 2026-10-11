"""Check that the store is internally consistent."""

import datetime as dt
import sqlite3
from typing import List

from fillbook.ledger import Position, build_positions, cash_moved, settlement_value
from fillbook.store import latest_settlements, load_state


def reconcile(conn: sqlite3.Connection) -> List[str]:
    st = load_state(conn)
    out: List[str] = list(st.problems)
    book = build_positions(st.fills)
    settled = latest_settlements(st)

    # 1. fills vs the latest settlement row of every settled market
    for ticker in sorted(st.settlements):
        rows = st.settlements[ticker]
        s = settled[ticker]
        revs = {r.revision for r in rows}
        missing = [r for r in range(1, s.revision + 1) if r not in revs]
        if missing:
            out.append("%s: settlement revision(s) %s missing (latest is %d)"
                       % (ticker, ",".join(map(str, missing)), s.revision))
        same = [r for r in rows if r.revision == s.revision]
        if any(r != s for r in same):
            out.append("%s: conflicting settlement rows for revision %d" % (ticker, s.revision))
        v = settlement_value(s)
        if s.result not in ("yes", "no", "void"):
            out.append("%s: unknown market_result %r" % (ticker, s.result))
        elif s.value is not None and s.result in ("yes", "no") and v != (100 if s.result == "yes" else 0):
            out.append("%s: settlement_value %s does not match result %s" % (ticker, s.value, s.result))
        elif s.value is not None and not 0 <= s.value <= 100:
            out.append("%s: settlement_value %s out of range" % (ticker, s.value))
        pos = book.get(ticker) or Position()
        checks = [
            ("yes_count", s.yes_count, pos.qty["yes"]),
            ("no_count", s.no_count, pos.qty["no"]),
            ("yes_total_cost", s.yes_total_cost,
             sum(f.count * f.price for f in st.fills if f.ticker == ticker and f.status != "busted" and f.side == "yes")),
            ("no_total_cost", s.no_total_cost,
             sum(f.count * f.price for f in st.fills if f.ticker == ticker and f.status != "busted" and f.side == "no")),
            ("fee_cost", s.fee_cost, pos.fees),
            ("revenue", s.revenue, pos.payout(s)),
        ]
        for name, row, mine in checks:
            if row is not None and row != mine:
                out.append("%s: %s settlement says %s, fills give %s" % (ticker, name, row, mine))

    # 2. cash statements: unbroken run of days, each starting where the last ended
    stm = st.statements
    for a, b in zip(stm, stm[1:]):
        try:
            da = dt.date.fromisoformat(a["date"])
            db = dt.date.fromisoformat(b["date"])
            gap = (db - da).days
        except ValueError:
            gap = 1
        if gap != 1:
            out.append("statements: %s is followed by %s (%d day(s) missing)"
                       % (a["date"], b["date"], gap - 1))
        if b["starting_cash_cents"] != a["ending_cash_cents"]:
            out.append("statements: %s starts at %d but %s ended at %d"
                       % (b["date"], b["starting_cash_cents"], a["date"], a["ending_cash_cents"]))

    # 3. start + deposits + trading cash == end
    if stm:
        try:
            since = dt.datetime.strptime(stm[0]["date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            since = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        trading = cash_moved(st.fills, since) + sum(s.revenue for s in settled.values())
        expected = stm[0]["starting_cash_cents"] + sum(a["net_deposits_cents"] for a in stm) + trading
        if expected != stm[-1]["ending_cash_cents"]:
            out.append("cash: start %d + deposits %d + trading %d = %d, but last statement ends at %d (off by %d)"
                       % (stm[0]["starting_cash_cents"], sum(a["net_deposits_cents"] for a in stm), trading,
                          expected, stm[-1]["ending_cash_cents"], stm[-1]["ending_cash_cents"] - expected))
    return out
