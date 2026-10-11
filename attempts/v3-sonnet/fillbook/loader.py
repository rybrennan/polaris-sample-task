"""Read exchange export files into Fill and Settlement objects.

Everything here is pure parsing: money is integer cents, no floats.
"""

import datetime as dt
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, NamedTuple, Optional


class Fill(NamedTuple):
    fill_id: str
    ticker: str
    side: str      # contract the account received: "yes" | "no"
    action: str    # verb on the order ticket (does not change the economics)
    count: int
    price: int     # cents, price of `side`
    fee: int       # cents
    ts: dt.datetime
    status: str = "executed"


class Settlement(NamedTuple):
    ticker: str
    result: str
    settled_time: dt.datetime
    revenue: int
    revision: int = 1
    value: Optional[int] = None      # settlement_value
    yes_count: Optional[int] = None
    no_count: Optional[int] = None
    yes_total_cost: Optional[int] = None
    no_total_cost: Optional[int] = None
    fee_cost: Optional[int] = None


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2})?$")


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(milliseconds=int(value))
    m = _ISO.match(str(value).strip())
    if not m:
        raise ValueError("bad timestamp %r" % (value,))
    y, mo, d, h, mi, s, frac, tz = m.groups()
    micro = int((frac or "0").ljust(6, "0")[:6])
    out = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=dt.timezone.utc)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        out -= sign * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return out


def dollars_to_cents(value) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _cents(value) -> int:
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def normalize(rec: dict) -> Fill:
    """A REST (v2) fill or a v1 wire-format fill (WebSocket / archived page)."""
    side = rec["side"]
    if "market_ticker" in rec or "quantity" in rec:          # v1 wire format
        ticker = rec.get("market_ticker") or rec.get("ticker")
        count = int(rec["quantity"])
        price = dollars_to_cents(rec["price"])
        fee = dollars_to_cents(rec.get("fee", 0))
        ts = parse_ts(rec.get("ts") if rec.get("ts") is not None else rec["created_time"])
    else:                                                    # v2
        ticker = rec["ticker"]
        count = int(rec["count"])
        yes_p, no_p = rec.get("yes_price"), rec.get("no_price")
        if side == "yes":
            price = _cents(yes_p) if yes_p is not None else 100 - _cents(no_p)
        else:
            price = _cents(no_p) if no_p is not None else 100 - _cents(yes_p)
        fee = _cents(rec.get("fee", 0))
        ts = parse_ts(rec["created_time"])
    return Fill(rec["fill_id"], ticker, side, rec["action"], count, price, fee, ts,
                rec.get("status") or "executed")


def follow_pages(pages: Dict[str, dict]) -> List[dict]:
    """Fill records of a REST page set, following cursors from `index`."""
    out: List[dict] = []
    seen = set()
    cursor: Optional[str] = "index"
    while cursor and cursor not in seen and cursor in pages:
        seen.add(cursor)
        page = pages[cursor]
        out.extend(page.get("fills") or [])
        cursor = page.get("cursor")
    return out


def load_rest_fills(data_dir: str) -> List[dict]:
    pages = {}
    fdir = os.path.join(data_dir, "fills")
    if os.path.isdir(fdir):
        for name in os.listdir(fdir):
            if name.endswith(".json"):
                with open(os.path.join(fdir, name)) as fh:
                    pages[name[:-5]] = json.load(fh)
    return follow_pages(pages)


def parse_ws_line(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def load_ws_messages(data_dir: str) -> List[dict]:
    path = os.path.join(data_dir, "ws_fills.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [m for m in (parse_ws_line(l) for l in fh) if m]


def load_fills(data_dir: str) -> List[Fill]:
    """Distinct fills of one export (REST preferred over WebSocket), busted ones marked."""
    fills: Dict[str, Fill] = {}
    for m in load_ws_messages(data_dir):
        if m.get("type") == "fill":
            f = normalize(m["msg"])
            fills[f.fill_id] = f
    for r in load_rest_fills(data_dir):
        f = normalize(r)
        fills[f.fill_id] = f
    for m in load_ws_messages(data_dir):
        if m.get("type") == "fill_bust" and m["msg"]["fill_id"] in fills:
            fills[m["msg"]["fill_id"]] = fills[m["msg"]["fill_id"]]._replace(status="busted")
    return sorted(fills.values(), key=lambda f: (f.ts, f.fill_id))


def parse_settlement(r: dict) -> Settlement:
    def opt(k):
        return int(r[k]) if r.get(k) is not None else None
    return Settlement(r["ticker"], r["market_result"], parse_ts(r["settled_time"]),
                      int(r.get("revenue", 0)), int(r.get("revision", 1)), opt("settlement_value"),
                      opt("yes_count"), opt("no_count"), opt("yes_total_cost"),
                      opt("no_total_cost"), opt("fee_cost"))


def load_settlements(data_dir: str) -> List[Settlement]:
    with open(os.path.join(data_dir, "settlements.json")) as fh:
        rows = json.load(fh)["settlements"]
    return [parse_settlement(r) for r in rows]


def load_account(data_dir: str) -> dict:
    with open(os.path.join(data_dir, "account.json")) as fh:
        return json.load(fh)
