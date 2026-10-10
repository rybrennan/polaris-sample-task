"""Read an account export directory into Fill and Settlement objects.

Field semantics follow docs/exchange_api.md:
- `side` is the contract the account received; every fill is a purchase of
  `side` at that side's price, whatever `action` says.
- REST prices/fees are integer cents; WebSocket `price` (of `side`) and `fee`
  are decimal dollar strings.
- A fill may appear in REST, WS, or both; `fill_id` identifies it.
"""

import datetime as dt
import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, NamedTuple, Optional, Tuple


class Fill(NamedTuple):
    fill_id: str
    ticker: str
    side: str      # contract received: "yes" | "no"
    action: str    # verb on the order ticket: "buy" | "sell" (informational only)
    count: int
    price: int     # cents paid per contract of `side`
    fee: int       # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    ticker: str
    result: str
    settled_time: dt.datetime
    revenue: int
    yes_count: int = 0
    no_count: int = 0
    yes_total_cost: int = 0
    no_total_cost: int = 0
    fee_cost: int = 0


_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(milliseconds=value)
    m = _TS_RE.match(str(value).strip())
    if not m:
        raise ValueError("bad timestamp: %r" % (value,))
    y, mo, d, h, mi, s, frac, tz = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    if tz in (None, "Z"):
        tzinfo = dt.timezone.utc
    else:
        sign = -1 if tz[0] == "-" else 1
        digits = tz[1:].replace(":", "")
        tzinfo = dt.timezone(sign * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    out = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), micro, tzinfo=tzinfo)
    return out.astimezone(dt.timezone.utc)


def dollars_to_cents(value) -> int:
    try:
        cents = Decimal(str(value).strip()) * 100
    except InvalidOperation:
        raise ValueError("bad dollar amount: %r" % (value,))
    if cents != cents.to_integral_value():
        raise ValueError("dollar amount not whole cents: %r" % (value,))
    return int(cents)


def strict_int(value, what="value") -> int:
    if isinstance(value, bool):
        raise ValueError("bad %s: %r" % (what, value))
    if isinstance(value, int):
        return value
    d = Decimal(str(value).strip())
    if d != d.to_integral_value():
        raise ValueError("bad %s: %r" % (what, value))
    return int(d)


def normalize_rest(rec: dict) -> Fill:
    side = rec["side"]
    if side not in ("yes", "no"):
        raise ValueError("bad side: %r" % (side,))
    if "yes_price" in rec and rec["yes_price"] is not None:
        yes_price = strict_int(rec["yes_price"], "yes_price")
    else:
        yes_price = 100 - strict_int(rec["no_price"], "no_price")
    price = yes_price if side == "yes" else 100 - yes_price
    return Fill(
        fill_id=rec["fill_id"],
        ticker=rec["ticker"],
        side=side,
        action=rec.get("action", ""),
        count=strict_int(rec["count"], "count"),
        price=price,
        fee=strict_int(rec.get("fee") or 0, "fee"),
        ts=parse_ts(rec["created_time"]),
    )


def normalize_ws(msg: dict) -> Fill:
    side = msg["side"]
    if side not in ("yes", "no"):
        raise ValueError("bad side: %r" % (side,))
    return Fill(
        fill_id=msg["fill_id"],
        ticker=msg["market_ticker"],
        side=side,
        action=msg.get("action", ""),
        count=strict_int(msg["quantity"], "quantity"),
        price=dollars_to_cents(msg["price"]),
        fee=dollars_to_cents(msg.get("fee") or "0"),
        ts=parse_ts(msg["ts"]),
    )


def load_rest_fills(data_dir: str) -> List[dict]:
    out = []
    cursor: Optional[str] = "index"
    seen = set()
    while cursor:
        if cursor in seen:  # cursor loop: every page already read
            break
        seen.add(cursor)
        path = os.path.join(data_dir, "fills", cursor + ".json")
        with open(path) as fh:
            page = json.load(fh)
        out.extend(page.get("fills") or [])
        cursor = page.get("cursor")
    return out


def load_ws_fills(data_dir: str) -> List[dict]:
    path = os.path.join(data_dir, "ws_fills.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("type") == "fill":
                out.append(msg["msg"])
    return out


def _same_fill(a: Fill, b: Fill) -> bool:
    # Timestamps can differ slightly between channels; compare the economics.
    return (a.ticker, a.side, a.count, a.price, a.fee) == (b.ticker, b.side, b.count, b.price, b.fee)


def load_fills_checked(data_dir: str) -> Tuple[List[Fill], List[str]]:
    """Merged, de-duplicated fills plus a list of source conflicts."""
    problems: List[str] = []
    merged: Dict[str, Fill] = {}
    for rec in load_rest_fills(data_dir):
        f = normalize_rest(rec)
        prev = merged.get(f.fill_id)
        if prev is not None and not _same_fill(prev, f):
            problems.append("fill %s: conflicting REST records" % f.fill_id)
        merged.setdefault(f.fill_id, f)
    for msg in load_ws_fills(data_dir):
        f = normalize_ws(msg)
        prev = merged.get(f.fill_id)
        if prev is None:
            merged[f.fill_id] = f
        elif not _same_fill(prev, f):
            problems.append("fill %s: REST and WebSocket records disagree (%s vs %s)"
                            % (f.fill_id, _desc(prev), _desc(f)))
    fills = sorted(merged.values(), key=lambda f: (f.ts, f.fill_id))
    return fills, problems


def _desc(f: Fill) -> str:
    return "%s %s x%d @%d fee %d" % (f.ticker, f.side, f.count, f.price, f.fee)


def load_fills(data_dir: str) -> List[Fill]:
    return load_fills_checked(data_dir)[0]


def load_settlement_rows(data_dir: str) -> List[dict]:
    path = os.path.join(data_dir, "settlements.json")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return json.load(fh).get("settlements") or []


def to_settlement(r: dict) -> Settlement:
    return Settlement(
        ticker=r["ticker"],
        result=r["market_result"],
        settled_time=parse_ts(r["settled_time"]),
        revenue=strict_int(r.get("revenue") or 0, "revenue"),
        yes_count=strict_int(r.get("yes_count") or 0, "yes_count"),
        no_count=strict_int(r.get("no_count") or 0, "no_count"),
        yes_total_cost=strict_int(r.get("yes_total_cost") or 0, "yes_total_cost"),
        no_total_cost=strict_int(r.get("no_total_cost") or 0, "no_total_cost"),
        fee_cost=strict_int(r.get("fee_cost") or 0, "fee_cost"),
    )


def load_settlements(data_dir: str) -> List[Settlement]:
    return [to_settlement(r) for r in load_settlement_rows(data_dir)]
