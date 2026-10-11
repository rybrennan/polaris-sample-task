# Task: make fillbook's ledger match the exchange

You are working in the `fillbook` repository (this directory). fillbook keeps a
ledger of the exchange's daily account exports in a SQLite store and reports
realized P&L from it.

Production has been running fillbook 0.4. Its store, `data/store.db`, holds the
first three exports in `data/exports/`. Finance flagged it:
`python3 -m fillbook report --store data/store.db` says the account realized
**$483.44** over those three days. The cash statements in those exports say the
balance went from $1,550.96 to $1,580.82 with no deposits, so trading moved
about **$30**. Both cannot be right.

Make fillbook correct, and keep it correct as exports keep arriving.

1. **`ingest --store DB --export DIR`** adds one daily export to the store. The
   exchange delivers at least once, so an export can arrive twice, and a missed
   export can be backfilled after later ones. Later exports can correct earlier
   ones.

2. **`report --store DB [--json]`** reports on everything ingested so far. Keep
   the output schema in `README.md`. Whenever the ingested exports form an
   unbroken run of days from the first, the report must be exact to the cent.
   It must be computed from fills and settlements; the cash statements are for
   `reconcile`.

3. **`reconcile --store DB`** exits 0 when the store is consistent and 1 when
   it is not, and prints one line per discrepancy. Consistent means all of
   these hold:
   - for every settled market, the fills agree with that market's latest
     settlement row on every field the row carries;
   - the cash statements form an unbroken run of days, each starting where the
     previous one ended;
   - the first starting cash plus all net deposits plus all cash moved by
     trading equals the last ending cash, to the cent.

   It must pass a correct store. It must catch a missing or extra fill, a
   settlement row that is altered or missing, a wrong statement, or a missing
   export.

4. **Stores written by 0.4 must keep working.** Production will run your
   version against `data/store.db` as it is, without re-ingesting anything.

The exchange's field reference is `docs/exchange_api.md`. Where it and the code
disagree, the document is right. Your work will be run against other accounts'
exports and stores, not only the sample. Use the standard library only, Python
3.9 or later.
