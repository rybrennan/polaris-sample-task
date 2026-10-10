"""Synthetic event-contract exchange.

Simulates one account trading binary weather markets and writes the records the
exchange would export for it: paginated REST fills, a WebSocket capture,
settlement rows and an account statement.

Ground truth comes from the simulator's own cash ledger, built while the trades
happen. It never comes from parsing the emitted records, so the grader shares
no logic with any solution.

All money is integer cents.
"""

import datetime as dt
import json
import os
import random

UTC = dt.timezone.utc
CITIES = ["SFO", "NYC", "CHI", "MIA", "DEN", "SEA", "AUS", "BOS", "PHX", "ATL"]
PAGE_SIZE = 50
LEGACY_CUTOFF = dt.datetime(2026, 8, 20, tzinfo=UTC)  # REST pages before this are served as v1
P_BUST = 0.03
P_VOID = 0.07


def fee_cents(count, price):
    """Exchange fee: ceil(0.07 * count * P * (1 - P)) dollars, in cents."""
    return (7 * count * price * (100 - price) + 9999) // 10000


class Market:
    def __init__(self, ticker, event_day, result, close, settle):
        self.ticker = ticker
        self.event_day = event_day
        self.result = result
        self.close = close
        self.settle = settle
        self.settled = False
        self.held = {"yes": 0, "no": 0}
        self.acq = {"yes": 0, "no": 0}
        self.cost = {"yes": 0, "no": 0}
        self.fees = 0
        self.cash = 0  # every cent this market moved, signed
        self.revenue = 0
        self.value = None  # YES payout per contract at settlement, cents


