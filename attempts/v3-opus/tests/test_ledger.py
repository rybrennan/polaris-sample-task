import datetime as dt
import json
import os
import shutil
import tempfile
import unittest

from fillbook.ledger import build_positions
from fillbook.loader import Fill, Settlement, event_date, parse_export
from fillbook.reconcile import check
from fillbook.report import build_report
from fillbook.store import connect, ingest

T = dt.datetime(2026, 9, 1, 20, tzinfo=dt.timezone.utc)
TK = "HIGHT-SFO-26SEP01-T70"
EXPORTS = os.path.join(os.path.dirname(__file__), "..", "data", "exports")


def fill(side, action, count, price, fee=0, fid="f1", busted=False):
    return Fill(fid, "o1", TK, side, action, count, price, fee, T, busted)


def settle(result, value):
    return Settlement(TK, 1, result, T, 0, 0, 0, 0, 0, value, 0)


class LedgerTest(unittest.TestCase):
    def test_buy_yes_settles_yes(self):
        pos = build_positions([fill("yes", "buy", 10, 40, fee=17)])[TK]
        self.assertEqual(pos.realized(settle("yes", 100)), 1000 - 400 - 17)

    def test_buy_yes_settles_no(self):
        pos = build_positions([fill("yes", "buy", 5, 30)])[TK]
        self.assertEqual(pos.realized(settle("no", 0)), -150)

    def test_sell_yes_is_a_no_purchase_and_pairs_net(self):
        # bought 10 YES at 40, then "sold" 10 YES at 55 = bought 10 NO at 45
        pos = build_positions([
            fill("yes", "buy", 10, 40, fid="f1"),
            fill("no", "sell", 10, 45, fid="f2"),
        ])[TK]
        self.assertEqual(pos.pairs, 10)
        self.assertEqual(pos.cash_flow(), 1000 - 400 - 450)
        self.assertEqual(pos.realized(settle("no", 0)), 150)

    def test_busted_fill_is_ignored(self):
        pos = build_positions([fill("yes", "buy", 5, 30, fid="f1"),
                               fill("yes", "buy", 9, 30, fid="f2", busted=True)])[TK]
        self.assertEqual(pos.count["yes"], 5)

    def test_void_pays_settlement_value(self):
        pos = build_positions([fill("yes", "buy", 3, 50, fid="f1"),
                               fill("no", "buy", 5, 40, fid="f2")])[TK]
        self.assertEqual(pos.realized(settle("void", 86)), 300 - 150 - 200 + 2 * 14)

    def test_event_date(self):
        self.assertEqual(event_date("HIGHT-SFO-26SEP04-T71"), "2026-09-04")


class LoaderTest(unittest.TestCase):
    def test_v1_archive_page_and_ws(self):
        page = {"api_version": 1, "cursor": None, "fills": [
            {"fill_id": "a", "order_id": "o", "market_ticker": TK, "side": "no",
             "action": "sell", "quantity": 12, "price": "0.37", "fee": "0.02",
             "ts": 1788290000123, "status": "executed"}]}
        ws = {"type": "fill", "msg": {"fill_id": "b", "order_id": "o", "market_ticker": TK,
                                       "side": "yes", "action": "buy", "quantity": 2,
                                       "price": "0.63", "fee": "0.01", "ts": 1788290000999}}
        bust = {"type": "fill_bust", "msg": {"fill_id": "b", "ts": 1788290001999}}
        exp = parse_export([
            ("rest_page", "index", json.dumps(page)),
            ("ws", "0", json.dumps(ws)), ("ws", "1", json.dumps(bust)),
            ("settlements", "settlements", '{"settlements": []}'),
            ("account", "account", json.dumps({"export_date": "2026-09-01",
                                               "starting_cash_cents": 0,
                                               "ending_cash_cents": 0,
                                               "net_deposits_cents": 0}))])
        a = exp.fills["a"]
        self.assertEqual((a.count, a.price, a.fee, a.side), (12, 37, 2, "no"))
        self.assertEqual(exp.busted, {"b"})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.days = sorted(os.listdir(EXPORTS))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, name, days):
        conn = connect(os.path.join(self.tmp, name))
        for d in days:
            ingest(conn, os.path.join(EXPORTS, d))
        return conn

    def test_order_and_redelivery_do_not_matter(self):
        ref = self.build("a.db", self.days)
        shuffled = self.days[::-1] + self.days[:3]
        other = self.build("b.db", shuffled)
        self.assertEqual(build_report(ref), build_report(other))
        self.assertEqual(check(ref), [])
        self.assertEqual(check(other), [])

    def test_missing_export_is_caught(self):
        conn = self.build("c.db", self.days[:4] + self.days[5:])
        self.assertTrue(any("missing" in p for p in check(conn)))

    def test_v04_store_migrates(self):
        src = os.path.join(os.path.dirname(__file__), "..", "data", "store.db")
        path = os.path.join(self.tmp, "old.db")
        shutil.copy(src, path)
        conn = connect(path)
        self.assertEqual(check(conn), [])
        fresh = self.build("d.db", self.days[:3])
        self.assertEqual(build_report(conn), build_report(fresh))


if __name__ == "__main__":
    unittest.main()
