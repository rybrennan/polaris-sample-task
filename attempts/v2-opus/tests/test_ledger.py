import datetime as dt
import os
import unittest

from fillbook.ledger import build_positions
from fillbook.loader import Fill, Settlement, event_date, merge_fills, RawExport, normalize
from fillbook.reconcile import reconcile
from fillbook.report import build_report

T = dt.datetime(2026, 9, 1, 20, tzinfo=dt.timezone.utc)
TK = "HIGHT-SFO-26SEP01-T70"
SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "sample")


def fill(side, action, count, price, fee=0, fid="f1"):
    return Fill(fid, TK, side, action, count, price, fee, T)


def settle(result, value=None):
    return Settlement(TK, result, T, 0, value)


class LedgerTest(unittest.TestCase):
    def pos(self, *fills):
        return build_positions(fills)[TK]

    def test_buy_yes_settles_yes(self):
        self.assertEqual(self.pos(fill("yes", "buy", 10, 40, fee=17)).realized(settle("yes")),
                         1000 - 400 - 17)

    def test_buy_yes_settles_no(self):
        self.assertEqual(self.pos(fill("yes", "buy", 5, 30)).realized(settle("no")), -150)

    def test_sell_yes_is_buy_no_and_nets_pairs(self):
        # buy 10 YES @40, then sell 10 YES @55 = receive 10 NO @45; pairs net for $1 each
        p = self.pos(fill("yes", "buy", 10, 40, fid="f1"), fill("no", "sell", 10, 45, fid="f2"))
        self.assertEqual(p.qty, {"yes": 0, "no": 0})
        self.assertEqual(p.realized(settle("no")), 1000 - 400 - 450)
        self.assertEqual(p.realized(settle("yes")), 150)

    def test_void_uses_settlement_value(self):
        p = self.pos(fill("yes", "buy", 10, 40), fill("no", "buy", 4, 50, fid="f2"))
        self.assertEqual(p.realized(settle("void", 30)), 400 + 6 * 30 - 400 - 200)

    def test_v1_and_dedup_and_bust(self):
        v2 = {"fill_id": "a", "ticker": TK, "side": "no", "action": "sell", "count": 3,
              "yes_price": 63, "no_price": 37, "fee": 2,
              "created_time": "2026-09-01T20:00:00.000Z", "status": "executed"}
        v1 = {"fill_id": "a", "market_ticker": TK, "side": "no", "action": "sell",
              "quantity": 3, "price": "0.37", "fee": "0.02", "ts": 1788292800000}
        self.assertEqual(normalize(v1)._replace(ts=T), normalize(v2)._replace(ts=T))
        b = dict(v1, fill_id="b")
        fills = merge_fills(RawExport([v2], [v1, b], {"b"}, []))
        self.assertEqual([f.fill_id for f in fills], ["a"])

    def test_event_date(self):
        self.assertEqual(event_date("HIGHT-SFO-26SEP04-T71"), dt.date(2026, 9, 4))


class SampleTest(unittest.TestCase):
    def test_sample_reconciles(self):
        self.assertEqual(reconcile(SAMPLE), [])

    def test_sample_total(self):
        self.assertEqual(build_report(SAMPLE)["total_cents"], 6223)


if __name__ == "__main__":
    unittest.main()
