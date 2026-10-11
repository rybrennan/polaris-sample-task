import datetime as dt
import unittest

from fillbook.ledger import build_positions, event_date
from fillbook.loader import Fill, Settlement, normalize

T = dt.datetime(2026, 9, 1, 20, tzinfo=dt.timezone.utc)
TK = "HIGHT-SFO-26SEP01-T70"


def fill(side, action, count, price, fee=0, fid="f1", status="executed"):
    return Fill(fid, TK, side, action, count, price, fee, T, status)


class LedgerTest(unittest.TestCase):
    def test_buy_yes_settles_yes(self):
        pos = build_positions([fill("yes", "buy", 10, 40, fee=17)])[TK]
        self.assertEqual(pos.realized(Settlement(TK, "yes", T, 1000)), 1000 - 400 - 17)

    def test_buy_yes_settles_no(self):
        pos = build_positions([fill("yes", "buy", 5, 30)])[TK]
        self.assertEqual(pos.realized(Settlement(TK, "no", T, 0)), -150)

    def test_sell_is_an_acquisition_of_the_other_side(self):
        # "sell" fills report the contract received; they cost money, they do not pay it
        pos = build_positions([fill("no", "sell", 10, 43)])[TK]
        self.assertEqual(pos.realized(Settlement(TK, "no", T, 1000)), 1000 - 430)

    def test_pairs_net_for_a_dollar(self):
        pos = build_positions([fill("yes", "buy", 10, 40, fid="a"), fill("no", "buy", 10, 55, fid="b")])[TK]
        self.assertEqual(pos.cash_flow(), 1000 - 400 - 550)
        self.assertEqual(pos.realized(Settlement(TK, "yes", T, 0)), 50)

    def test_busted_fill_never_happened(self):
        pos = build_positions([fill("yes", "buy", 10, 40, fee=5), fill("no", "buy", 3, 60, fid="b", status="busted")])[TK]
        self.assertEqual(pos.cash_flow(), -405)

    def test_void_pays_settlement_value(self):
        pos = build_positions([fill("no", "buy", 10, 40)])[TK]
        self.assertEqual(pos.realized(Settlement(TK, "void", T, 600, value=40)), 600 - 400)


class LoaderTest(unittest.TestCase):
    def test_v1_wire_format(self):
        f = normalize({"fill_id": "x", "order_id": "o", "market_ticker": TK, "side": "no", "action": "sell",
                       "quantity": 12, "price": "0.37", "fee": "0.02", "ts": 1788290000123})
        self.assertEqual((f.count, f.price, f.fee), (12, 37, 2))

    def test_v2_uses_price_of_received_side(self):
        f = normalize({"fill_id": "x", "ticker": TK, "side": "no", "action": "sell", "count": 2,
                       "yes_price": 57, "no_price": 43, "fee": 4, "created_time": "2026-08-25T18:41:33.000Z"})
        self.assertEqual(f.price, 43)

    def test_event_date(self):
        self.assertEqual(event_date(TK), "2026-09-01")


if __name__ == "__main__":
    unittest.main()
