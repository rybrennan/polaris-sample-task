"""Check that the store is internally consistent (see TASK.md / README.md).

1. Every settled market: the live fills agree with the latest settlement row on
   every field the row carries (counts, costs, fees, revenue at the row's
   settlement value, result vs settlement value, no fills after settlement),
   and the market's revisions run 1..n with none missing.
2. The cash statements form an unbroken run of days, each starting where the
   previous one ended.
3. first starting cash + all net deposits + all cash moved by trading
   == last ending cash, to the cent.
"""

import datetime as dt
import sqlite3
from typing import List

from fillbook.ledger import Position, build_positions, latest_settlements
from fillbook.loader import event_date
from fillbook.report import dollars, stored_fills, stored_settlement_rows

EXPECTED_VALUE = {"yes": 100, "no": 0}
# an open market this many days past its event date is missing its settlement row
OVERDUE_DAYS = 3


def check(conn: sqlite3.Connection) -> List[str]:
    problems: List[str] = []
    fills = list(stored_fills(conn))
    book = build_positions(fills)
    rows = list(stored_settlement_rows(conn))
    settled = latest_settlements(rows)

    # 1. settled markets vs their latest settlement row
    revisions = {}
    for s in rows:
        revisions.setdefault(s.ticker, []).append(s.revision)
    for ticker, revs in sorted(revisions.items()):
        want = list(range(1, max(revs) + 1))
        if sorted(revs) != want:
            missing = sorted(set(want) - set(revs))
            problems.append("settlement %s: revision(s) %s missing (have %s)"
                            % (ticker, ",".join(map(str, missing)), ",".join(map(str, sorted(revs)))))

    for ticker, s in sorted(settled.items()):
        pos = book.get(ticker) or Position()
        tag = "settlement %s rev %d" % (ticker, s.revision)
        if s.result in EXPECTED_VALUE:
            if s.settlement_value != EXPECTED_VALUE[s.result]:
                problems.append("%s: market_result %s but settlement_value %d"
                                % (tag, s.result, s.settlement_value))
        elif s.result == "void":
            if not 0 <= s.settlement_value <= 100:
                problems.append("%s: void settlement_value %d outside 0..100"
                                % (tag, s.settlement_value))
        else:
            problems.append("%s: unknown market_result %r" % (tag, s.result))
        for field, got in (("yes_count", pos.count["yes"]), ("no_count", pos.count["no"]),
                           ("yes_total_cost", pos.cost["yes"]),
                           ("no_total_cost", pos.cost["no"]),
                           ("fee_cost", pos.fees), ("revenue", pos.revenue(s))):
            row = getattr(s, field)
            if got != row:
                problems.append("%s: %s is %d on the row, fills give %d"
                                % (tag, field, row, got))
        late = [f for f in fills if f.ticker == ticker and f.ts > s.settled_time]
        if late:
            problems.append("%s: %d live fill(s) after settled_time %s (first %s)"
                            % (tag, len(late), s.settled_time.isoformat(), late[0].fill_id))

    # 2. statements form an unbroken chain of days
    stm = conn.execute("SELECT export_date, starting_cash, ending_cash, net_deposits "
                       "FROM statements ORDER BY export_date").fetchall()
    if not stm:
        problems.append("statements: none ingested")
        return problems
    for prev, cur in zip(stm, stm[1:]):
        d0 = dt.date.fromisoformat(prev[0])
        d1 = dt.date.fromisoformat(cur[0])
        gap = (d1 - d0).days
        if gap > 1:
            missing = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(1, gap)]
            problems.append("statements: export(s) missing for %s" % ", ".join(missing))
        if cur[1] != prev[2]:
            problems.append("statement %s: starting cash %s but %s ended at %s"
                            % (cur[0], dollars(cur[1]), prev[0], dollars(prev[2])))

    # A market settles within a day or so of its event date, and its row lands
    # in that day's export. A losing market's row carries no revenue, so losing
    # it leaves the cash identity intact; catch it by age instead.
    last_day = dt.date.fromisoformat(stm[-1][0])
    for ticker, pos in sorted(book.items()):
        ev = event_date(ticker)
        if ticker in settled or ev is None:
            continue
        age = (last_day - dt.date.fromisoformat(ev)).days
        if age >= OVERDUE_DAYS:
            problems.append("settlement %s: no settlement row, but its event date %s is %d days "
                            "before the last export (%s)" % (ticker, ev, age, stm[-1][0]))

    # 3. cash identity over the whole run
    trading = sum(p.cash_flow() for p in book.values())
    trading += sum(book[t].revenue(s) for t, s in settled.items() if t in book)
    deposits = sum(r[3] for r in stm)
    expected = stm[0][1] + deposits + trading
    if expected != stm[-1][2]:
        problems.append("cash: start %s + deposits %s + trading %s = %s, but %s ended at %s "
                        "(off by %s)" % (dollars(stm[0][1]), dollars(deposits), dollars(trading),
                                         dollars(expected), stm[-1][0], dollars(stm[-1][2]),
                                         dollars(stm[-1][2] - expected)))
    return problems
