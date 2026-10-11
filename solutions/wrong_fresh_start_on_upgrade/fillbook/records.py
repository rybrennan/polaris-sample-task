"""Parses exchange records into Fill and Settlement objects. No I/O here."""

import datetime as dt
from decimal import Decimal
from typing import NamedTuple, Union

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


class Fill(NamedTuple):
    """One executed fill, normalized to integer cents."""
    fill_id: str
    ticker: str
    side: str      # contract received: "yes" | "no"
    action: str    # verb on the order ticket; says nothing about open/close
    count: int
    price: int     # cents, price of `side`
    fee: int       # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    """One settlement row. A higher revision supersedes a lower one."""
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
    """An export is malformed, incomplete or self-contradictory."""


def parse_ts(value: Union[str, int, float]) -> dt.datetime:
    """Parses an ISO 8601 UTC string or epoch milliseconds."""
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    return dt.datetime.strptime(value.replace("Z", "+0000"), TS_FORMAT)


def dollars_to_cents(value: str) -> int:
    """Converts a decimal dollar string ("0.37") to exact integer cents."""
    cents = Decimal(value) * 100
    if cents != cents.to_integral_value():
        raise ExportError("sub-cent dollar amount: %r" % value)
    return int(cents)


def from_rest(rec: dict) -> Fill:
    """Parses a REST v2 fill. The fill's price is the price of its side."""
    side = rec["side"]
    price = rec["yes_price"] if side == "yes" else rec["no_price"]
    return Fill(rec["fill_id"], rec["ticker"], side, rec["action"],
                int(rec["count"]), int(price), int(rec["fee"]),
                parse_ts(rec["created_time"]))


def from_v1(msg: dict) -> Fill:
    """Parses a v1 wire-format fill (WebSocket and archived REST pages)."""
    return Fill(msg["fill_id"], msg["market_ticker"], msg["side"],
                msg["action"], int(msg["quantity"]),
                dollars_to_cents(msg["price"]), dollars_to_cents(msg["fee"]),
                parse_ts(msg["ts"]))


def settlement(r: dict) -> Settlement:
    """Parses a settlement row."""
    return Settlement(
        r["ticker"], int(r.get("revision", 1)), r["market_result"],
        parse_ts(r["settled_time"]), r["yes_count"], r["no_count"],
        r["yes_total_cost"], r["no_total_cost"], r["fee_cost"],
        r["settlement_value"], r["revenue"])
