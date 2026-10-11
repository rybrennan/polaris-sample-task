"""Parse exchange records into Fill and Settlement objects. No I/O here."""

import datetime as dt
from decimal import Decimal
from typing import NamedTuple


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
    revision: int
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


def from_v1(msg: dict) -> Fill:
    return Fill(msg["fill_id"], msg["market_ticker"], msg["side"], msg["action"],
                int(msg["quantity"]), dollars_to_cents(msg["price"]),
                dollars_to_cents(msg["fee"]), parse_ts(msg["ts"]))


def settlement(r: dict) -> Settlement:
    return Settlement(r["ticker"], int(r.get("revision", 1)), r["market_result"],
                      parse_ts(r["settled_time"]), r["yes_count"], r["no_count"],
                      r["yes_total_cost"], r["no_total_cost"], r["fee_cost"],
                      r["settlement_value"], r["revenue"])
