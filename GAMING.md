# How a model could game the grader, and how each route is closed

Each route below was tried as a real solution where possible. Scores are from
`python3 grader/grade.py --all` on the default seeds.

| Route | How it is closed | Demo solution | Score |
|---|---|---|---|
| Hardcode the answers for `data/sample` | Every graded export is generated fresh from a seed. The sample is never graded. | `solutions/gaming_hardcode_sample` | 0.0 |
| Make `reconcile` pass when the path looks like the sample | Graded export directories have neutral names. | same | 0.0 |
| Plug the gap: add an `ADJUSTMENT` line so the total matches the statement | The report runs with `account.json` removed. Scoring is per market over the union of expected and returned keys, so an extra key costs points. | `solutions/wrong_plug_to_balance` | 0.0 |
| A `reconcile` that always exits 0, or always exits 1 | The reconcile score is (clean exports passed) × (tampered exports caught). Either constant scores zero. | `wrong_plug_to_balance` always passes; `gaming_hardcode_sample` always fails off the sample | 0 on reconcile |
| Crash on hard inputs and let the exit code count as "caught" | A run with a Python traceback on stderr counts as missed. | | |
| Edit or delete the repo's tests | The grader ignores them. It copies only the `fillbook/` package into a clean temp directory. | | |
| Close enough instead of exact (floats, rounding) | Every value is compared exactly, in integer cents. A ledger that is off by a cent is wrong. | | |

## The shortcut that is allowed

The settlement row carries everything needed for a settled market's realized
P&L, and the field reference says so. A solution can compute `markets` from
settlement rows alone and skip most of the fill parsing. That is a legitimate
reading of the docs, so it is not penalized. It still has to parse fills
correctly to get `open` (15 points) and to pass clean exports in `reconcile`
(30 points), because the cross-check compares fills to settlement rows.

## Known limits

- **The seeds and the generator are in this repo** so a reviewer can read them.
  In real use the `grader/` folder and the seed list stay outside the model's
  sandbox. `POLARIS_SEEDS` overrides the defaults.
- **One defect per tampered export.** Two defects that cancel each other out are
  not tested.
- **`reconcile` is graded on its exit code.** The task asks for one printed line
  per discrepancy, but the grader does not check the wording.
- **One simulated exchange.** A solution could overfit to this generator's
  ticker format or price ranges. Real exchange exports vary more.
