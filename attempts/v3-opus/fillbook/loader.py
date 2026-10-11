"""Read daily exports and parse their records (see docs/exchange_api.md).

An export is handled as a list of raw records ``(kind, name, payload)``, the
same shape the store archives in ``raw_records``:

    ("rest_page",   <cursor>,      <page JSON text>)
    ("ws",          <line number>, <one JSONL line>)
    ("settlements", "settlements", <settlements.json text>)
    ("account",     "account",     <account.json text>)

All money is integer cents.
"""

import datetime as dt
import json
import os
import re
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

Record = Tuple[str, str, str]


class Fill(NamedTuple):
    fill_id: str
    order_id: str
    ticker: str
    side: str      # contract the account received: "yes" | "no"
    action: str    # verb on the order ticket; does not change the cash math
    count: int
    price: int     # cents paid per contract of `side`
    fee: int       # cents
    ts: dt.datetime
    busted: bool = False


class Settlement(NamedTuple):
    ticker: str
    revision: int
    result: str            # "yes" | "no" | "void"
    settled_time: dt.datetime
    yes_count: int
    no_count: int
    yes_total_cost: int
    no_total_cost: int
    fee_cost: int
    settlement_value: int  # cents one YES contract pays
    revenue: int


class Export(NamedTuple):
    export_date: str
    exported_at: dt.datetime
    account: dict
    fills: Dict[str, Fill]     # REST and WebSocket fills, by fill_id
    busted: Set[str]           # fill_ids reported busted in this export
    settlements: List[Settlement]


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", s)
    if m:  # normalize fractional seconds to 6 digits for fromisoformat on 3.9
        frac = (m.group(2) or "")[:6].ljust(6, "0")
        s = "%s.%s%s" % (m.group(1), frac, m.group(3))
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def dollars_to_cents(value) -> int:
    cents = Decimal(str(value)) * 100
    if cents != cents.to_integral_value():
        raise ValueError("amount %r is not a whole number of cents" % (value,))
    return int(cents)


def parse_fill_v2(rec: dict) -> Fill:
    side = rec["side"]
    if side not in ("yes", "no"):
        raise ValueError("fill %s: bad side %r" % (rec.get("fill_id"), side))
    price = rec["yes_price"] if side == "yes" else rec["no_price"]
    return Fill(
        fill_id=rec["fill_id"],
        order_id=rec.get("order_id", ""),
        ticker=rec["ticker"],
        side=side,
        action=rec.get("action", ""),
        count=int(rec["count"]),
        price=int(price),
        fee=int(rec.get("fee", 0)),
        ts=parse_ts(rec["created_time"]),
        busted=rec.get("status", "executed") == "busted",
    )


def parse_fill_v1(rec: dict) -> Fill:
    side = rec["side"]
    if side not in ("yes", "no"):
        raise ValueError("fill %s: bad side %r" % (rec.get("fill_id"), side))
    return Fill(
        fill_id=rec["fill_id"],
        order_id=rec.get("order_id", ""),
        ticker=rec["market_ticker"],
        side=side,
        action=rec.get("action", ""),
        count=int(rec["quantity"]),
        price=dollars_to_cents(rec["price"]),
        fee=dollars_to_cents(rec.get("fee", "0")),
        ts=parse_ts(rec["ts"]),
        busted=rec.get("status", "executed") == "busted",
    )


def parse_settlement(r: dict) -> Settlement:
    return Settlement(
        ticker=r["ticker"],
        revision=int(r.get("revision", 1)),
        result=r["market_result"],
        settled_time=parse_ts(r["settled_time"]),
        yes_count=int(r["yes_count"]),
        no_count=int(r["no_count"]),
        yes_total_cost=int(r["yes_total_cost"]),
        no_total_cost=int(r["no_total_cost"]),
        fee_cost=int(r["fee_cost"]),
        settlement_value=int(r["settlement_value"]),
        revenue=int(r["revenue"]),
    )


def read_export_dir(export_dir: str) -> List[Record]:
    """Read every file of one export directory as raw records, account last."""
    out: List[Record] = []
    fills_dir = os.path.join(export_dir, "fills")
    if os.path.isdir(fills_dir):
        for name in sorted(os.listdir(fills_dir)):
            if name.endswith(".json"):
                with open(os.path.join(fills_dir, name)) as fh:
                    out.append(("rest_page", name[:-5], fh.read()))
    ws = os.path.join(export_dir, "ws_fills.jsonl")
    if os.path.exists(ws):
        with open(ws) as fh:
            for i, line in enumerate(fh):
                if line.strip():
                    out.append(("ws", str(i), line.strip()))
    for kind in ("settlements", "account"):
        with open(os.path.join(export_dir, kind + ".json")) as fh:
            out.append((kind, kind, fh.read()))
    return out


def _rest_pages(pages: Dict[str, str]) -> List[dict]:
    """Follow the cursor chain from the index page."""
    out = []
    cursor: Optional[str] = "index"
    seen = set()
    while cursor is not None:
        if cursor in seen:
            raise ValueError("REST fills: cursor loop at %r" % cursor)
        if cursor not in pages:
            raise ValueError("REST fills: page %r is missing" % cursor)
        seen.add(cursor)
        page = json.loads(pages[cursor])
        out.append(page)
        cursor = page.get("cursor")
    return out


def parse_export(records: List[Record]) -> Export:
    pages: Dict[str, str] = {}
    ws_lines: List[Tuple[int, str]] = []
    settlements_txt = account_txt = None
    for kind, name, payload in records:
        if kind == "rest_page":
            pages[name] = payload
        elif kind == "ws":
            ws_lines.append((int(name), payload))
        elif kind == "settlements":
            settlements_txt = payload
        elif kind == "account":
            account_txt = payload
    if account_txt is None:
        raise ValueError("export has no account.json")
    account = json.loads(account_txt)

    fills: Dict[str, Fill] = {}
    busted: Set[str] = set()

    def add(f: Fill) -> None:
        fills[f.fill_id] = f
        if f.busted:
            busted.add(f.fill_id)

    # WebSocket first, then REST: the REST record is the reference copy.
    for _, line in sorted(ws_lines):
        msg = json.loads(line)
        if msg.get("type") == "fill":
            add(parse_fill_v1(msg["msg"]))
        elif msg.get("type") == "fill_bust":
            busted.add(msg["msg"]["fill_id"])
    if pages:
        for page in _rest_pages(pages):
            parse = parse_fill_v1 if int(page.get("api_version", 2)) == 1 else parse_fill_v2
            for rec in page.get("fills", []):
                add(parse(rec))

    settlements = []
    if settlements_txt is not None:
        settlements = [parse_settlement(r) for r in json.loads(settlements_txt)["settlements"]]

    exported_at = account.get("exported_at")
    return Export(
        export_date=account["export_date"],
        exported_at=parse_ts(exported_at) if exported_at else
        parse_ts(account["export_date"] + "T00:00:00Z") + dt.timedelta(days=1),
        account=account,
        fills=fills,
        busted=busted,
        settlements=settlements,
    )


def load_export(export_dir: str) -> Export:
    return parse_export(read_export_dir(export_dir))


_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def event_date(ticker: str) -> Optional[str]:
    """The event date in a ticker, e.g. HIGHT-SFO-26SEP04-T71 -> 2026-09-04."""
    for seg in ticker.split("-"):
        m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})$", seg)
        if m and m.group(2) in _MONTHS:
            try:
                return dt.date(2000 + int(m.group(1)), _MONTHS[m.group(2)],
                               int(m.group(3))).isoformat()
            except ValueError:
                continue
    return None
