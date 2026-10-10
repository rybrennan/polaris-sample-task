"""Checks on the grader itself.

    python3 -m unittest discover -s grader -p 'test_*.py'
"""

import os
import random
import unittest

import grade
import sim

ROOT = grade.ROOT
SEEDS = [11, 12]


class SimTest(unittest.TestCase):
    def test_deterministic(self):
        a, b = sim.records(sim.simulate(7)), sim.records(sim.simulate(7))
        self.assertEqual(a, b)

    def test_cash_identity_holds_in_truth(self):
        for seed in SEEDS:
            acct = sim.simulate(seed)
            t = sim.truth(acct)
            moved = acct.end_cash - acct.start_cash - acct.net_deposits
            self.assertEqual(moved, t["total_cents"] + sum(t["open"].values()))

    def test_every_trap_is_present(self):
        for seed in grade.DEFAULT_SEEDS:
            acct = sim.simulate(seed)
            cov = sim.coverage(acct, sim.records(acct))
            self.assertGreater(cov["rest_pages"], 5, seed)
            for k in ("ws_only_fills", "overlap_fills", "short_entries", "identical_partial_fills",
                      "fully_closed_early", "cross_midnight_settles", "open_markets",
                      "busted_fills", "void_markets"):
                self.assertGreater(cov[k], 0, (seed, k))
        legacy = [sim.coverage(a, sim.records(a))["legacy_pages"]
                  for a in map(sim.simulate, grade.DEFAULT_SEEDS)]
        self.assertGreater(sum(1 for n in legacy if n), 2)
        self.assertGreater(sum(1 for n in legacy if not n), 0)

    def test_sample_has_no_rare_behaviors(self):
        acct = sim.simulate(20261010, rare=False)
        cov = sim.coverage(acct, sim.records(acct))
        self.assertEqual((cov["busted_fills"], cov["void_markets"], cov["legacy_pages"]), (0, 0, 0))

    def test_every_tamper_changes_the_export(self):
        recs = sim.records(sim.simulate(SEEDS[0]))
        for kind in grade.TAMPERS:
            bad, _ = grade.tamper(recs, kind, random.Random(1))
            self.assertNotEqual(bad, recs, kind)


class GraderTest(unittest.TestCase):
    def score(self, rel):
        return grade.grade(os.path.join(ROOT, rel), SEEDS)["score"]

    def test_reference_scores_full_marks(self):
        self.assertEqual(self.score("solutions/reference"), 100.0)

    def test_starter_scores_low(self):
        self.assertLess(self.score("starter"), 10)

    def test_hardcoded_answers_score_zero(self):
        self.assertEqual(self.score("solutions/gaming_hardcode_sample"), 0.0)

    def test_wrong_solutions_lose_points(self):
        for rel in ("solutions/wrong_symptom_only", "solutions/wrong_plug_to_balance",
                    "solutions/wrong_dedup_by_value", "solutions/wrong_settlement_double_loss"):
            self.assertLessEqual(self.score(rel), 70.0, rel)


if __name__ == "__main__":
    unittest.main()
