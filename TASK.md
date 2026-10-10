# Task: make fillbook's P&L match the exchange

You are working in the `fillbook` repository (this directory). fillbook reads an
exchange account export and reports realized P&L.

Finance flagged the sample export in `data/sample`.
`python3 -m fillbook report --data data/sample` says the account realized
**$523.61** over the period. The account statement, `data/sample/account.json`,
says cash went from $1,457.99 to $1,370.20 with $150.00 of net withdrawals, so
trading moved about **$62** in total. Both cannot be right.

Do two things.

1. **Make `fillbook report` correct.** Keep the output schema in `README.md`.
   The report must be computed from the fills and settlements and must not read
   `account.json`. Every value is integer cents and must be exact.

2. **Add `python3 -m fillbook reconcile --data DIR`.** It exits 0 when the
   export is consistent and 1 when it is not, and prints one line per
   discrepancy. Consistent means both of these hold:
   - for every settled market, the fills agree with that market's settlement
     row on every field the row carries;
   - starting cash plus net deposits plus all cash moved by trading equals
     ending cash, to the cent.

   It must pass a clean export. It must catch an export with a missing or extra
   fill, a settlement row that is altered or missing, or a wrong statement.

The exchange's field reference is `docs/exchange_api.md`. Where it and the code
disagree, the document is right. Your work will be run against other exports
from the same exchange, not only the sample. Use the standard library only,
Python 3.9 or later.
