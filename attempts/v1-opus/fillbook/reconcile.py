"""Check an export for internal consistency.

1. For every settled market, the fills must agree with its settlement row on
   every field the row carries (counts, costs, fees, revenue, result, and the
   settlement time must not precede the market's fills).
2. starting cash + net deposits + all cash moved by trading == ending cash.
"""

import datetime as dt
import json
import os
from typing import List

from fillbook.ledger import Position, build_positions
from fillbook.loader import load_fills_checked, load_settlement_rows, parse_ts, to_settlement, strict_int
from fillbook.report import dollars, event_date

# An open market whose event day ended this long before the export is
# reported as probably missing its settlement row.
SETTLE_GRACE = dt.timedelta(days=2)


def reconcile(data_dir: str) -> List[str]:
    problems: List[str] = []
    fills, conflicts = load_fills_checked(data_dir)
    problems.extend(conflicts)
    book = build_positions(fills)
    last_fill = {}
    for f in fills:
        last_fill[f.ticker] = max(last_fill.get(f.ticker, f.ts), f.ts)

    settled = {}
    for row in load_settlement_rows(data_dir):
        try:
            s = to_settlement(row)
        except (KeyError, ValueError) as e:
            problems.append("settlement row %r: unreadable (%s)" % (row.get("ticker"), e))
            continue
        if s.ticker in settled:
            problems.append("%s: duplicate settlement row" % s.ticker)
            continue
        settled[s.ticker] = (s, row)

    trading = 0
    for ticker in sorted(set(book) | set(settled)):
        pos = book.get(ticker) or Position()
        if ticker not in settled:
            trading += pos.cash_flow()
            continue
        s, row = settled[ticker]
        if s.result not in ("yes", "no"):
            problems.append("%s: market_result %r is not yes/no" % (ticker, s.result))
            trading += pos.cash_flow() + s.revenue
            continue
        expect = {
            "yes_count": pos.acquired["yes"],
            "no_count": pos.acquired["no"],
            "yes_total_cost": pos.cost["yes"],
            "no_total_cost": pos.cost["no"],
            "fee_cost": pos.fees,
            "revenue": pos.revenue(s.result),
        }
        for field, want in expect.items():
            if field not in row:
                problems.append("%s: settlement row lacks %s (fills give %d)" % (ticker, field, want))
                continue
            got = strict_int(row[field], field)
            if got != want:
                problems.append("%s: %s is %d in settlement row, fills give %d"
                                % (ticker, field, got, want))
        if ticker not in book:
            problems.append("%s: settlement row has no fills" % ticker)
        elif last_fill[ticker] > s.settled_time:
            problems.append("%s: fill at %s after settled_time %s"
                            % (ticker, last_fill[ticker].isoformat(), s.settled_time.isoformat()))
        trading += pos.realized(s)

    acct_path = os.path.join(data_dir, "account.json")
    if not os.path.exists(acct_path):
        problems.append("account.json missing: cannot check cash")
        return problems
    with open(acct_path) as fh:
        acct = json.load(fh)
    try:
        start = strict_int(acct["starting_cash_cents"], "starting_cash_cents")
        end = strict_int(acct["ending_cash_cents"], "ending_cash_cents")
        dep = strict_int(acct.get("net_deposits_cents", 0), "net_deposits_cents")
    except (KeyError, ValueError) as e:
        problems.append("account.json unreadable: %s" % e)
        return problems
    implied = start + dep + trading
    if implied != end:
        problems.append("cash: start %s + net deposits %s + trading %s = %s, statement ends at %s (off by %s)"
                        % (dollars(start), dollars(dep), dollars(trading), dollars(implied),
                           dollars(end), dollars(end - implied)))

    exported = acct.get("exported_at")
    if exported:
        exported_at = parse_ts(exported)
        for ticker in sorted(set(book) - set(settled)):
            d = event_date(ticker)
            if d is None:
                continue
            day_end = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
            if day_end + SETTLE_GRACE < exported_at:
                problems.append("%s: no settlement row though event date %s ended more than %d days "
                                "before export" % (ticker, d.isoformat(), SETTLE_GRACE.days))
    return problems
