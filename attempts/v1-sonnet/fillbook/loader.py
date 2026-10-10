"""Read an account export directory into Fill and Settlement objects."""

import datetime as dt
import json
import os
import re
from decimal import Decimal
from typing import List, NamedTuple, Optional


class Fill(NamedTuple):
    fill_id: str
    ticker: str
    side: str      # "yes" | "no"
    action: str    # "buy" | "sell"
    count: int
    price: int     # cents, price of `side` (the contract received)
    fee: int       # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    ticker: str
    result: str
    settled_time: dt.datetime
    revenue: int
    yes_count: Optional[int] = None
    no_count: Optional[int] = None
    yes_total_cost: Optional[int] = None
    no_total_cost: Optional[int] = None
    fee_cost: Optional[int] = None


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    m = _ISO.match(str(value).strip())
    if not m:
        raise ValueError("bad timestamp %r" % (value,))
    y, mo, d, h, mi, sec, frac, tz = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    t = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0), micro,
                    tzinfo=dt.timezone.utc)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        t -= sign * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return t


def dollars_to_cents(value) -> int:
    return int((Decimal(str(value)) * 100).to_integral_value())


def normalize(rec: dict) -> Fill:
    side = rec["side"]
    if "yes_price" in rec or "no_price" in rec:      # REST
        price = rec["yes_price"] if side == "yes" else rec["no_price"]
        if price is None:
            other = rec["no_price"] if side == "yes" else rec["yes_price"]
            price = 100 - other
        price, fee = int(price), int(rec.get("fee", 0))
        count = int(rec["count"])
        ts = rec["created_time"]
    else:                                              # WebSocket v1, dollars
        price, fee = dollars_to_cents(rec["price"]), dollars_to_cents(rec.get("fee", "0"))
        count = int(rec["quantity"])
        ts = rec["ts"]
    return Fill(
        fill_id=rec["fill_id"],
        ticker=rec.get("ticker") or rec.get("market_ticker"),
        side=side,
        action=rec["action"],
        count=count,
        price=price,
        fee=fee,
        ts=parse_ts(ts),
    )


def load_rest_fills(data_dir: str) -> List[dict]:
    out = []
    cursor: Optional[str] = "index"
    seen = set()
    while cursor and cursor not in seen:
        seen.add(cursor)
        with open(os.path.join(data_dir, "fills", cursor + ".json")) as fh:
            page = json.load(fh)
        out.extend(page["fills"])
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


def load_fills(data_dir: str) -> List[Fill]:
    """REST and WebSocket fills merged, one per fill_id (REST wins)."""
    by_id = {}
    for r in load_ws_fills(data_dir) + load_rest_fills(data_dir):
        f = normalize(r)
        by_id[f.fill_id] = f
    return sorted(by_id.values(), key=lambda f: (f.ts, f.fill_id))


def load_all_fill_records(data_dir: str) -> List[Fill]:
    """Every fill record from both sources, duplicates kept (for reconcile)."""
    return [normalize(r) for r in load_rest_fills(data_dir) + load_ws_fills(data_dir)]


def load_settlements(data_dir: str) -> List[Settlement]:
    with open(os.path.join(data_dir, "settlements.json")) as fh:
        rows = json.load(fh)["settlements"]
    return [Settlement(r["ticker"], r["market_result"], parse_ts(r["settled_time"]), r["revenue"],
                       r.get("yes_count"), r.get("no_count"), r.get("yes_total_cost"),
                       r.get("no_total_cost"), r.get("fee_cost"))
            for r in rows]
