# polaris-sample-task: fillbook ledger reconciliation

A sample long-horizon software engineering task in the Polaris shape: a prompt,
a starter repository, and a deterministic grader that assigns a numeric score.
It also includes a reference solution that scores full marks, plausible wrong
solutions that score low, a note on how the grader could be gamed, and model
attempts used to check difficulty.

The problem comes from a real bug in my own trading systems: a P&L screen
showed about a $25 loss when the exchange's records said about $320. All data
here is synthetic. See [DESIGN.md](DESIGN.md).

## The task

[TASK.md](TASK.md) is the prompt. The solver gets `starter/`: a small Python
tool that turns an exchange account export into a P&L report, and that report is
wrong. On the sample export it says the account made $523.61. The account's own
cash statement says trading moved about $62. The solver has to:

1. make `fillbook report` exact to the cent, from fills and settlements only;
2. add `fillbook reconcile`, which passes clean exports and catches broken ones.

The exchange's field reference ([starter/docs/exchange_api.md](starter/docs/exchange_api.md))
is the spec. The starter has ten interacting traps, including a unit test that encodes
the wrong behavior. Graded exports also contain three documented behaviors the sample
never shows.

## Results

`python3 grader/grade.py --all` on the default seeds:

| solution | score | settled /40 | open /15 | total /5 | by_day /10 | reconcile /30 | clean passed | tampers caught |
|---|---|---|---|---|---|---|---|---|
| `solutions/reference` | **100.0** | 40.0 | 15.0 | 5.0 | 10.0 | 30.0 | 100% | 100% |
| `solutions/wrong_settlement_double_loss` | **70.0** | 40.0 | 15.0 | 5.0 | 10.0 | 0.0 | 0% | 100% |
| `solutions/wrong_dedup_by_value` | **39.8** | 26.3 | 12.5 | 0.0 | 1.0 | 0.0 | 0% | 100% |
| `solutions/wrong_fits_the_sample` | **23.0** | 15.4 | 6.4 | 0.0 | 1.2 | 0.0 | 0% | 50% |
| `solutions/wrong_symptom_only` | **0.0** | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0% | 0% |
| `solutions/wrong_plug_to_balance` | **0.0** | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 100% | 0% |
| `solutions/gaming_hardcode_sample` | **0.0** | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0% | 100% |
| `starter` | **0.0** | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0% | 0% |

Each wrong solution is one plausible mistake, built as a small patch by
[solutions/build_variants.py](solutions/build_variants.py):

- **settlement_double_loss**: correct report; the cash check forgets pairs netted before settlement, so every clean export fails.
- **dedup_by_value**: drops "duplicate" fills by their contents, which deletes real partial fills.
- **fits_the_sample**: fixes everything the sample shows and nothing it doesn't.
- **symptom_only**: fixes the bug the ticket points at, then stops.
- **plug_to_balance**: adds an adjustment line so the total matches the statement.
- **gaming_hardcode_sample**: prints the sample's answers.

**Difficulty: not yet at the bar.** Four attempts by Claude Sonnet and Claude
Opus, across two versions of the task, all scored 100, each in about three
minutes or less ([attempts/](attempts/README.md)). The grader separates wrong
solutions well. The task does not yet separate strong models. DESIGN.md says
why, and what the next version changes.

## Grading

```bash
python3 grader/grade.py path/to/solution     # breakdown for one solution
python3 grader/grade.py --all                # the table above
python3 -m unittest discover -s grader -p 'test_*.py'   # checks on the grader itself
```

- **Truth.** Every graded export is generated fresh from a seed by a simulated
  exchange ([grader/sim.py](grader/sim.py)). Truth comes from the simulator's
  own cash ledger, not from parsing the export, so the grader shares no code
  path with any solution.
- **What runs.** Only the solution's `fillbook/` package is copied into a clean
  temp directory.
- **How `report` is scored.** It runs without the cash statement and is scored
  per market and per day, exact to the cent.
- **How `reconcile` is scored.** It runs on six clean exports and twelve
  tampered ones, covering six defect kinds. Its score is the clean pass rate
  times the catch rate.
- Standard library only. Python 3.9+. A full run takes about three seconds.

How a model could game this, and how each route is closed: [GAMING.md](GAMING.md).

## Layout

```
TASK.md                 the prompt
starter/                the repository the solver receives
grader/                 simulator, tamper set, scorer, grader self-tests
solutions/reference/    full-marks solution
solutions/wrong_*/      plausible wrong solutions
solutions/gaming_*/     a gaming attempt
attempts/               model attempts, with scores
DESIGN.md               where the task came from, the traps, calibration
GAMING.md               gaming routes and known limits
```

MIT license.
