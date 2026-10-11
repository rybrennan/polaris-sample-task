"""The ledger store: one SQLite file that every daily export is ingested into."""

import os
import sqlite3

from fillbook.loader import load_account, load_fills, load_settlements

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- every file of every export, as received, so nothing is ever lost
CREATE TABLE IF NOT EXISTS raw_records (
    export_id TEXT, kind TEXT, name TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT, ticker TEXT, side TEXT, action TEXT,
    count INTEGER, price REAL, fee REAL, ts TEXT
);
CREATE TABLE IF NOT EXISTS settlements (
    ticker TEXT, result TEXT, settled_time TEXT, revenue INTEGER
);
CREATE TABLE IF NOT EXISTS statements (
    export_id TEXT, starting_cash INTEGER, ending_cash INTEGER, net_deposits INTEGER
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    if conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() is None:
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    return conn


def archive(conn: sqlite3.Connection, export_id: str, export_dir: str) -> None:
    fills_dir = os.path.join(export_dir, "fills")
    for name in sorted(os.listdir(fills_dir)):
        if name.endswith(".json"):
            with open(os.path.join(fills_dir, name)) as fh:
                conn.execute("INSERT INTO raw_records VALUES (?, 'rest_page', ?, ?)",
                             (export_id, name[:-5], fh.read()))
    ws = os.path.join(export_dir, "ws_fills.jsonl")
    if os.path.exists(ws):
        with open(ws) as fh:
            for i, line in enumerate(fh):
                if line.strip():
                    conn.execute("INSERT INTO raw_records VALUES (?, 'ws', ?, ?)",
                                 (export_id, str(i), line.strip()))
    for kind in ("settlements", "account"):
        with open(os.path.join(export_dir, kind + ".json")) as fh:
            conn.execute("INSERT INTO raw_records VALUES (?, ?, ?, ?)",
                         (export_id, kind, kind, fh.read()))


def ingest(conn: sqlite3.Connection, export_dir: str) -> str:
    export_id = os.path.basename(os.path.normpath(export_dir))
    archive(conn, export_id, export_dir)
    for f in load_fills(export_dir):
        conn.execute("INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (f.fill_id, f.ticker, f.side, f.action, f.count, f.price, f.fee,
                      f.ts.isoformat()))
    for s in load_settlements(export_dir):
        conn.execute("INSERT INTO settlements VALUES (?, ?, ?, ?)",
                     (s.ticker, s.result, s.settled_time.isoformat(), s.revenue))
    a = load_account(export_dir)
    conn.execute("INSERT INTO statements VALUES (?, ?, ?, ?)",
                 (export_id, a["starting_cash_cents"], a["ending_cash_cents"],
                  a["net_deposits_cents"]))
    conn.commit()
    return export_id
