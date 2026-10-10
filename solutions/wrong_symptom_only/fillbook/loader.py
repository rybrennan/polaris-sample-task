"""Read an account export directory into Fill and Settlement objects."""

import datetime as dt
import json
import os
from typing import List, NamedTuple, Optional

MAX_PAGES = 5  # guard against cursor loops


class Fill(NamedTuple):
    fill_id: str
    ticker: str
    side: str      # "yes" | "no"
    action: str    # "buy" | "sell"
    count: int
    price: float   # cents
    fee: float     # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    ticker: str
    result: str
    settled_time: dt.datetime
    revenue: int


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    return dt.datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def normalize(rec: dict) -> Fill:
    return Fill(
        fill_id=rec["fill_id"],
        ticker=rec.get("ticker") or rec.get("market_ticker"),
        side=rec["side"],
        action=rec["action"],
        count=int(rec.get("count", 0)),
        price=float(rec["no_price"] if rec["side"] == "no" and "no_price" in rec
                    else rec.get("yes_price", rec.get("price"))),
        fee=float(rec.get("fee", 0)),
        ts=parse_ts(rec.get("created_time") or rec.get("ts")),
    )


def load_rest_fills(data_dir: str) -> List[dict]:
    out = []
    cursor: Optional[str] = "index"
    pages = 0
    while cursor and pages < MAX_PAGES:
        with open(os.path.join(data_dir, "fills", cursor + ".json")) as fh:
            page = json.load(fh)
        out.extend(page["fills"])
        cursor = page.get("cursor")
        pages += 1
    return out


def load_ws_fills(data_dir: str) -> List[dict]:
    path = os.path.join(data_dir, "ws_fills.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            msg = json.loads(line)
            if msg.get("type") == "fill":
                out.append(msg["msg"])
    return out


def load_fills(data_dir: str) -> List[Fill]:
    raw = load_rest_fills(data_dir) + load_ws_fills(data_dir)
    return sorted((normalize(r) for r in raw), key=lambda f: f.ts)


def load_settlements(data_dir: str) -> List[Settlement]:
    with open(os.path.join(data_dir, "settlements.json")) as fh:
        rows = json.load(fh)["settlements"]
    return [Settlement(r["ticker"], r["market_result"], parse_ts(r["settled_time"]), r["revenue"])
            for r in rows]
