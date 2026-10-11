"""Synthetic event-contract exchange.

Simulates one account trading binary weather markets over a few weeks, then
writes the daily exports the exchange delivers for it. Each export holds
paginated REST fills, a WebSocket capture, the settlement rows issued that
day and a cash statement.

Ground truth comes from the simulator's own record of every fill, bust and
settlement. It never comes from parsing the exports, so the grader shares no
logic with any solution.

All money is integer cents. All time windows are half-open: [start, end).
"""

import datetime as dt
import json
import os
import random
from typing import Dict, List, Optional, Tuple

UTC = dt.timezone.utc
CITIES = ["SFO", "NYC", "CHI", "MIA", "DEN", "SEA", "AUS", "BOS", "PHX", "ATL"]
PAGE_SIZE = 12
# REST pages holding only fills before this date are served in the v1 format.
LEGACY_CUTOFF = dt.datetime(2026, 8, 20, tzinfo=UTC)
# Each export's REST window reaches back into the day before...
REST_BACK = dt.timedelta(hours=6)
# ...and stops short of the export time.
REST_LAG = dt.timedelta(hours=2)
P_BUST = 0.04
P_LATE_BUST = 0.4  # share of busts announced hours or days later
P_VOID = 0.07
TICK = dt.timedelta(milliseconds=1)

FillRec = dict     # one simulated fill (see Account.apply_fill)
Export = dict      # one daily export (see exports)


def fee_cents(count: int, price: int) -> int:
    """Exchange fee: ceil(0.07 * count * P * (1 - P)) dollars, in cents."""
    return (7 * count * price * (100 - price) + 9999) // 10000


class State:
    """A market's position as of some moment, replayed from its live fills."""

    def __init__(self):
        self.held = {"yes": 0, "no": 0}
        self.acq = {"yes": 0, "no": 0}
        self.cost = {"yes": 0, "no": 0}
        self.fees = 0
        self.cash = 0
        self.live = 0

    def apply(self, f: FillRec) -> None:
        side, count, price = f["side"], f["count"], f["price"]
        self.held[side] += count
        self.acq[side] += count
        self.cost[side] += count * price
        self.fees += f["fee"]
        self.cash -= count * price + f["fee"]
        self.live += 1
        pairs = min(self.held["yes"], self.held["no"])
        if pairs:
            # One YES and one NO always pay $1 together, so the exchange nets
            # the pair and credits cash at once.
            self.held["yes"] -= pairs
            self.held["no"] -= pairs
            self.cash += 100 * pairs

    def payout(self, value: int) -> int:
        """Settlement payout when one YES is worth `value` cents."""
        return self.held["yes"] * value + self.held["no"] * (100 - value)


class Market:
    """One market and every fill the account made in it."""

    def __init__(self, ticker: str, event_day: dt.date, result: str,
                 close: dt.datetime, settle: dt.datetime):
        self.ticker = ticker
        self.event_day = event_day
        self.result = result
        self.close = close
        self.settle = settle
        self.value: Optional[int] = None  # what one YES pays, cents
        self.fills: List[FillRec] = []

    def state(self, t: dt.datetime) -> State:
        """Position as of t: fills before t, minus busts announced before t."""
        st = State()
        for f in self.fills:
            if f["ts"] < t and not (f["busted"] and f["bust_ts"] < t):
                st.apply(f)
        return st

    def cash(self, t: dt.datetime) -> int:
        """Net cash this market has moved as of t."""
        st = self.state(t)
        return st.cash + (st.payout(self.value) if self.settle < t else 0)

    def revisions(self) -> List[Tuple[dt.datetime, dict]]:
        """Settlement rows the exchange issues, with their issue times.

        One row at settlement, then one more for every bust announced after
        the market settled.
        """
        times = [self.settle] + sorted(
            f["bust_ts"] for f in self.fills
            if f["busted"] and f["bust_ts"] >= self.settle)
        out = []
        for i, t in enumerate(times):
            st = self.state(t + TICK)
            out.append((t, {
                "ticker": self.ticker, "revision": i + 1,
                "market_result": self.result,
                "settled_time": iso(self.settle),
                "yes_count": st.acq["yes"], "no_count": st.acq["no"],
                "yes_total_cost": st.cost["yes"],
                "no_total_cost": st.cost["no"],
                "fee_cost": st.fees, "settlement_value": self.value,
                "revenue": st.payout(self.value),
            }))
        return out


