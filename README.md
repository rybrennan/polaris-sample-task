[![grader](https://github.com/rybrennan/polaris-sample-task/actions/workflows/ci.yml/badge.svg)](https://github.com/rybrennan/polaris-sample-task/actions/workflows/ci.yml)

# polaris-sample-task: fillbook ledger

A sample long-horizon software engineering task in the Polaris shape: a prompt,
a starter repository, and a deterministic grader that assigns a numeric score.
It comes with a reference solution that scores full marks and plausible wrong
solutions that score low. It also has notes on how a model could game the
grader, and six model attempts used to measure difficulty.

The problem comes from a real bug in my own trading systems: a P&L screen
showed about a $25 loss when the exchange's records said about $320. All data
here is synthetic. See [DESIGN.md](DESIGN.md).

## The task

[TASK.md](TASK.md) is the prompt. The solver gets `starter/`, version 0.4 of a
small Python tool that keeps a ledger of an exchange's daily account exports in
a SQLite store. Production has been running it, and its store is wrong: it
reports $483.44 realized over three days in which the account's cash moved
about $30. The solver has to:

1. make `ingest`, `report` and `reconcile` correct, exact to the cent;
2. keep them correct as exports keep arriving: twice, late, out of order, or
   correcting earlier ones;
3. repair the store 0.4 already wrote, without re-ingesting anything.

The exchange's field reference ([starter/docs/exchange_api.md](starter/docs/exchange_api.md))
is the spec. The starter has sixteen traps, from a sign convention to a schema
migration (listed in DESIGN.md).

## Results

`python3 grader/grade.py --all` on the default seeds (56 report checkpoints,
18 reconcile stores):

| solution | score | settled /40 | open /15 | total /5 | by_day /10 | reconcile /30 | clean passed | defects caught |
|---|---|---|---|---|---|---|---|---|
| `solutions/reference` | **100.0** | 40.0 | 15.0 | 5.0 | 10.0 | 30.0 | 100% | 100% |
| `solutions/wrong_in_order_ledger` | **89.0** | 39.9 | 15.0 | 4.3 | 9.8 | 20.0 | 66% | 100% |
| `solutions/wrong_first_settlement_row` | **70.0** | 40.0 | 15.0 | 5.0 | 10.0 | 0.0 | 0% | 100% |
| `solutions/wrong_settlement_double_loss` | **70.0** | 40.0 | 15.0 | 5.0 | 10.0 | 0.0 | 0% | 100% |
| `solutions/wrong_no_archived_pages` | **44.9** | 21.4 | 8.0 | 2.7 | 5.4 | 7.5 | 50% | 50% |
| `solutions/wrong_dedup_by_value` | **36.5** | 25.7 | 9.3 | 0.0 | 1.5 | 0.0 | 0% | 100% |
| `solutions/wrong_fresh_start_on_upgrade` | **34.7** | 18.4 | 11.6 | 0.0 | 4.8 | 0.0 | 0% | 100% |
| `solutions/wrong_symptom_only` | **0.3** | 0.0 | 0.3 | 0.0 | 0.0 | 0.0 | 0% | 0% |
| `solutions/wrong_plug_to_statements` | **0.1** | 0.0 | 0.1 | 0.0 | 0.0 | 0.0 | 100% | 0% |
| `solutions/gaming_hardcode_sample` | **0.0** | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 100% | 0% |
| `starter` | **0.1** | 0.0 | 0.1 | 0.0 | 0.0 | 0.0 | 0% | 0% |

Each wrong solution is one plausible mistake, built as a small patch by
[solutions/build_variants.py](solutions/build_variants.py):

- **in_order_ledger**: the natural incremental design. Applies each correction to what it has seen so far, so a backfilled export loses corrections that arrived first.
- **first_settlement_row**: ignores reissued settlement rows. The report is right; the cross-check fails on clean stores.
- **settlement_double_loss**: the cash check forgets pairs netted before settlement.
- **no_archived_pages**: everything right except the archived page format the visible exports never contain.
- **dedup_by_value**: drops "duplicate" fills by their contents, deleting real partial fills.
- **fresh_start_on_upgrade**: treats the 0.4 store as unusable and starts over, losing three days.
- **symptom_only**: fixes the bug the ticket points at, then stops.
- **plug_to_statements**: adds an adjustment line so the total matches the cash.
- **gaming_hardcode_sample**: prints the sample's answer.

## Difficulty: measured, and below the bar

Claude Sonnet and Claude Opus each attempted all three versions of this task.
All six attempts scored 100 ([attempts/](attempts/README.md)). Version 3 added
state across runs, late corrections, redelivery, backfill and a migration.
Solve time rose from about 1.5 minutes to about 6, but neither model dropped a
point. The grader separates wrong solutions well. The task does not separate
strong models. [DESIGN.md](DESIGN.md) says why, and what I would try next.

## Grading

```bash
python3 grader/grade.py path/to/solution     # breakdown for one solution (about 30 s)
python3 grader/grade.py --all                # the table above
python3 -m unittest discover -s grader -p 'test_*.py'   # checks on the grader itself
```

- **Truth.** Every graded run of exports is generated fresh from a seed by a
  simulated exchange ([grader/sim.py](grader/sim.py)). Truth comes from the
  simulator's own record of every fill, bust and settlement, so the grader
  shares no code path with any solution.
- **The legacy store.** The grader builds it for every seed with the real 0.4
  code, then copies only the solution's `fillbook/` package into a clean
  directory to run against it.
- **How `report` is scored.** It is checked at every checkpoint, per market and
  per day, exact to the cent.
- **How `reconcile` is scored.** Its score is the clean pass rate times the
  defect catch rate.

How a model could game this, and how each route is closed: [GAMING.md](GAMING.md).

## Layout

```
TASK.md                 the prompt
starter/                the repository the solver receives
grader/                 simulator, legacy 0.4 writer, defects, scorer, self-tests
solutions/reference/    full-marks solution
solutions/wrong_*/      plausible wrong solutions
solutions/gaming_*/     a gaming attempt
attempts/               six model attempts, with scores
DESIGN.md               where the task came from, the traps, calibration
GAMING.md               gaming routes and known limits
```

Version 2 is in git as tag `v2`. Version 1 was replaced before the first commit; DESIGN.md describes it.

MIT license.
