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

## What makes it long-horizon

The bugs are layered, so fixing one exposes the next:

1. Fixing the meaning of `action` changes every NO position.
2. Reading WebSocket `quantity` correctly turns zero-count fills into real ones.
   Then the fills that appear in both sources start double-counting.
3. Deduplicating "obvious" duplicates by value deletes real partial fills that
   are identical except for `fill_id`.
4. The cash identity still fails until every REST page is read. The starter
   stops after five pages, and the sample has eight.
5. With every fill right, the settlement cross-check still raises false alarms,
   until it accounts for pairs netted before settlement.

No single file or test points at all of this. The solver has to read the
exchange's field reference, form a model of how cash moves, and check that
model against an independent number (the account statement).

## The traps, and where each lives in the starter

| Trap | Where | Effect if missed |
|---|---|---|
| `action` is the ticket verb, not open or close | `ledger.Position.apply` | NO entries booked as income |
| NO fills priced from `yes_price` | `loader.normalize` | cost of every NO fill wrong |
| WebSocket v1 uses dollars and `quantity` | `loader.normalize` | WS-only fills count as zero |
| Same fill in REST and WebSocket | `loader.load_fills` | double-counted cost |
| Identical partial fills with different `fill_id` | the data | value-based dedup drops real fills |
| Pagination stops at `MAX_PAGES = 5` | `loader.load_rest_fills` | oldest markets vanish (52 of 70 settled markets on the sample) |
| Pair netting credits $1 per pair at fill time | `ledger` | early closes scored as losses |
| `by_day` keyed on UTC settle date | `report.build_report` | evening markets land on the next day |
| A test encodes a record shape the exchange never emits | `tests/test_ledger.py::test_sell_books_proceeds` | a solver who trusts the tests keeps the bug |
| Settlement `revenue` excludes netted pairs | the data | naive cross-check flags clean markets |

Documented in the field reference, absent from the sample, present in the graded exports:

| Behavior | Effect if missed |
|---|---|
| Busted fills (`status: "busted"` in REST, `fill_bust` messages on the WebSocket) | cancelled trades counted as real |
| Voided markets (`market_result: "void"`, YES pays `settlement_value`) | crash on an unknown result, or the wrong payout |
| Archived REST pages (`api_version: 1`, v1 field names and dollar units) | crash, or zero-count fills |
| A settled market whose only fill was busted | market missing from the report |

## Grader

- **Truth.** It comes from the simulator's own cash ledger, written as trades
  happen. The grader never parses the exported records, so it shares no code
  path with any solution.
- **Partial credit.** Scoring is per market and per day, so the score shows how
  far a solution got. The reconcile term is a product of the pass rate on clean
  exports and the catch rate on tampered ones. See `GAMING.md`.
- **Determinism.** Fixed seeds, integer cents, no clock, no network. The full
  run takes about three seconds.

## Calibration

A Polaris task should be solvable, should not be trivial, and should separate
stronger attempts from weaker ones.

- **Solvable.** `solutions/reference` scores 100.
- **Plausible mistakes score where they should,** from 70.0 down to 0. See the
  results table in `README.md`.
- **Version 1 was too easy.** In the first version, every trap appeared in the
  visible sample and the hidden exports held nothing new. Claude Sonnet and
  Claude Opus both scored 100 in under three minutes. Their solutions are kept
  in `attempts/v1-*`. A model could iterate against the cash statement until the
  sample reconciled, without ever needing behavior it hadn't seen.
- **Version 2,** the current one, adds three behaviors the field reference
  documents but the sample never shows, and cuts the sentences in the reference
  that explained each trap outright. That tests the skill the first version
  missed: reading a spec and writing code that holds on data it hasn't seen.
  `solutions/wrong_fits_the_sample` is exactly the version 1 solution shape,
  and it scores 23.0.
- **Version 2 is still too easy for frontier models.** Claude Sonnet and Claude
  Opus both scored 100, each in about three minutes or less. Both read the field
  reference closely enough to handle behavior they never saw, including the
  market whose only fill was busted. The grader separates wrong solutions well,
  but the task does not yet separate strong models. Solutions are in
  `attempts/v2-*`.

## What would make it harder

The bugs here are each local to one function, and the whole repository is
about 300 lines. Fixing it takes reading, not sustained work. The next version
moves the difficulty to the places long-horizon work actually breaks:

- **State across runs.** Exports arrive daily into a persistent store, and
  `report` must be right after each one.
- **Late corrections.** Busts arrive days after the fill they cancel, and
  settlement rows arrive after the export that held the fills.
- **Idempotency.** One export is delivered twice, and another overlaps the one
  before it.
- **A migration.** The store already holds rows written by the buggy version,
  and they have to be repaired, not just recomputed.

The grader would replay a sequence of exports and score the report after every
step. Whether that version separates models is a guess until it is run.