class Account:
    def __init__(self, seed, rare=True):
        self.seed = seed
        self.rare = rare
        self.rng = random.Random(seed)
        self.markets = []
        self.fills = []

    def new_id(self, prefix):
        return "%s-%012x" % (prefix, self.rng.getrandbits(48))

    def apply_fill(self, m, side, action, price, count, ts, order_id):
        fee = fee_cents(count, price)
        if self.rare and self.rng.random() < P_BUST:
            # The exchange cancels the trade after the fact. It never happened.
            self.fills.append({
                "fill_id": self.new_id("f"), "order_id": order_id, "ticker": m.ticker,
                "side": side, "action": action, "count": count, "price": price,
                "fee": fee, "ts": ts, "busted": True,
                "bust_ts": ts + dt.timedelta(seconds=self.rng.randint(30, 900)),
            })
            return
        m.held[side] += count
        m.acq[side] += count
        m.cost[side] += count * price
        m.fees += fee
        m.cash -= count * price + fee
        pairs = min(m.held["yes"], m.held["no"])
        if pairs:
            # A YES and a NO in one market always pay $1 together, so the
            # exchange nets the pair and credits cash at once.
            m.held["yes"] -= pairs
            m.held["no"] -= pairs
            m.cash += 100 * pairs
        self.fills.append({
            "fill_id": self.new_id("f"), "order_id": order_id, "ticker": m.ticker,
            "side": side, "action": action, "count": count, "price": price,
            "fee": fee, "ts": ts, "busted": False,
        })

    def order(self, m, side, action, price, qty, ts):
        """One order ticket, filled in one to three pieces."""
        rng = self.rng
        oid = self.new_id("o")
        r = rng.random()
        if qty >= 2 and qty % 2 == 0 and r < 0.25:
            # Two equal partial fills in the same second: identical except fill_id.
            parts = [(qty // 2, 0), (qty // 2, 0)]
        elif qty >= 3 and r < 0.55:
            a = rng.randint(1, qty - 1)
            parts = [(a, 0), (qty - a, rng.randint(1, 3))]
        else:
            parts = [(qty, 0)]
        for count, lag in parts:
            self.apply_fill(m, side, action, price, count, ts + dt.timedelta(seconds=lag), oid)

    def trade(self, m, fair):
        rng = self.rng
        open_ = m.close - dt.timedelta(minutes=rng.randint(120, 600))
        span = int((m.close - open_).total_seconds())
        times = sorted(open_ + dt.timedelta(seconds=rng.randint(0, span - 5))
                       for _ in range(rng.randint(1, 6)))
        yes_px = max(4, min(96, round(fair * 100 + rng.gauss(0, 8))))
        for t in times:
            yes_px = max(2, min(98, yes_px + rng.randint(-6, 6)))
            if m.held["yes"] == 0 and m.held["no"] == 0:
                held = None
            else:
                held = "yes" if m.held["yes"] else "no"
            if held is None or rng.random() < 0.45:
                side = held or ("yes" if rng.random() < 0.5 else "no")
                qty = rng.randint(1, 40)
                if side == "yes":
                    self.order(m, "yes", "buy", yes_px, qty, t)
                else:
                    # A NO position can be opened with a buy-NO ticket or a
                    # sell-YES ticket. The exchange reports the second as
                    # side=no, action=sell.
                    action = "buy" if rng.random() < 0.5 else "sell"
                    self.order(m, "no", action, 100 - yes_px, qty, t)
            else:
                q = m.held[held]
                k = q if rng.random() < 0.6 else rng.randint(1, q)
                if held == "yes":
                    # Sell-YES ticket: executes as a purchase of NO.
                    self.order(m, "no", "sell", 100 - yes_px, k, t)
                else:
                    self.order(m, "yes", "sell", yes_px, k, t)

    def run(self):
        rng = self.rng
        first_day = dt.date(2026, 8, 3) + dt.timedelta(days=rng.randint(0, 40))
        n_days = rng.randint(8, 12)
        seen = set()
        for di in range(n_days):
            day = first_day + dt.timedelta(days=di)
            for _ in range(rng.randint(5, 10)):
                ticker = "HIGHT-%s-%s-T%d" % (rng.choice(CITIES), day.strftime("%y%b%d").upper(),
                                              rng.randint(40, 99))
                if ticker in seen:
                    continue
                seen.add(ticker)
                fair = rng.uniform(0.08, 0.92)
                close = dt.datetime(day.year, day.month, day.day, 18, tzinfo=UTC) + \
                    dt.timedelta(minutes=rng.randint(0, 330))
                settle = close + dt.timedelta(minutes=rng.randint(20, 480))
                m = Market(ticker, day, "yes" if rng.random() < fair else "no", close, settle)
                if self.rare and rng.random() < P_VOID:
                    m.result = "void"
                self.trade(m, fair)
                self.markets.append(m)
        self.fills.sort(key=lambda f: f["ts"])
        self.export_ts = max(m.close for m in self.markets) + dt.timedelta(minutes=90)
        for m in self.markets:
            if m.settle <= self.export_ts:
                m.settled = True
                if m.result == "void":
                    m.value = rng.randint(5, 95)
                else:
                    m.value = 100 if m.result == "yes" else 0
                m.revenue = m.held["yes"] * m.value + m.held["no"] * (100 - m.value)
                m.cash += m.revenue
        self.start_cash = rng.randint(50000, 250000)
        self.net_deposits = rng.choice([0, 20000, 50000, -15000])
        self.end_cash = self.start_cash + self.net_deposits + sum(m.cash for m in self.markets)
        self.period = (first_day, first_day + dt.timedelta(days=n_days))
        return self


def simulate(seed, rare=True):
    return Account(seed, rare).run()


def truth(acct):
    """What a correct `fillbook report --json` prints for this account."""
    settled = {m.ticker: m.cash for m in acct.markets if m.settled}
    open_ = {m.ticker: m.cash for m in acct.markets if not m.settled}
    by_day = {}
    for m in acct.markets:
        if m.settled:
            k = m.event_day.isoformat()
            by_day[k] = by_day.get(k, 0) + m.cash
    return {"markets": settled, "open": open_, "total_cents": sum(settled.values()), "by_day": by_day}


def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ts.microsecond // 1000)


def rest_record(f):
    p = f["price"]
    return {
        "fill_id": f["fill_id"], "order_id": f["order_id"], "ticker": f["ticker"],
        "side": f["side"], "action": f["action"], "count": f["count"],
        "yes_price": p if f["side"] == "yes" else 100 - p,
        "no_price": p if f["side"] == "no" else 100 - p,
        "fee": f["fee"], "created_time": iso(f["ts"]),
        "status": "busted" if f["busted"] else "executed",
    }


def ws_record(f):
    return {"type": "fill", "msg": {
        "fill_id": f["fill_id"], "order_id": f["order_id"], "market_ticker": f["ticker"],
        "side": f["side"], "action": f["action"], "quantity": f["count"],
        "price": "%.2f" % (f["price"] / 100.0), "fee": "%.2f" % (f["fee"] / 100.0),
        "ts": int(f["ts"].timestamp() * 1000),
    }}


def records(acct):
    """The exchange export, as plain data: rest pages, ws lines, settlements, account."""
    rng = random.Random(acct.seed * 7919 + 1)
    fills = acct.fills
    n = len(fills)
    rest_cut = int(n * 0.93)   # REST lags the live feed
    ws_from = int(n * 0.78)    # the WebSocket capture started late
    rest = list(reversed(fills[:rest_cut]))  # newest first
    pages = [rest[i:i + PAGE_SIZE] for i in range(0, len(rest), PAGE_SIZE)]
    names = ["index"] + ["%08x" % rng.getrandbits(32) for _ in pages[1:]]
    rest_pages = {}
    for i, page in enumerate(pages):
        cursor = names[i + 1] if i + 1 < len(pages) else None
        if max(f["ts"] for f in page) < LEGACY_CUTOFF:
            body = [dict(ws_record(f)["msg"], status="busted" if f["busted"] else "executed")
                    for f in page]
            rest_pages[names[i]] = {"api_version": 1, "fills": body, "cursor": cursor}
        else:
            rest_pages[names[i]] = {"api_version": 2, "fills": [rest_record(f) for f in page],
                                    "cursor": cursor}
    events = []
    for f in fills[ws_from:]:
        if rng.random() < 0.15:
            events.append((f["ts"] - dt.timedelta(milliseconds=500), {"type": "heartbeat",
                           "ts": int(f["ts"].timestamp() * 1000) - 500}))
        events.append((f["ts"], ws_record(f)))
        if f["busted"]:
            events.append((f["bust_ts"], {"type": "fill_bust", "msg": {
                "fill_id": f["fill_id"], "ts": int(f["bust_ts"].timestamp() * 1000)}}))
    events.sort(key=lambda e: e[0])
    ws = [e[1] for e in events]
    settlements = []
    for m in sorted(acct.markets, key=lambda m: m.settle):
        if m.settled:
            settlements.append({
                "ticker": m.ticker, "market_result": m.result, "settled_time": iso(m.settle),
                "yes_count": m.acq["yes"], "no_count": m.acq["no"],
                "yes_total_cost": m.cost["yes"], "no_total_cost": m.cost["no"],
                "fee_cost": m.fees, "settlement_value": m.value, "revenue": m.revenue,
            })
    account = {
        "period_start": acct.period[0].isoformat(), "period_end": acct.period[1].isoformat(),
        "exported_at": iso(acct.export_ts),
        "starting_cash_cents": acct.start_cash, "ending_cash_cents": acct.end_cash,
        "net_deposits_cents": acct.net_deposits,
    }
    return {"rest": rest_pages, "ws": ws, "settlements": settlements, "account": account}


def coverage(acct, recs):
    """Confirm the dataset exercises every trap the task is about."""
    ws_ids = {line["msg"]["fill_id"] for line in recs["ws"] if line["type"] == "fill"}
    rest_ids = {f["fill_id"] for p in recs["rest"].values() for f in p["fills"]}
    seen, identical = set(), 0
    for f in acct.fills:
        if f["busted"]:
            continue
        key = (f["ticker"], f["side"], f["action"], f["count"], f["price"], f["ts"])
        identical += key in seen
        seen.add(key)
    netted = [m for m in acct.markets if m.settled and min(m.acq.values()) > 0]
    return {
        "rest_pages": len(recs["rest"]),
        "ws_only_fills": len(ws_ids - rest_ids),
        "overlap_fills": len(ws_ids & rest_ids),
        "short_entries": sum(1 for f in acct.fills if f["side"] == "no" and f["action"] == "sell"
                             and not f["busted"]),
        "identical_partial_fills": identical,
        "netted_markets": len(netted),
        "fully_closed_early": sum(1 for m in netted if m.revenue == 0),
        "cross_midnight_settles": sum(1 for m in acct.markets if m.settled and m.settle.date() != m.event_day),
        "open_markets": sum(1 for m in acct.markets if not m.settled),
        "busted_fills": sum(1 for f in acct.fills if f["busted"]),
        "void_markets": sum(1 for m in acct.markets if m.settled and m.result == "void"),
        "legacy_pages": sum(1 for p in recs["rest"].values() if p["api_version"] == 1),
    }


def write(recs, out_dir):
    os.makedirs(os.path.join(out_dir, "fills"), exist_ok=True)
    for name, page in recs["rest"].items():
        with open(os.path.join(out_dir, "fills", name + ".json"), "w") as fh:
            json.dump(page, fh, indent=1)
            fh.write("\n")
    with open(os.path.join(out_dir, "ws_fills.jsonl"), "w") as fh:
        for line in recs["ws"]:
            fh.write(json.dumps(line) + "\n")
    with open(os.path.join(out_dir, "settlements.json"), "w") as fh:
        json.dump({"settlements": recs["settlements"]}, fh, indent=1)
        fh.write("\n")
    with open(os.path.join(out_dir, "account.json"), "w") as fh:
        json.dump(recs["account"], fh, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    import sys
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    seed = int(args[0])
    # --sample: the visible export, which never shows the rare behaviors
    a = simulate(seed, rare="--sample" not in sys.argv)
    r = records(a)
    print(json.dumps(coverage(a, r), indent=1))
    if len(args) > 1:
        write(r, args[1])
