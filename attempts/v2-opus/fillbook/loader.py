"""Read an account export directory into Fill and Settlement objects.

See docs/exchange_api.md. Key rules implemented here:
- REST pages are followed by cursor until null (cycle-guarded, no page cap).
- Archived pages (api_version 1) and the WebSocket capture use the v1 wire
  format: dollars as decimal strings, `quantity`, `market_ticker`, `ts` in ms.
- REST and WebSocket overlap; fills are de-duplicated by fill_id.
- Busted fills (REST status "busted" or a WebSocket `fill_bust`) are dropped.
- `side` is the contract received; `price` is the price paid for that side.
"""

import datetime as dt
import json
import os
import re
from decimal import Decimal
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


class Fill(NamedTuple):
    fill_id: str
    ticker: str
    side: str      # "yes" | "no": contract received
    action: str    # "buy" | "sell": verb on the order ticket (informational)
    count: int
    price: int     # cents paid per contract of `side`
    fee: int       # cents
    ts: dt.datetime


class Settlement(NamedTuple):
    ticker: str
    result: str                 # "yes" | "no" | "void"
    settled_time: dt.datetime
    revenue: int
    settlement_value: Optional[int] = None   # cents per YES contract
    row: Optional[dict] = None               # raw row, for reconciliation

    def yes_value(self) -> int:
        if self.settlement_value is not None:
            return int(self.settlement_value)
        if self.result == "yes":
            return 100
        if self.result == "no":
            return 0
        raise ValueError("void settlement for %s has no settlement_value" % self.ticker)


def parse_ts(value) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
    s = str(value).strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if m and m.group(2):  # Python 3.9 fromisoformat wants 3 or 6 fraction digits
        frac = (m.group(2)[1:] + "000000")[:6]
        s = m.group(1) + "." + frac + m.group(3)
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc)


def dollars_to_cents(value) -> int:
    c = Decimal(str(value)) * 100
    if c != c.to_integral_value():
        raise ValueError("amount %r is not a whole number of cents" % (value,))
    return int(c)


def _int_cents(value) -> int:
    if isinstance(value, bool):
        raise ValueError("bad cents value %r" % (value,))
    if isinstance(value, int):
        return value
    d = Decimal(str(value))
    if d != d.to_integral_value():
        raise ValueError("cents value %r is not an integer" % (value,))
    return int(d)


def is_v1(rec: dict) -> bool:
    return "market_ticker" in rec or "quantity" in rec or (
        "price" in rec and "yes_price" not in rec)


def normalize(rec: dict) -> Fill:
    side = rec["side"]
    if side not in ("yes", "no"):
        raise ValueError("fill %s has bad side %r" % (rec.get("fill_id"), side))
    if is_v1(rec):
        return Fill(
            fill_id=rec["fill_id"],
            ticker=rec["market_ticker"],
            side=side,
            action=rec.get("action", ""),
            count=int(rec["quantity"]),
            price=dollars_to_cents(rec["price"]),
            fee=dollars_to_cents(rec.get("fee", "0")),
            ts=parse_ts(rec["ts"]),
        )
    if side == "yes":
        price = _int_cents(rec["yes_price"]) if "yes_price" in rec else 100 - _int_cents(rec["no_price"])
    else:
        price = _int_cents(rec["no_price"]) if "no_price" in rec else 100 - _int_cents(rec["yes_price"])
    return Fill(
        fill_id=rec["fill_id"],
        ticker=rec["ticker"],
        side=side,
        action=rec.get("action", ""),
        count=int(rec["count"]),
        price=price,
        fee=_int_cents(rec.get("fee", 0)),
        ts=parse_ts(rec["created_time"]),
    )


class RawExport(NamedTuple):
    rest: List[dict]            # REST fill records, in page order
    ws: List[dict]              # WebSocket fill records
    ws_busts: Set[str]          # fill_ids busted per the WebSocket
    problems: List[str]         # structural problems found while loading


def load_rest_fills(data_dir: str, problems: Optional[List[str]] = None) -> List[dict]:
    out: List[dict] = []
    cursor: Optional[str] = "index"
    seen: Set[str] = set()
    while cursor:
        if cursor in seen:
            if problems is not None:
                problems.append("fills: cursor loop at page %r" % cursor)
            break
        seen.add(cursor)
        path = os.path.join(data_dir, "fills", cursor + ".json")
        with open(path) as fh:
            page = json.load(fh)
        out.extend(page.get("fills") or [])
        cursor = page.get("cursor")
    return out


def load_ws(data_dir: str) -> Tuple[List[dict], Set[str]]:
    path = os.path.join(data_dir, "ws_fills.jsonl")
    fills: List[dict] = []
    busts: Set[str] = set()
    if not os.path.exists(path):
        return fills, busts
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            kind = msg.get("type")
            if kind == "fill":
                fills.append(msg["msg"])
            elif kind == "fill_bust":
                body = msg.get("msg") if isinstance(msg.get("msg"), dict) else msg
                fid = body.get("fill_id") or msg.get("fill_id")
                if fid:
                    busts.add(fid)
    return fills, busts


def load_ws_fills(data_dir: str) -> List[dict]:
    return load_ws(data_dir)[0]


def load_raw(data_dir: str) -> RawExport:
    problems: List[str] = []
    rest = load_rest_fills(data_dir, problems)
    ws, busts = load_ws(data_dir)
    return RawExport(rest, ws, busts, problems)


def merge_fills(raw: RawExport, problems: Optional[List[str]] = None) -> List[Fill]:
    """De-duplicate REST + WebSocket fills by fill_id and drop busted ones."""
    busted: Set[str] = set(raw.ws_busts)
    chosen: Dict[str, Fill] = {}
    for rec in raw.rest + raw.ws:
        fid = rec["fill_id"]
        if rec.get("status", "executed") == "busted":
            busted.add(fid)
            continue
        f = normalize(rec)
        prev = chosen.get(fid)
        if prev is None:
            chosen[fid] = f
        elif problems is not None and prev._replace(ts=f.ts, action=f.action) != f:
            problems.append("fill %s: copies disagree (%s vs %s)" % (fid, _short(prev), _short(f)))
    fills = [f for fid, f in chosen.items() if fid not in busted]
    return sorted(fills, key=lambda f: (f.ts, f.fill_id))


def _short(f: Fill) -> str:
    return "%s %s x%d @%d fee %d" % (f.ticker, f.side, f.count, f.price, f.fee)


def load_fills(data_dir: str) -> List[Fill]:
    return merge_fills(load_raw(data_dir))


def load_settlement_rows(data_dir: str) -> List[dict]:
    with open(os.path.join(data_dir, "settlements.json")) as fh:
        return json.load(fh)["settlements"]


def to_settlement(r: dict) -> Settlement:
    sv = r.get("settlement_value")
    return Settlement(r["ticker"], r["market_result"], parse_ts(r["settled_time"]),
                      int(r.get("revenue", 0)), None if sv is None else int(sv), r)


def load_settlements(data_dir: str) -> List[Settlement]:
    return [to_settlement(r) for r in load_settlement_rows(data_dir)]


_MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def event_date(ticker: str) -> Optional[dt.date]:
    """Event date from a ticker segment like 26SEP04 (YYMONDD)."""
    for seg in ticker.split("-"):
        m = re.fullmatch(r"(\d{2})([A-Z]{3})(\d{2})", seg)
        if m and m.group(2) in _MONTHS:
            try:
                return dt.date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
            except ValueError:
                continue
    return None
