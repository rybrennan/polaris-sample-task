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
    side: str      # contract received: "yes" | "no"
    action: str    # order-ticket verb (informational; every fill buys `side`)
    count: int
    price: int     # cents, price of `side`
    fee: int       # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    ticker: str
    result: str
    settled_time: dt.datetime
    revenue: int
    settlement_value: Optional[int] = None   # cents paid per YES contract
    yes_count: int = 0
    no_count: int = 0
    yes_total_cost: int = 0
    no_total_cost: int = 0
    fee_cost: int = 0


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    m = re.match(r"^(.*?)\.(\d+)(.*)$", v)
    if m:  # normalise fractional seconds for 3.9's fromisoformat
        v = m.group(1) + "." + (m.group(2) + "000000")[:6] + m.group(3)
    t = dt.datetime.fromisoformat(v)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def dollars_to_cents(value) -> int:
    c = Decimal(str(value)) * 100
    r = c.to_integral_value()
    if r != c:
        raise ValueError("not a whole number of cents: %r" % (value,))
    return int(r)


def normalize(rec: dict) -> Fill:
    side = rec["side"]
    if "market_ticker" in rec or "quantity" in rec or "yes_price" not in rec:
        # v1 wire format: price/fee in dollars, price is for `side`
        ticker = rec.get("market_ticker") or rec.get("ticker")
        count = int(rec["quantity"])
        price = dollars_to_cents(rec["price"])
        fee = dollars_to_cents(rec.get("fee", 0))
        ts = rec.get("ts") if rec.get("ts") is not None else rec.get("created_time")
    else:
        ticker = rec["ticker"]
        count = int(rec["count"])
        price = int(rec["yes_price"] if side == "yes" else rec["no_price"])
        fee = int(rec.get("fee", 0))
        ts = rec.get("created_time")
    return Fill(rec["fill_id"], ticker, side, rec["action"], count, price, fee, parse_ts(ts))


def load_rest_fills(data_dir: str) -> List[dict]:
    out = []
    cursor: Optional[str] = "index"
    seen = set()
    while cursor and cursor not in seen:  # guard against cursor loops
        seen.add(cursor)
        with open(os.path.join(data_dir, "fills", cursor + ".json")) as fh:
            page = json.load(fh)
        out.extend(page["fills"])
        cursor = page.get("cursor")
    return out


def load_ws_messages(data_dir: str):
    """Return (fill msgs, busted fill ids) from the WebSocket capture."""
    path = os.path.join(data_dir, "ws_fills.jsonl")
    fills, busted = [], set()
    if not os.path.exists(path):
        return fills, busted
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("type") == "fill":
                fills.append(msg["msg"])
            elif msg.get("type") == "fill_bust":
                busted.add(msg["msg"]["fill_id"] if "msg" in msg else msg["fill_id"])
    return fills, busted


def load_ws_fills(data_dir: str) -> List[dict]:
    return load_ws_messages(data_dir)[0]


def load_fills(data_dir: str) -> List[Fill]:
    """Executed fills, de-duplicated by fill_id across REST and WebSocket."""
    ws, ws_busted = load_ws_messages(data_dir)
    busted = set(ws_busted)
    by_id = {}
    for r in load_rest_fills(data_dir) + ws:
        if r.get("status") == "busted":
            busted.add(r["fill_id"])
        by_id.setdefault(r["fill_id"], r)
    fills = [normalize(r) for fid, r in by_id.items() if fid not in busted]
    return sorted(fills, key=lambda f: (f.ts, f.fill_id))


def load_settlements(data_dir: str) -> List[Settlement]:
    with open(os.path.join(data_dir, "settlements.json")) as fh:
        rows = json.load(fh)["settlements"]
    return [Settlement(
        r["ticker"], r["market_result"], parse_ts(r["settled_time"]), r["revenue"],
        r.get("settlement_value"), r.get("yes_count", 0), r.get("no_count", 0),
        r.get("yes_total_cost", 0), r.get("no_total_cost", 0), r.get("fee_cost", 0))
        for r in rows]
