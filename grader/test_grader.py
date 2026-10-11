"""Checks on the grader itself.

    python3 -m unittest discover -s grader -p 'test_*.py'
"""

import filecmp
import os
import random
import unittest

import grade
import sim

ROOT = grade.ROOT
SEEDS = [11]


class SimTest(unittest.TestCase):
    def test_deterministic(self):
        a, b = sim.exports(sim.simulate(7)), sim.exports(sim.simulate(7))
        self.assertEqual(a, b)

    def test_statements_chain_and_close(self):
        for seed in [11, 12]:
            acct = sim.simulate(seed)
            ex = sim.exports(acct)
            for prev, cur in zip(ex, ex[1:]):
                self.assertEqual(prev["account"]["ending_cash_cents"], cur["account"]["starting_cash_cents"])
            t = acct.truth(ex[-1]["end"])
            self.assertEqual(ex[0]["account"]["starting_cash_cents"]
                             + sum(e["account"]["net_deposits_cents"] for e in ex)
                             + t["total_cents"] + sum(t["open"].values()),
                             ex[-1]["account"]["ending_cash_cents"])

    def test_every_trap_is_present(self):
        legacy = 0
        for seed in grade.DEFAULT_SEEDS:
            acct = sim.simulate(seed)
            cov = sim.coverage(acct, sim.exports(acct))
            self.assertGreater(cov["max_pages"], 5, seed)
            self.assertGreaterEqual(cov["exports"], grade.LEGACY_EXPORTS + 6, seed)
            for k in ("in_two_exports", "busted_fills", "late_busts_next_export",
                      "reissued_settlements", "void_markets", "identical_partial_fills",
                      "short_entries"):
                self.assertGreater(cov[k], 0, (seed, k))
            legacy += cov["legacy_pages"] > 0
        self.assertGreaterEqual(legacy, 2)
        self.assertLess(legacy, len(grade.DEFAULT_SEEDS))

    def test_every_tamper_changes_something(self):
        acct = sim.simulate(SEEDS[0])
        ex = sim.exports(acct)
        for kind in grade.TAMPERS:
            bad, skip, _ = grade.tamper(acct, ex, kind, random.Random(1))
            self.assertTrue(bad != ex or skip is not None, kind)

    def test_legacy_writer_is_the_starter(self):
        a = os.path.join(ROOT, "starter", "fillbook")
        b = os.path.join(grade.LEGACY, "fillbook")
        names = sorted(n for n in os.listdir(a) if n.endswith(".py"))
        self.assertEqual(names, sorted(n for n in os.listdir(b) if n.endswith(".py")))
        match, mismatch, errors = filecmp.cmpfiles(a, b, names, shallow=False)
        self.assertEqual((mismatch, errors), ([], []))


class GraderTest(unittest.TestCase):
    def score(self, rel):
        return grade.grade(os.path.join(ROOT, rel), SEEDS)["score"]

    def test_reference_scores_full_marks(self):
        self.assertEqual(self.score("solutions/reference"), 100.0)

    def test_starter_scores_low(self):
        self.assertLess(self.score("starter"), 10)

    def test_hardcoded_answers_score_zero(self):
        self.assertEqual(self.score("solutions/gaming_hardcode_sample"), 0.0)


if __name__ == "__main__":
    unittest.main()
