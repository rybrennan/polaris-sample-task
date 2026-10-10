"""Check an export for internal consistency.

1. For every settled market, the fills must agree with its settlement row on
   every field the row carries.
2. starting cash + net deposits + all cash moved by trading == ending cash.
"""

import datetime as dt
import json
import os
from typing import Dict, List

from fillbook.ledger import Position, build_positions
from fillbook.loader import (event_date, load_raw, load_settlement_rows, merge_fills,
                             parse_ts, to_settlement)
from fillbook.report import dollars

# A market with fills and no settlement row is accepted as still open only if
# the export was taken within this many days after its event date.
OPEN_GRACE_DAYS = 2


def reconcile(data_dir: str) -> List[str]:
    out: List[str] = []
    raw = load_raw(data_dir)
    out.extend(raw.problems)
    fills = merge_fills(raw, out)
    book = build_positions(fills)
    last_fill: Dict[str, dt.datetime] = {}
    for f in fills:
        last_fill[f.ticker] = max(last_fill.get(f.ticker, f.ts), f.ts)

    with open(os.path.join(data_dir, "account.json")) as fh:
        acct = json.load(fh)
    exported = parse_ts(acct["exported_at"]) if acct.get("exported_at") else None

    rows = load_settlement_rows(data_dir)
    settled = {}
    for r in rows:
        t = r.get("ticker")
        if t in settled:
            out.append("%s: duplicate settlement row" % t)
            continue
        settled[t] = r

    trading = 0
    for ticker in sorted(set(book) | set(settled)):
        pos = book.get(ticker) or Position()
        r = settled.get(ticker)
        if r is None:
            trading += pos.cash_flow()
            ev = event_date(ticker)
            if exported is not None and ev is not None and \
                    (exported.date() - ev).days > OPEN_GRACE_DAYS:
                out.append("%s: has fills but no settlement row (event %s, exported %s)"
                           % (ticker, ev.isoformat(), exported.date().isoformat()))
            continue
        out.extend(_check_row(ticker, pos, r, last_fill.get(ticker), exported))
        try:
            trading += pos.realized(to_settlement(r))
        except (ValueError, TypeError, KeyError) as e:
            out.append("%s: cannot value settlement: %s" % (ticker, e))

    start = int(acct["starting_cash_cents"])
    deps = int(acct["net_deposits_cents"])
    end = int(acct["ending_cash_cents"])
    expected = start + deps + trading
    if expected != end:
        out.append("cash: start %s + net deposits %s + trading %s = %s, statement says %s (off by %s)"
                   % (dollars(start), dollars(deps), dollars(trading), dollars(expected),
                      dollars(end), dollars(end - expected)))
    return out


def _check_row(ticker, pos: Position, r: dict, last_fill, exported) -> List[str]:
    out: List[str] = []

    def cmp(field, computed):
        if field not in r:
            return
        if r[field] != computed:
            out.append("%s: %s is %r in settlement row, fills give %r"
                       % (ticker, field, r[field], computed))

    if not pos.acquired["yes"] and not pos.acquired["no"] and not pos.fees:
        out.append("%s: settlement row but no fills" % ticker)
    cmp("yes_count", pos.acquired["yes"])
    cmp("no_count", pos.acquired["no"])
    cmp("yes_total_cost", pos.cost["yes"])
    cmp("no_total_cost", pos.cost["no"])
    cmp("fee_cost", pos.fees)

    result = r.get("market_result")
    sv = r.get("settlement_value")
    if result not in ("yes", "no", "void"):
        out.append("%s: market_result %r is not yes/no/void" % (ticker, result))
    elif result in ("yes", "no"):
        want = 100 if result == "yes" else 0
        if sv is not None and sv != want:
            out.append("%s: settlement_value %r contradicts market_result %r"
                       % (ticker, sv, result))
    elif sv is None or not isinstance(sv, int) or not 0 <= sv <= 100:
        out.append("%s: void settlement has bad settlement_value %r" % (ticker, sv))

    if "revenue" in r and result in ("yes", "no", "void"):
        try:
            cmp("revenue", pos.payout(to_settlement(r)))
        except (ValueError, TypeError, KeyError):
            pass

    if "settled_time" in r:
        try:
            st = parse_ts(r["settled_time"])
        except (ValueError, TypeError):
            out.append("%s: bad settled_time %r" % (ticker, r["settled_time"]))
        else:
            if last_fill is not None and last_fill > st:
                out.append("%s: fill at %s is after settled_time %s"
                           % (ticker, last_fill.isoformat(), r["settled_time"]))
            if exported is not None and st > exported:
                out.append("%s: settled_time %s is after export time" % (ticker, r["settled_time"]))
            ev = event_date(ticker)
            if ev is not None and st.date() < ev:
                out.append("%s: settled_time %s is before event date %s"
                           % (ticker, r["settled_time"], ev.isoformat()))
    return out