class Account:
    """One simulated account: its markets, fills, deposits and export days."""

    def __init__(self, seed: int, rare: bool = True):
        self.seed = seed
        self.rare = rare  # False turns off busts and voided markets
        self.rng = random.Random(seed)
        self.markets: List[Market] = []
        self.fills: List[FillRec] = []
        self.days: List[dt.datetime] = []
        self.deposits: List[Tuple[dt.datetime, int]] = []
        self.start_cash = 0

    def new_id(self, prefix: str) -> str:
        return "%s-%012x" % (prefix, self.rng.getrandbits(48))

    def apply_fill(self, m: Market, side: str, action: str, price: int,
                   count: int, ts: dt.datetime, order_id: str,
                   live: State) -> None:
        """Records one fill, deciding now whether it will be busted."""
        rng = self.rng
        busted = self.rare and rng.random() < P_BUST
        bust_ts = None
        if busted:
            if rng.random() < P_LATE_BUST:
                bust_ts = ts + dt.timedelta(hours=rng.uniform(2, 40))
            else:
                bust_ts = ts + dt.timedelta(seconds=rng.randint(30, 900))
        f = {"fill_id": self.new_id("f"), "order_id": order_id,
             "ticker": m.ticker, "side": side, "action": action,
             "count": count, "price": price, "fee": fee_cents(count, price),
             "ts": ts, "busted": busted, "bust_ts": bust_ts}
        m.fills.append(f)
        self.fills.append(f)
        if not busted:
            live.apply(f)

    def order(self, m: Market, side: str, action: str, price: int, qty: int,
              ts: dt.datetime, live: State) -> None:
        """One order ticket, filled in one to three pieces."""
        rng = self.rng
        oid = self.new_id("o")
        r = rng.random()
        if qty >= 2 and qty % 2 == 0 and r < 0.25:
            # Two equal partial fills in the same second: identical except
            # for fill_id.
            parts = [(qty // 2, 0), (qty // 2, 0)]
        elif qty >= 3 and r < 0.55:
            a = rng.randint(1, qty - 1)
            parts = [(a, 0), (qty - a, rng.randint(1, 3))]
        else:
            parts = [(qty, 0)]
        for count, lag in parts:
            self.apply_fill(m, side, action, price, count,
                            ts + dt.timedelta(seconds=lag), oid, live)

    def trade(self, m: Market, fair: float) -> None:
        """Trades one market: opens, adds to and closes positions."""
        rng = self.rng
        live = State()  # what the trader believes it holds
        open_ = m.close - dt.timedelta(minutes=rng.randint(120, 600))
        span = int((m.close - open_).total_seconds())
        times = sorted(open_ + dt.timedelta(seconds=rng.randint(0, span - 5))
                       for _ in range(rng.randint(1, 6)))
        yes_px = max(4, min(96, round(fair * 100 + rng.gauss(0, 8))))
        for t in times:
            yes_px = max(2, min(98, yes_px + rng.randint(-6, 6)))
            if live.held["yes"] == 0 and live.held["no"] == 0:
                held = None
            else:
                held = "yes" if live.held["yes"] else "no"
            if held is None or rng.random() < 0.45:
                side = held or ("yes" if rng.random() < 0.5 else "no")
                qty = rng.randint(1, 40)
                if side == "yes":
                    self.order(m, "yes", "buy", yes_px, qty, t, live)
                else:
                    # A NO position opens with a buy-NO ticket or a sell-YES
                    # ticket. The exchange reports the second as side=no,
                    # action=sell.
                    action = "buy" if rng.random() < 0.5 else "sell"
                    self.order(m, "no", action, 100 - yes_px, qty, t, live)
            else:
                q = live.held[held]
                k = q if rng.random() < 0.6 else rng.randint(1, q)
                if held == "yes":  # a sell-YES ticket
                    self.order(m, "no", "sell", 100 - yes_px, k, t, live)
                else:              # a sell-NO ticket
                    self.order(m, "yes", "sell", yes_px, k, t, live)

    def run(self) -> "Account":
        """Simulates the whole period and returns self."""
        rng = self.rng
        first_day = dt.date(2026, 8, 3) + dt.timedelta(days=rng.randint(0, 40))
        n_days = rng.randint(8, 12)
        seen = set()
        for di in range(n_days):
            day = first_day + dt.timedelta(days=di)
            for _ in range(rng.randint(5, 10)):
                ticker = "HIGHT-%s-%s-T%d" % (
                    rng.choice(CITIES), day.strftime("%y%b%d").upper(),
                    rng.randint(40, 99))
                if ticker in seen:
                    continue
                seen.add(ticker)
                fair = rng.uniform(0.08, 0.92)
                close = (dt.datetime(day.year, day.month, day.day, 18,
                                     tzinfo=UTC)
                         + dt.timedelta(minutes=rng.randint(0, 330)))
                settle = close + dt.timedelta(minutes=rng.randint(20, 480))
                result = "yes" if rng.random() < fair else "no"
                m = Market(ticker, day, result, close, settle)
                if self.rare and rng.random() < P_VOID:
                    m.result = "void"
                self.trade(m, fair)
                m.value = {"yes": 100, "no": 0}.get(m.result)
                if m.value is None:
                    m.value = rng.randint(5, 95)
                self.markets.append(m)
        self.fills.sort(key=lambda f: f["ts"])
        for m in self.markets:
            m.fills.sort(key=lambda f: f["ts"])

        # One export per UTC day, until every settlement and bust is delivered.
        last = max([m.settle for m in self.markets]
                   + [f["bust_ts"] for f in self.fills if f["busted"]])
        d = dt.datetime(first_day.year, first_day.month, first_day.day,
                        tzinfo=UTC)
        while d <= last:
            self.days.append(d)
            d += dt.timedelta(days=1)
        self.start_cash = rng.randint(50000, 250000)
        for day in self.days:
            if rng.random() < 0.2:
                self.deposits.append(
                    (day + dt.timedelta(hours=rng.randint(1, 11)),
                     rng.choice([10000, 20000, 50000, -15000])))
        return self

    # ------------------------------------------------------------- truth

    def total_cash(self, t: dt.datetime) -> int:
        """The account's cash balance as of t."""
        return (self.start_cash
                + sum(a for ts, a in self.deposits if ts < t)
                + sum(m.cash(t) for m in self.markets))

    def truth(self, t: dt.datetime) -> dict:
        """What a correct `fillbook report --json` prints once every export
        up to time t has been ingested."""
        settled: Dict[str, int] = {}
        open_: Dict[str, int] = {}
        by_day: Dict[str, int] = {}
        for m in self.markets:
            if m.settle < t:
                settled[m.ticker] = m.cash(t)
                k = m.event_day.isoformat()
                by_day[k] = by_day.get(k, 0) + settled[m.ticker]
            else:
                st = m.state(t)
                if st.live:
                    open_[m.ticker] = st.cash
        return {"markets": settled, "open": open_,
                "total_cents": sum(settled.values()), "by_day": by_day}


def simulate(seed: int, rare: bool = True) -> Account:
    return Account(seed, rare).run()


# ------------------------------------------------------------------- export

def iso(ts: dt.datetime) -> str:
    return (ts.strftime("%Y-%m-%dT%H:%M:%S.")
            + "%03dZ" % (ts.microsecond // 1000))


def ms(ts: dt.datetime) -> int:
    return int(ts.timestamp() * 1000)


def rest_record(f: FillRec, as_of: dt.datetime) -> dict:
    """A fill in the REST v2 format, as the endpoint shows it at `as_of`."""
    p = f["price"]
    busted = f["busted"] and f["bust_ts"] < as_of
    return {
        "fill_id": f["fill_id"], "order_id": f["order_id"],
        "ticker": f["ticker"], "side": f["side"], "action": f["action"],
        "count": f["count"],
        "yes_price": p if f["side"] == "yes" else 100 - p,
        "no_price": p if f["side"] == "no" else 100 - p,
        "fee": f["fee"], "created_time": iso(f["ts"]),
        "status": "busted" if busted else "executed",
    }


def ws_msg(f: FillRec) -> dict:
    """A fill in the v1 wire format."""
    return {
        "fill_id": f["fill_id"], "order_id": f["order_id"],
        "market_ticker": f["ticker"], "side": f["side"],
        "action": f["action"], "quantity": f["count"],
        "price": "%.2f" % (f["price"] / 100.0),
        "fee": "%.2f" % (f["fee"] / 100.0),
        "ts": ms(f["ts"]),
    }


def _rest_pages(acct: Account, start: dt.datetime, end: dt.datetime,
                rng: random.Random) -> Dict[str, dict]:
    """REST fills in [start - 6h, end - 2h), newest first, paginated."""
    rest = [f for f in reversed(acct.fills)
            if start - REST_BACK <= f["ts"] < end - REST_LAG]
    pages = [rest[i:i + PAGE_SIZE]
             for i in range(0, len(rest), PAGE_SIZE)] or [[]]
    names = ["index"] + ["%08x" % rng.getrandbits(32) for _ in pages[1:]]
    out = {}
    for i, page in enumerate(pages):
        cursor = names[i + 1] if i + 1 < len(pages) else None
        if page and max(f["ts"] for f in page) < LEGACY_CUTOFF:
            body = [dict(ws_msg(f), status=rest_record(f, end)["status"])
                    for f in page]
            out[names[i]] = {"api_version": 1, "fills": body,
                             "cursor": cursor}
        else:
            out[names[i]] = {"api_version": 2,
                             "fills": [rest_record(f, end) for f in page],
                             "cursor": cursor}
    return out


def _ws_lines(acct: Account, start: dt.datetime, end: dt.datetime,
              rng: random.Random) -> List[dict]:
    """The day's WebSocket capture, minus reconnect gaps early in the day.

    The gaps all fall inside the REST window, so no fill is lost.
    """
    gaps = []
    for _ in range(rng.randint(0, 2)):
        g0 = start + dt.timedelta(minutes=rng.randint(0, 17 * 60 - 90))
        gaps.append((g0, g0 + dt.timedelta(minutes=rng.randint(20, 90))))
    events = []
    for f in acct.fills:
        in_gap = any(a <= f["ts"] < b for a, b in gaps)
        if start <= f["ts"] < end and not in_gap:
            events.append((f["ts"], {"type": "fill", "msg": ws_msg(f)}))
            if rng.random() < 0.15:
                events.append((f["ts"] - TICK,
                               {"type": "heartbeat", "ts": ms(f["ts"]) - 1}))
        if f["busted"] and start <= f["bust_ts"] < end:
            events.append((f["bust_ts"], {"type": "fill_bust", "msg": {
                "fill_id": f["fill_id"], "ts": ms(f["bust_ts"])}}))
    events.sort(key=lambda e: e[0])
    return [e[1] for e in events]


def exports(acct: Account) -> List[Export]:
    """The exchange's daily exports, oldest first, as plain data."""
    rng = random.Random(acct.seed * 7919 + 1)
    revisions = sorted((r for m in acct.markets for r in m.revisions()),
                       key=lambda r: r[0])
    out = []
    for start in acct.days:
        end = start + dt.timedelta(days=1)
        rest_pages = _rest_pages(acct, start, end, rng)
        ws = _ws_lines(acct, start, end, rng)
        account = {
            "export_date": start.date().isoformat(),
            "exported_at": iso(end),
            "starting_cash_cents": acct.total_cash(start),
            "ending_cash_cents": acct.total_cash(end),
            "net_deposits_cents": sum(a for ts, a in acct.deposits
                                      if start <= ts < end),
        }
        out.append({
            "id": start.date().isoformat(), "end": end, "rest": rest_pages,
            "ws": ws,
            "settlements": [row for t, row in revisions if start <= t < end],
            "account": account,
        })
    return out


def coverage(acct: Account, exps: List[Export]) -> Dict[str, int]:
    """Counts how often each trap occurs in a run, for the self-tests."""
    two_hours = dt.timedelta(hours=2)
    late = [f for f in acct.fills
            if f["busted"] and f["bust_ts"] - f["ts"] > two_hours]
    rest_ids: Dict[str, int] = {}
    for e in exps:
        for p in e["rest"].values():
            for r in p["fills"]:
                rest_ids[r["fill_id"]] = rest_ids.get(r["fill_id"], 0) + 1
    ws_ids = {line["msg"]["fill_id"] for e in exps for line in e["ws"]
              if line["type"] == "fill"}
    seen, identical = set(), 0
    for f in acct.fills:
        key = (f["ticker"], f["side"], f["action"], f["count"], f["price"],
               f["ts"])
        identical += key in seen
        seen.add(key)
    return {
        "exports": len(exps),
        "max_pages": max(len(e["rest"]) for e in exps),
        "legacy_pages": sum(1 for e in exps for p in e["rest"].values()
                            if p["api_version"] == 1),
        "rest_only_fills": len(set(rest_ids) - ws_ids),
        "ws_only_fills": len(ws_ids - set(rest_ids)),
        "in_two_exports": sum(1 for n in rest_ids.values() if n > 1),
        "busted_fills": sum(1 for f in acct.fills if f["busted"]),
        "late_busts_next_export": sum(
            1 for f in late if f["bust_ts"].date() != f["ts"].date()),
        "reissued_settlements": sum(1 for m in acct.markets
                                    if len(m.revisions()) > 1),
        "void_markets": sum(1 for m in acct.markets if m.result == "void"),
        "identical_partial_fills": identical,
        "short_entries": sum(1 for f in acct.fills
                             if f["side"] == "no" and f["action"] == "sell"),
        "deposit_days": len(acct.deposits),
    }


def write_export(exp: Export, out_dir: str) -> None:
    """Writes one export as the directory layout the exchange delivers."""
    os.makedirs(os.path.join(out_dir, "fills"), exist_ok=True)
    for name, page in exp["rest"].items():
        with open(os.path.join(out_dir, "fills", name + ".json"), "w") as fh:
            json.dump(page, fh, indent=1)
            fh.write("\n")
    with open(os.path.join(out_dir, "ws_fills.jsonl"), "w") as fh:
        for line in exp["ws"]:
            fh.write(json.dumps(line) + "\n")
    with open(os.path.join(out_dir, "settlements.json"), "w") as fh:
        json.dump({"settlements": exp["settlements"]}, fh, indent=1)
        fh.write("\n")
    with open(os.path.join(out_dir, "account.json"), "w") as fh:
        json.dump(exp["account"], fh, indent=1)
        fh.write("\n")


def write_all(exps: List[Export], root: str) -> None:
    for e in exps:
        write_export(e, os.path.join(root, e["id"]))


if __name__ == "__main__":
    import sys
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    a = simulate(int(args[0]))
    ex = exports(a)
    print(json.dumps(coverage(a, ex), indent=1))
    if len(args) > 1:
        write_all(ex, args[1])
