# fillbook

Turns an exchange account export into a P&L report.

```bash
python3 -m fillbook report --data data/sample          # table by day
python3 -m fillbook report --data data/sample --json   # machine-readable
python3 -m unittest                                    # tests
```

Python 3.9+, standard library only.

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
- `open`: every market with fills and no settlement yet, with the net cash it has
  moved so far (negative when money went in).
- `by_day`: realized P&L of settled markets, grouped by event date.

All values are integer cents.

## Layout

```
fillbook/loader.py   read the export into Fill and Settlement objects
fillbook/ledger.py   positions and P&L per market
fillbook/report.py   build and format the report
fillbook/cli.py      command line
docs/exchange_api.md the exchange's field reference
data/sample/         a real-shaped export (synthetic account)
```
