# How a model could game the grader, and how each route is closed

Each route below was tried as a real solution where possible. Scores are from
`python3 grader/grade.py --all` on the default seeds.

| Route | How it is closed | Demo solution | Score |
|---|---|---|---|
| Hardcode the answer for the visible sample | Every graded run of exports is generated fresh from a seed. The sample is never graded. | `solutions/gaming_hardcode_sample` | 0.0 |
| A `reconcile` that always exits 0, or always exits 1 | The reconcile score is (clean stores passed) × (defective stores caught). Either constant scores zero. | `gaming_hardcode_sample` and `wrong_plug_to_statements` always pass | 0 on reconcile |
| Plug the gap: add an `ADJUSTMENT` line so the total matches the cash statements | Scoring is per market, over the union of expected and returned keys. An extra key costs points, and the real markets stay wrong. | `solutions/wrong_plug_to_statements` | 0.1 |
| Print an empty answer and collect credit where nothing is expected | Open markets are scored only at checkpoints that have open markets. (A self-test caught this leak during the build: the hardcoded solution scored 1.4 before the fix.) | `gaming_hardcode_sample` | 0.0 |
| Crash on hard inputs and let the exit code count as "caught" | A run with a Python traceback on stderr counts as missed. | | |
| Edit or delete the repo's tests, or the visible store | The grader ignores them. It copies only the `fillbook/` package into a clean temp directory and builds every store itself with the 0.4 code in `grader/legacy/`. | | |
| Close enough instead of exact (floats, rounding) | Every value is compared exactly, in integer cents. | | |

## The shortcut that is allowed

The settlement row carries everything needed for a settled market's realized
P&L. A solution can lean on settlement rows and skip some of the fill work. That
is a legitimate reading of the field reference, so it is not penalized. It
still has to get every fill right to report open markets (15 points) and to
pass clean stores in `reconcile` (30 points), because the cross-check compares
fills to the latest settlement row.

## Known limits

- **The seeds, the generator and the legacy writer are in this repo** so a
  reviewer can read them. In real use `grader/` and the seed list stay outside
  the model's sandbox. `POLARIS_SEEDS` overrides the defaults.
- **One defect per defective store.** Two defects that cancel each other out
  are not tested.
- **`reconcile` is graded on its exit code**, not on the wording of what it prints.
- **The ingest sequence has one redelivery and one swapped pair per run.**
  Heavier reordering is not tested.
- **One simulated exchange.** A solution could overfit to this generator's
  formats. Real exports vary more.
