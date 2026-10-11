# Exchange account export: field reference

This is the part of the exchange's API documentation that covers the daily
account export. The exchange delivers one export per UTC day, in a directory
named for the date (`2026-09-04/`). An export directory holds:

```
fills/index.json      first page of REST fills, newest first
fills/<cursor>.json   later pages
ws_fills.jsonl        WebSocket capture of the live fill channel
settlements.json      settlement rows issued that day
account.json          cash statement for that day
```

Each export describes the account as the exchange saw it at the end of that day.
The REST fills reach back six hours into the previous day and stop two hours
before the export time, so a fill can appear in two exports. The WebSocket
capture covers the export's own day, except when the connection dropped.

Delivery is at-least-once. An export can be delivered again, and a missed export
can be backfilled after later ones.

## Markets

Each market is one yes/no question, for example `HIGHT-SFO-26SEP04-T71`: "Will the
high temperature in SFO on 2026-09-04 be 71°F or above?" The ticker segment
`26SEP04` is the **event date**. Markets settle after the event, often on the
next UTC day.

A YES contract pays $1.00 if the market resolves YES. A NO contract pays $1.00 if
it resolves NO. A market can also be **voided** (for example, when the weather
station's reading is withdrawn); see Settlements. Prices are quoted in cents, and
in any market `yes_price + no_price = 100`.

### One book per market

The exchange runs a single YES order book per market. A NO contract is the other
side of the same book. An order to sell YES executes as a purchase of NO at
`100 - price`. An order to sell NO executes as a purchase of YES at
`100 - price`.

Fills report the contract the account received, in `side`, and the verb on the
order ticket, in `action`.

### Pair netting

When an account holds YES and NO in the same market, the exchange nets the pairs
as soon as the fill lands. Both positions shrink by the number of pairs, and the
account is credited $1.00 per pair in cash. No record is written for the netting.

### Busted fills

The exchange can cancel ("bust") a fill after it executes, for example after a
pricing error. A busted fill never happened: it moves no cash, changes no
position and is not counted on the settlement row.

## REST fills (`fills/*.json`)

```json
{"api_version": 2, "fills": [ ... ], "cursor": "6a8a2ae2"}
```

Pages are newest first. Follow `cursor` to the page file of that name until
`cursor` is `null`.

| field | type | meaning |
|---|---|---|
| `fill_id` | string | unique per fill |
| `order_id` | string | the order ticket; one order can fill in several pieces |
| `ticker` | string | market |
| `side` | `"yes"` \| `"no"` | contract the account received |
| `action` | `"buy"` \| `"sell"` | verb on the order ticket |
| `count` | int | contracts |
| `yes_price` | int, cents | YES price of the fill |
| `no_price` | int, cents | NO price of the fill |
| `fee` | int, cents | exchange fee on this fill |
| `created_time` | ISO 8601, UTC | |
| `status` | `"executed"` \| `"busted"` | |

**Archived pages.** Pages holding only fills from before 2026-08-20 are served
from the archive with `"api_version": 1`. Their fill objects use the v1 wire
format described under WebSocket, plus `status`.

## WebSocket capture (`ws_fills.jsonl`)

One JSON message per line. The REST endpoint lags the live channel, and the
capture may have started late.

| `type` | meaning |
|---|---|
| `fill` | a fill, in the v1 wire format below |
| `fill_bust` | `{"fill_id": "...", "ts": ...}`: that fill was busted |
| anything else | ignore |

v1 wire format:

```json
{"type": "fill", "msg": {"fill_id": "...", "order_id": "...", "market_ticker": "...",
 "side": "no", "action": "sell", "quantity": 12, "price": "0.37", "fee": "0.02",
 "ts": 1788290000123}}
```

| field | meaning |
|---|---|
| `market_ticker` | market |
| `quantity` | contracts |
| `price` | price of `side`, in dollars, as a decimal string |
| `fee` | fee in dollars, as a decimal string |
| `ts` | epoch milliseconds |

## Settlements (`settlements.json`)

```json
{"settlements": [ ... ]}
```

| field | type | meaning |
|---|---|---|
| `ticker` | string | market |
| `revision` | int | 1 for the first row; higher numbers supersede lower ones |
| `market_result` | `"yes"` \| `"no"` \| `"void"` | resolution |
| `settled_time` | ISO 8601, UTC | when the market settled |
| `yes_count`, `no_count` | int | contracts of each side the account acquired over the market's life |
| `yes_total_cost`, `no_total_cost` | int, cents | total paid for those contracts |
| `fee_cost` | int, cents | total fees in this market |
| `settlement_value` | int, cents | what one YES contract pays at settlement: 100 if `yes`, 0 if `no`, set by the exchange if `void`. One NO contract pays `100 - settlement_value`. |
| `revenue` | int, cents | paid to the account at settlement |

When a fill in a settled market is busted, the exchange issues a new settlement
row for that market with the next `revision`, in the export for the day of the
bust.

## Account statement (`account.json`)

| field | meaning |
|---|---|
| `export_date` | the UTC day this export covers |
| `starting_cash_cents`, `ending_cash_cents` | cash balance at the start and end of that day |
| `net_deposits_cents` | deposits minus withdrawals that day |
| `exported_at` | export time, the end of that day |

All cash movement other than deposits comes from fills (cost and fees), pair
netting, settlement revenue, and the reversal of busted fills.
