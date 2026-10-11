# fillbook

Keeps a ledger of the exchange's daily account exports and reports P&L.

```bash
python3 -m fillbook ingest --store data/store.db --export data/exports/2026-08-28
python3 -m fillbook report --store data/store.db          # table by day
python3 -m fillbook report --store data/store.db --json   # machine-readable
python3 -m unittest                                       # tests
```

Python 3.9+, standard library only. The store is one SQLite file.

## Report JSON

```json
{
 "markets":     {"<ticker>": <realized cents>, ...},
 "open":        {"<ticker>": <net cash moved so far, cents>, ...},
 "total_cents": <sum of markets>,
 "by_day":      {"YYYY-MM-DD": <realized cents>, ...}
}
```

- `markets`: every settled market, with its realized P&L.
- `open`: every market with live fills and no settlement yet, with the net cash
  it has moved so far (negative when money went in).
- `by_day`: realized P&L of settled markets, grouped by event date.

All values are integer cents.

## Layout

```
fillbook/loader.py   read one export directory
fillbook/store.py    the SQLite store and ingest
fillbook/ledger.py   positions and P&L per market
fillbook/report.py   build and format the report
fillbook/cli.py      command line
docs/exchange_api.md the exchange's field reference
data/exports/        ten daily exports (synthetic account)
data/store.db        the production store, built by 0.4 from the first three exports
```
