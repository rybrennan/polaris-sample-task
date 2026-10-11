"""The ledger store: one SQLite file that every daily export is ingested into.

``raw_records`` archives every file of every export exactly as received (this
has not changed since 0.4). The ledger tables ``fills``, ``settlements`` and
``statements`` are derived from that archive and rebuilt in full after every
ingest, so redelivered, backfilled and corrected exports all land the same way
regardless of arrival order:

- one delivery per export day is used: the one with the latest ``exported_at``
  (ties: the one ingested last);
- a fill keeps the fields from the latest export day that reported it, and is
  busted if any export reported it busted;
- every settlement revision is kept; the highest revision is the live one.

Stores written by 0.4 (schema_version 1) are migrated on open: their derived
tables were wrong (prices in mixed units, duplicate fills, busts ignored), so
they are renamed to ``legacy_v1_*`` and rebuilt from ``raw_records``.
"""

import os
import sqlite3
from collections import OrderedDict
from typing import Dict, List, Tuple

from fillbook.loader import Export, Fill, Record, Settlement, parse_export, read_export_dir

SCHEMA_VERSION = 2

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- every file of every export, as received, so nothing is ever lost
CREATE TABLE IF NOT EXISTS raw_records (
    export_id TEXT, kind TEXT, name TEXT, payload TEXT
);
"""

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, order_id TEXT, ticker TEXT, side TEXT, action TEXT,
    count INTEGER, price INTEGER, fee INTEGER, ts TEXT, busted INTEGER,
    export_date TEXT
);
CREATE TABLE IF NOT EXISTS settlements (
    ticker TEXT, revision INTEGER, market_result TEXT, settled_time TEXT,
    yes_count INTEGER, no_count INTEGER, yes_total_cost INTEGER, no_total_cost INTEGER,
    fee_cost INTEGER, settlement_value INTEGER, revenue INTEGER, export_date TEXT,
    PRIMARY KEY (ticker, revision)
);
CREATE TABLE IF NOT EXISTS statements (
    export_date TEXT PRIMARY KEY, starting_cash INTEGER, ending_cash INTEGER,
    net_deposits INTEGER, exported_at TEXT, export_id TEXT
);
"""


def _version(conn: sqlite3.Connection):
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return None if row is None else int(row[0])


def _tables(conn: sqlite3.Connection):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(BASE_SCHEMA)
    version = _version(conn)
    if version is None or version < SCHEMA_VERSION:
        with conn:  # one transaction: the migration lands whole or not at all
            conn.execute("BEGIN")
            if version is not None and version < 2:
                tables = _tables(conn)
                for t in ("fills", "settlements", "statements"):
                    if t in tables:
                        conn.execute("ALTER TABLE %s RENAME TO legacy_v1_%s" % (t, t))
            for stmt in LEDGER_SCHEMA.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            rebuild(conn)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
    elif version > SCHEMA_VERSION:
        raise RuntimeError("store schema %d is newer than this fillbook (%d)"
                           % (version, SCHEMA_VERSION))
    return conn


def deliveries(conn: sqlite3.Connection) -> List[Tuple[str, List[Record]]]:
    """Split raw_records into deliveries, in ingest order.

    Every ingest (0.4 and later) writes one export's records with its
    account record last, inside a single transaction.
    """
    out = []
    pending: Dict[str, List[Record]] = {}
    for export_id, kind, name, payload in conn.execute(
            "SELECT export_id, kind, name, payload FROM raw_records ORDER BY rowid"):
        pending.setdefault(export_id, []).append((kind, name, payload))
        if kind == "account":
            out.append((export_id, pending.pop(export_id)))
    return out


def current_exports(conn: sqlite3.Connection) -> List[Tuple[str, Export]]:
    """The delivery in force for each export day, oldest day first."""
    best: Dict[str, Tuple[tuple, str, Export]] = {}
    for order, (export_id, records) in enumerate(deliveries(conn)):
        exp = parse_export(records)
        key = (exp.exported_at, order)
        cur = best.get(exp.export_date)
        if cur is None or key > cur[0]:
            best[exp.export_date] = (key, export_id, exp)
    return [(eid, exp) for _, eid, exp in sorted(best.values(),
                                                 key=lambda v: (v[2].export_date, v[0]))]


def rebuild(conn: sqlite3.Connection) -> None:
    """Recompute the ledger tables from raw_records. Caller commits."""
    exports = current_exports(conn)
    fills: "OrderedDict[str, Tuple[Fill, str]]" = OrderedDict()
    busted = set()
    rows: Dict[Tuple[str, int], Tuple[Settlement, str]] = {}
    for _, exp in exports:
        for f in exp.fills.values():
            fills[f.fill_id] = (f, exp.export_date)
        busted |= exp.busted
        for s in exp.settlements:
            rows[(s.ticker, s.revision)] = (s, exp.export_date)

    conn.execute("DELETE FROM fills")
    conn.execute("DELETE FROM settlements")
    conn.execute("DELETE FROM statements")
    conn.executemany(
        "INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(f.fill_id, f.order_id, f.ticker, f.side, f.action, f.count, f.price, f.fee,
          f.ts.isoformat(), int(f.fill_id in busted), day)
         for f, day in fills.values()])
    conn.executemany(
        "INSERT INTO settlements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(s.ticker, s.revision, s.result, s.settled_time.isoformat(), s.yes_count,
          s.no_count, s.yes_total_cost, s.no_total_cost, s.fee_cost, s.settlement_value,
          s.revenue, day) for s, day in rows.values()])
    conn.executemany(
        "INSERT INTO statements VALUES (?, ?, ?, ?, ?, ?)",
        [(exp.export_date, int(exp.account["starting_cash_cents"]),
          int(exp.account["ending_cash_cents"]), int(exp.account.get("net_deposits_cents", 0)),
          exp.exported_at.isoformat(), eid) for eid, exp in exports])


def ingest(conn: sqlite3.Connection, export_dir: str) -> str:
    export_id = os.path.basename(os.path.normpath(export_dir))
    records = read_export_dir(export_dir)
    parse_export(records)  # refuse a malformed export before it touches the store
    with conn:
        already = any(eid == export_id and sorted(recs) == sorted(records)
                      for eid, recs in deliveries(conn))
        if not already:  # an identical redelivery adds nothing
            conn.executemany("INSERT INTO raw_records VALUES (?, ?, ?, ?)",
                             [(export_id, k, n, p) for k, n, p in records])
        rebuild(conn)
    return export_id
