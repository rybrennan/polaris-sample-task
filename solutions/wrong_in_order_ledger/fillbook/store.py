"""The ledger store.

Every export is kept exactly as received, and everything else is recomputed
from those raw records. That makes ingest order, redelivery and late
corrections a non-issue: the answer depends only on which exports are stored.

Schema history:
  1  fillbook 0.4: raw_records plus derived fills/settlements/statements tables.
     The derived tables were built by buggy parsing and are not trusted.
  2  fillbook 0.5: raw_records plus an exports index. Derived tables dropped.
"""

import hashlib
import json
import os
import sqlite3
from typing import Dict, List

SCHEMA_VERSION = 2


class Export:
    """One daily export's files, as received."""

    def __init__(self, export_id: str):
        self.export_id = export_id
        self.pages: Dict[str, str] = {}   # page name -> JSON text
        self.ws: Dict[int, str] = {}      # line number -> JSON text
        self.settlements = '{"settlements": []}'
        self.account = "{}"

    def records(self):
        for name, text in sorted(self.pages.items()):
            yield "rest_page", name, text
        for i, text in sorted(self.ws.items()):
            yield "ws", str(i), text
        yield "settlements", "settlements", self.settlements
        yield "account", "account", self.account

    def digest(self) -> str:
        h = hashlib.sha256()
        for rec in self.records():
            h.update(json.dumps(rec).encode())
        return h.hexdigest()

    def add(self, kind: str, name: str, payload: str) -> None:
        if kind == "rest_page":
            self.pages[name] = payload
        elif kind == "ws":
            self.ws[int(name)] = payload
        elif kind == "settlements":
            self.settlements = payload
        elif kind == "account":
            self.account = payload


def read_export_dir(export_dir: str) -> Export:
    exp = Export(os.path.basename(os.path.normpath(export_dir)))
    fills_dir = os.path.join(export_dir, "fills")
    for name in os.listdir(fills_dir):
        if name.endswith(".json"):
            with open(os.path.join(fills_dir, name)) as fh:
                exp.pages[name[:-5]] = fh.read()
    ws = os.path.join(export_dir, "ws_fills.jsonl")
    if os.path.exists(ws):
        with open(ws) as fh:
            for i, line in enumerate(fh):
                if line.strip():
                    exp.ws[i] = line.strip()
    with open(os.path.join(export_dir, "settlements.json")) as fh:
        exp.settlements = fh.read()
    with open(os.path.join(export_dir, "account.json")) as fh:
        exp.account = fh.read()
    return exp


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    version = int(row[0]) if row else None
    if version is None:
        conn.executescript("""
            CREATE TABLE raw_records (export_id TEXT, kind TEXT, name TEXT, payload TEXT);
            CREATE TABLE exports (export_id TEXT PRIMARY KEY, digest TEXT);
        """)
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    elif version == 1:
        migrate_v1(conn)
    elif version != SCHEMA_VERSION:
        raise SystemExit("store schema %d is newer than this fillbook" % version)
    return conn


def migrate_v1(conn: sqlite3.Connection) -> None:
    """Keep the raw records, rebuild the export index, drop the derived tables.

    0.4 had no duplicate check, so an export ingested twice has its raw rows
    twice. Loading keys rows by (kind, name), which collapses those copies.
    """
    with conn:
        conn.execute("CREATE TABLE exports (export_id TEXT PRIMARY KEY, digest TEXT)")
        for exp in load_exports(conn):
            conn.execute("INSERT INTO exports VALUES (?, ?)", (exp.export_id, exp.digest()))
        for table in ("fills", "settlements", "statements"):
            conn.execute("DROP TABLE IF EXISTS %s" % table)
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))


def ingest(conn: sqlite3.Connection, export_dir: str) -> str:
    exp = read_export_dir(export_dir)
    digest = exp.digest()
    row = conn.execute("SELECT digest FROM exports WHERE export_id = ?", (exp.export_id,)).fetchone()
    if row and row[0] == digest:
        return "%s already ingested, unchanged" % exp.export_id
    with conn:
        conn.execute("DELETE FROM raw_records WHERE export_id = ?", (exp.export_id,))
        conn.execute("DELETE FROM exports WHERE export_id = ?", (exp.export_id,))
        conn.executemany("INSERT INTO raw_records VALUES (?, ?, ?, ?)",
                         [(exp.export_id,) + rec for rec in exp.records()])
        conn.execute("INSERT INTO exports VALUES (?, ?)", (exp.export_id, digest))
    return "%s %s" % (exp.export_id, "replaced" if row else "ingested")


def load_exports(conn: sqlite3.Connection) -> List[Export]:
    exports: Dict[str, Export] = {}
    for export_id, kind, name, payload in conn.execute(
            "SELECT export_id, kind, name, payload FROM raw_records ORDER BY rowid"):
        exports.setdefault(export_id, Export(export_id)).add(kind, name, payload)
    return list(exports.values())  # in ingest order
