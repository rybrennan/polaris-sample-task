import datetime as dt
import unittest

from fillbook.ledger import build_positions
from fillbook.loader import Fill, Settlement

T = dt.datetime(2026, 9, 1, 20, tzinfo=dt.timezone.utc)


def fill(side, action, count, price, fee=0, fid="f1"):
    return Fill(fid, "HIGHT-SFO-26SEP01-T70", side, action, count, price, fee, T)


class LedgerTest(unittest.TestCase):
    def test_buy_yes_settles_yes(self):
        pos = build_positions([fill("yes", "buy", 10, 40, fee=17)])["HIGHT-SFO-26SEP01-T70"]
        s = Settlement("HIGHT-SFO-26SEP01-T70", "yes", T, 1000)
        self.assertEqual(pos.realized(s), 1000 - 400 - 17)

    def test_buy_yes_settles_no(self):
        pos = build_positions([fill("yes", "buy", 5, 30)])["HIGHT-SFO-26SEP01-T70"]
        s = Settlement("HIGHT-SFO-26SEP01-T70", "no", T, 0)
        self.assertEqual(pos.realized(s), -150)

    def test_sell_books_proceeds(self):
        pos = build_positions([
            fill("yes", "buy", 10, 40, fid="f1"),
            fill("yes", "sell", 10, 55, fid="f2"),
        ])["HIGHT-SFO-26SEP01-T70"]
        s = Settlement("HIGHT-SFO-26SEP01-T70", "no", T, 0)
        self.assertEqual(pos.realized(s), 550 - 400)


if __name__ == "__main__":
    unittest.main()
