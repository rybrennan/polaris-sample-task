import datetime as dt
import unittest

from fillbook.ledger import build_positions, event_date
from fillbook.loader import Fill, Settlement

T = dt.datetime(2026, 9, 1, 20, tzinfo=dt.timezone.utc)
TK = "HIGHT-SFO-26SEP01-T70"


def fill(side, action, count, price, fee=0, fid="f1"):
    return Fill(fid, TK, side, action, count, price, fee, T)


def S(result, revenue=0):
    return Settlement(TK, result, T, revenue)


class LedgerTest(unittest.TestCase):
    def test_buy_yes_settles_yes(self):
        pos = build_positions([fill("yes", "buy", 10, 40, fee=17)])[TK]
        self.assertEqual(pos.realized(S("yes")), 1000 - 400 - 17)

    def test_buy_yes_settles_no(self):
        pos = build_positions([fill("yes", "buy", 5, 30)])[TK]
        self.assertEqual(pos.realized(S("no")), -150)

    def test_sell_yes_is_buy_no_and_nets(self):
        # buy 10 YES @40, then sell-YES ticket: receive 10 NO @45 (YES sold at 55)
        pos = build_positions([
            fill("yes", "buy", 10, 40, fid="f1"),
            fill("no", "sell", 10, 45, fid="f2"),
        ])[TK]
        self.assertEqual(pos.realized(S("no")), 1000 - 400 - 450)
        self.assertEqual(pos.realized(S("yes")), 1000 - 400 - 450)

    def test_event_date(self):
        self.assertEqual(event_date(TK), dt.date(2026, 9, 1))


if __name__ == "__main__":
    unittest.main()
