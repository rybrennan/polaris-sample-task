"""Read an account export directory into Fill and Settlement objects."""

import datetime as dt
import json
import os
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


class Fill(NamedTuple):
    fill_id: str
    ticker: str
    side: str      # contract received: "yes" | "no"
    action: str    # verb on the order ticket; says nothing about open or close
    count: int
    price: int     # cents, price of `side`
    fee: int       # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    ticker: str
    result: str
    settled_time: dt.datetime
    yes_count: int
    no_count: int
    yes_total_cost: int
    no_total_cost: int
    fee_cost: int
    settlement_value: int
    revenue: int


class ExportError(Exception):
    pass


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    return dt.datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def dollars_to_cents(value: str) -> int:
    cents = Decimal(value) * 100
    if cents != cents.to_integral_value():
        raise ExportError("sub-cent dollar amount: %r" % value)
    return int(cents)


def from_rest(rec: dict) -> Fill:
    side = rec["side"]
    price = rec["yes_price"] if side == "yes" else rec["no_price"]
    return Fill(rec["fill_id"], rec["ticker"], side, rec["action"], int(rec["count"]),
                int(price), int(rec["fee"]), parse_ts(rec["created_time"]))


def from_ws(msg: dict) -> Fill:
    return Fill(msg["fill_id"], msg["market_ticker"], msg["side"], msg["action"],
                int(msg["quantity"]), dollars_to_cents(msg["price"]),
                dollars_to_cents(msg["fee"]), parse_ts(msg["ts"]))


def load_rest_fills(data_dir: str) -> Tuple[List[Fill], Set[str]]:
    """All REST fills, and the ids of those marked busted."""
    out: List[Fill] = []
    busted: Set[str] = set()
    seen_cursors = set()
    cursor: Optional[str] = "index"
    while cursor is not None:
        if cursor in seen_cursors:
            raise ExportError("cursor loop at page %s" % cursor)
        seen_cursors.add(cursor)
        path = os.path.join(data_dir, "fills", cursor + ".json")
        if not os.path.exists(path):
            raise ExportError("missing page %s; export is incomplete" % cursor)
        with open(path) as fh:
            page = json.load(fh)
        version = page.get("api_version", 2)
        if version not in (1, 2):
            raise ExportError("page %s: unknown api_version %r" % (cursor, version))
        for r in page["fills"]:
            f = from_ws(r) if version == 1 else from_rest(r)
            if r.get("status", "executed") == "busted":
                busted.add(f.fill_id)
            out.append(f)
        cursor = page.get("cursor")
    return out, busted


def load_ws_fills(data_dir: str) -> Tuple[List[Fill], Set[str]]:
    """All WebSocket fills, and the ids named by fill_bust messages."""
    path = os.path.join(data_dir, "ws_fills.jsonl")
    out: List[Fill] = []
    busted: Set[str] = set()
    if not os.path.exists(path):
        return out, busted
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            msg = json.loads(line)
            if msg.get("type") == "fill":
                out.append(from_ws(msg["msg"]))
            elif msg.get("type") == "fill_bust":
                busted.add(msg["msg"]["fill_id"])
    return out, busted


def load_fills(data_dir: str) -> List[Fill]:
    """REST and WebSocket fills merged on fill_id, busted fills removed.

    A fill_id seen in both sources must describe the same fill.
    """
    rest, rest_busted = load_rest_fills(data_dir)
    ws, ws_busted = load_ws_fills(data_dir)
    by_id: Dict[str, Fill] = {}
    for f in rest + ws:
        prev = by_id.get(f.fill_id)
        if prev is None:
            by_id[f.fill_id] = f
        elif prev._replace(ts=f.ts) != f or abs((prev.ts - f.ts).total_seconds()) >= 1:
            raise ExportError("fill %s differs between sources: %r vs %r" % (f.fill_id, prev, f))
    for fid in rest_busted | ws_busted:
        by_id.pop(fid, None)
    return sorted(by_id.values(), key=lambda f: (f.ts, f.fill_id))


def load_settlements(data_dir: str) -> List[Settlement]:
    with open(os.path.join(data_dir, "settlements.json")) as fh:
        rows = json.load(fh)["settlements"]
    return [Settlement(r["ticker"], r["market_result"], parse_ts(r["settled_time"]),
                       r["yes_count"], r["no_count"], r["yes_total_cost"], r["no_total_cost"],
                       r["fee_cost"], r["settlement_value"], r["revenue"]) for r in rows]


def load_account(data_dir: str) -> dict:
    with open(os.path.join(data_dir, "account.json")) as fh:
        return json.load(fh)
