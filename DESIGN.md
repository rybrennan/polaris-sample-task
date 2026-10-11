# Design notes

## Where the task comes from

I run live trading systems on an event-contract exchange. In July 2026 one of my
own P&L screens showed a lifetime loss of about $25 when the exchange's records
said about $320. The cause was a single misread field: the exchange records an
entry into a NO position as `action=sell`, and my code read every lone sell as a
cash-out. Hundreds of settled markets dropped out of the total. Afterwards I
wrote a read-only tool that rebuilds P&L from raw fills and settlements, and kept
a list of every convention in the exchange's records that had fooled a reader.

This task is that list, turned into a repository. All data is synthetic. No real
account data, keys or API calls are in it.

## The task, version 3

fillbook 0.4 keeps a ledger of the exchange's daily exports in a SQLite store.
Production has been running it, and its store is wrong. The solver has to make
ingest, report and reconcile correct, keep them correct as exports keep
arriving (some twice, some late, some correcting earlier ones), and repair the
store 0.4 already wrote without re-ingesting anything.

## The traps

In the parsing, carried over from versions 1 and 2:

| Trap | Effect if missed |
|---|---|
| `action` is the ticket verb, not open or close | NO entries booked as income |
| NO fills priced from `yes_price` | cost of every NO fill wrong |
| WebSocket and archived pages use v1: dollars, `quantity` | fills count as zero |
| Pagination stops at five pages | late pages vanish |
| Identical partial fills with different `fill_id` | value-based dedup drops real fills |
| Pair netting credits $1 per pair at fill time | early closes scored as losses |
| Voided markets pay `settlement_value` | wrong payout |
| `by_day` keyed on UTC settle date | evening markets land on the next day |
| A unit test encodes a record shape the exchange never emits | a solver who trusts the tests keeps the bug |

In the ledger, new in version 3:

| Trap | Effect if missed |
|---|---|
| The same fill appears in two exports (REST windows overlap) | double-counted cost |
| An export is delivered twice | everything in it counted twice |
| A bust arrives in a later export than its fill | cancelled trades stay in |
| A bust after settlement reissues the settlement row (`revision`) | the cross-check fails on clean stores |
| A missed export is backfilled after later ones | corrections applied before the thing they correct are lost |
| The production store was written by 0.4 | three days of history wrong, or lost on upgrade |
| Statements must chain day to day | a missing export goes unnoticed |

## Grader

- **Truth.** It comes from the simulator's own record of every fill, bust and
  settlement, replayed to each checkpoint's time. The grader never parses an
  export, so it shares no code path with any solution.
- **The legacy store.** For every seed the grader builds the store with the
  real 0.4 code (`grader/legacy/`, identical to `starter/fillbook`, and a
  self-test checks that). That's the same way production was left.
- **The sequence.** The solution ingests the remaining exports with one
  redelivered and two swapped. `report` is scored at every point where the
  ingested exports form an unbroken run, starting with the untouched 0.4
  store. That gives 56 checkpoints across six seeds.
- **Reconcile.** It runs on six clean stores and twelve defective ones covering
  six defect kinds. Its score is the clean pass rate times the catch rate.
- **Determinism.** Fixed seeds, integer cents, no clock, no network. A full
  run of one solution takes about 30 seconds.

## Calibration

A Polaris task should be solvable, should not be trivial, and should separate
stronger attempts from weaker ones.

- **Solvable.** `solutions/reference` scores 100.
- **Plausible mistakes land across the range.** The in-order ledger, the
  natural incremental design, scores 89.0. Starting the store over on upgrade
  scores 34.7. See the table in `README.md`.
- **It does not separate frontier models.** Six attempts by Claude Sonnet and
  Claude Opus across three versions all scored 100.
  - **Version 1** put every trap in the visible sample.
  - **Version 2** added behavior the field reference documents but the sample
    never shows. The models read the reference closely and handled it anyway.
  - **Version 3** added state across runs, late corrections, redelivery,
    backfill and a migration. Solve time rose from about 1.5 minutes to about
    6, but both models still scored 100.

  Both version 3 attempts saw the same shortcut: 0.4 archived every raw
  export, so recomputing everything from the archive makes ingest order
  irrelevant and the migration nearly free. Taking away the archive would make the
  migration impossible, which wouldn't be fair.

**What I take from it.** A well-specified data-correctness task of a few
hundred lines doesn't separate current frontier models, however many traps it
holds. Careful reading of a precise spec is now a strength of these models. The
next things I would try are the ones this format can't hold:
- **A much larger existing codebase**, where finding the right place to change
  is the work.
- **A spec with real gaps**, where the solver has to ask, or choose and say why.
- **Wall-clock and resource limits** that rule out recomputing everything.

Those are the conditions Polaris tasks are built around. This repository is
the grader, the anti-gaming work and the calibration method, run honestly on
a task that turned out easier than I designed it to be.
