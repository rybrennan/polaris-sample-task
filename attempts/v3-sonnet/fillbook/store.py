"""The ledger store: one SQLite file that every daily export is ingested into.

`raw_records` holds every file of every export exactly as received and is the
source of truth. Reports are computed from it (see `load_state`), so a store
written by an older version stays correct; `fills`, `settlements` and
`statements` are a convenience copy rebuilt on every ingest.
"""

import json
import os
import sqlite3
from typing import Dict, List, NamedTuple

from fillbook import loader
from fillbook.loader import Fill, Settlement

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


# ---------------------------------------------------------------- ingest

def _read(path):
    with open(path) as fh:
        return fh.read()


def _export_records(export_id: str, export_dir: str):
    recs = []
    fills_dir = os.path.join(export_dir, "fills")
    if os.path.isdir(fills_dir):
        for name in sorted(os.listdir(fills_dir)):
            if name.endswith(".json"):
                recs.append((export_id, "rest_page", name[:-5], _read(os.path.join(fills_dir, name))))
    ws = os.path.join(export_dir, "ws_fills.jsonl")
    if os.path.exists(ws):
        for i, line in enumerate(_read(ws).splitlines()):
            if line.strip():
                recs.append((export_id, "ws", str(i), line.strip()))
    for kind in ("settlements", "account"):       # account is last: it closes the batch
        recs.append((export_id, kind, kind, _read(os.path.join(export_dir, kind + ".json"))))
    return recs


def ingest(conn: sqlite3.Connection, export_dir: str) -> str:
    """Add one export. Re-delivery of the same export replaces the earlier copy."""
    export_id = os.path.basename(os.path.normpath(export_dir))
    recs = _export_records(export_id, export_dir)       # read everything before touching the store
    for r in recs:                                       # reject unparseable files up front
        if r[1] in ("settlements", "account", "rest_page"):
            json.loads(r[3])
    with conn:
        conn.execute("DELETE FROM raw_records WHERE export_id = ?", (export_id,))
        conn.executemany("INSERT INTO raw_records VALUES (?, ?, ?, ?)", recs)
        rebuild_tables(conn)
    return export_id


def rebuild_tables(conn: sqlite3.Connection) -> None:
    st = load_state(conn)
    conn.execute("DELETE FROM fills")
    conn.execute("DELETE FROM settlements")
    conn.execute("DELETE FROM statements")
    conn.executemany("INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     [(f.fill_id, f.ticker, f.side, f.action, f.count, f.price, f.fee, f.ts.isoformat())
                      for f in st.fills if f.status != "busted"])
    conn.executemany("INSERT INTO settlements VALUES (?, ?, ?, ?)",
                     [(s.ticker, s.result, s.settled_time.isoformat(), s.revenue)
                      for rows in st.settlements.values() for s in rows])
    conn.executemany("INSERT INTO statements VALUES (?, ?, ?, ?)",
                     [(a["export_id"], a["starting_cash_cents"], a["ending_cash_cents"],
                       a["net_deposits_cents"]) for a in st.statements])


# ----------------------------------------------------------------- state

class State(NamedTuple):
    fills: List[Fill]                       # distinct fills; busted ones have status "busted"
    settlements: Dict[str, List[Settlement]]  # ticker -> rows, oldest revision first
    statements: List[dict]                  # one per export day, ordered by date
    problems: List[str]                     # data the loader could not use


def _batches(conn):
    """Group raw records into deliveries (an `account` record closes one); the
    latest delivery of an export day wins. Returns deliveries in arrival order."""
    cur, out = [], {}
    for rowid, export_id, kind, name, payload in conn.execute(
            "SELECT rowid, export_id, kind, name, payload FROM raw_records ORDER BY rowid"):
        cur.append((rowid, export_id, kind, name, payload))
        if kind == "account":
            try:
                key = loader.parse_ts(json.loads(payload)["export_date"] + "T00:00:00").date().isoformat()
            except Exception:
                key = export_id
            out.pop(key, None)
            out[key] = cur
            cur = []
    return list(out.values())


def load_state(conn: sqlite3.Connection) -> State:
    fills: Dict[str, tuple] = {}     # fill_id -> (rank, Fill)
    busted = set()
    settlements: Dict[str, List[tuple]] = {}
    statements = []
    problems: List[str] = []
    for bi, batch in enumerate(_batches(conn)):
        export_id = batch[-1][1]
        pages, ws_msgs = {}, []
        for _, _, kind, name, payload in batch:
            try:
                if kind == "rest_page":
                    pages[name] = json.loads(payload)
                elif kind == "ws":
                    m = loader.parse_ws_line(payload)
                    if m:
                        ws_msgs.append(m)
                elif kind == "settlements":
                    for n, r in enumerate(json.loads(payload)["settlements"]):
                        settlements.setdefault(r["ticker"], []).append(
                            (int(r.get("revision", 1)), bi, n, loader.parse_settlement(r)))
                elif kind == "account":
                    a = json.loads(payload)
                    statements.append({
                        "export_id": export_id,
                        "date": a.get("export_date") or export_id,
                        "starting_cash_cents": int(a["starting_cash_cents"]),
                        "ending_cash_cents": int(a["ending_cash_cents"]),
                        "net_deposits_cents": int(a.get("net_deposits_cents", 0)),
                    })
            except Exception as e:  # keep going, but never silently
                problems.append("%s: unreadable %s %s (%s)" % (export_id, kind, name, e))
        for rank, recs in ((0, [m["msg"] for m in ws_msgs if m.get("type") == "fill"]),
                           (1, loader.follow_pages(pages))):
            for r in recs:
                try:
                    f = loader.normalize(r)
                except Exception as e:
                    problems.append("%s: unreadable fill %r (%s)" % (export_id, r.get("fill_id"), e))
                    continue
                if f.status == "busted":
                    busted.add(f.fill_id)
                old = fills.get(f.fill_id)
                if old is None or (rank, bi) >= old[0]:
                    fills[f.fill_id] = ((rank, bi), f)
        for m in ws_msgs:
            if m.get("type") == "fill_bust" and isinstance(m.get("msg"), dict) and "fill_id" in m["msg"]:
                busted.add(m["msg"]["fill_id"])
    out_fills = []
    for fid, (_, f) in fills.items():
        out_fills.append(f._replace(status="busted") if fid in busted else f._replace(status="executed"))
    out_fills.sort(key=lambda f: (f.ts, f.fill_id))
    sett = {t: [s for _, _, _, s in sorted(rows, key=lambda r: r[:3])] for t, rows in settlements.items()}
    # one statement per day: the latest delivery wins
    by_date = {}
    for a in statements:
        by_date[a["date"]] = a
    return State(out_fills, sett, [by_date[d] for d in sorted(by_date)], problems)


def latest_settlements(st: State) -> Dict[str, Settlement]:
    return {t: _latest(rows) for t, rows in st.settlements.items()}


def _latest(rows):
    best = rows[0]
    for s in rows:            # rows are in arrival order; ties go to the later one
        if s.revision >= best.revision:
            best = s
    return best
