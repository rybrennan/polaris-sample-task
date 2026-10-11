"""Rebuild the wrong and gaming solutions from `reference/` and `../starter/`.

Each variant is one plausible mistake, applied as a small text patch, so the
difference from a correct solution is easy to read.

    python3 solutions/build_variants.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STARTER = os.path.join(HERE, "..", "starter", "fillbook")
REFERENCE = os.path.join(HERE, "reference", "fillbook")


def clone(src, name):
    dst = os.path.join(HERE, name, "fillbook")
    if os.path.isdir(os.path.dirname(dst)):
        shutil.rmtree(os.path.dirname(dst))
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def patch(pkg, filename, old, new):
    path = os.path.join(pkg, filename)
    with open(path) as fh:
        text = fh.read()
    if old not in text:
        raise SystemExit("patch target not found in %s: %r" % (path, old[:60]))
    with open(path, "w") as fh:
        fh.write(text.replace(old, new, 1))


def in_order_ledger():
    """Processes exports in the order they were ingested and applies each
    correction to whatever it has seen so far. Right when exports arrive in
    order; wrong after a backfill, when a bust or a reissued settlement row
    arrives before the export it corrects."""
    pkg = clone(REFERENCE, "wrong_in_order_ledger")
    patch(pkg, "store.py", '''    return [exports[k] for k in sorted(exports)]''',
          '''    return list(exports.values())  # in ingest order''')
    patch(pkg, "book.py", '''        for fid in self.busted:
            self.fills.pop(fid, None)
''', "")
    patch(pkg, "book.py", '''                if r.get("status", "executed") == "busted":
                    self.busted.add(f.fill_id)
                self._add_fill(f)''', '''                if r.get("status", "executed") == "busted":
                    self.fills.pop(f.fill_id, None)
                    self.busted.add(f.fill_id)
                elif f.fill_id not in self.busted:
                    self._add_fill(f)''')
    patch(pkg, "book.py", '''            if msg.get("type") == "fill":
                self._add_fill(from_v1(msg["msg"]))
            elif msg.get("type") == "fill_bust":
                self.busted.add(msg["msg"]["fill_id"])''', '''            if msg.get("type") == "fill":
                if msg["msg"]["fill_id"] not in self.busted:
                    self._add_fill(from_v1(msg["msg"]))
            elif msg.get("type") == "fill_bust":
                self.fills.pop(msg["msg"]["fill_id"], None)''')
    patch(pkg, "book.py", '''            if prev is None or s.revision > prev.revision:
                self.settlements[s.ticker] = s''', '''            self.settlements[s.ticker] = s  # newest ingested wins''')


def fresh_start_on_upgrade():
    """Treats the 0.4 store as unusable and starts it over, losing every
    export 0.4 ingested."""
    pkg = clone(REFERENCE, "wrong_fresh_start_on_upgrade")
    patch(pkg, "store.py", '''    elif version == 1:
        migrate_v1(conn)''', '''    elif version == 1:
        # 0.4 data is wrong anyway: start the store over.
        with conn:
            for table in ("raw_records", "fills", "settlements", "statements"):
                conn.execute("DROP TABLE IF EXISTS %s" % table)
            conn.executescript("""
                CREATE TABLE raw_records (export_id TEXT, kind TEXT, name TEXT, payload TEXT);
                CREATE TABLE exports (export_id TEXT PRIMARY KEY, digest TEXT);
            """)
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'",
                         (str(SCHEMA_VERSION),))''')


def first_settlement_row():
    """Keeps the first settlement row it sees for a market and ignores reissues."""
    pkg = clone(REFERENCE, "wrong_first_settlement_row")
    patch(pkg, "book.py", '''            if prev is None or s.revision > prev.revision:''',
          '''            if prev is None:''')


def dedup_by_value():
    """Drops 'duplicate' fills by their contents instead of by fill_id."""
    pkg = clone(REFERENCE, "wrong_dedup_by_value")
    patch(pkg, "book.py", '''        prev = self.fills.get(f.fill_id)
        if prev is None:
            self.fills[f.fill_id] = f''', '''        key = (f.ticker, f.side, f.action, f.count, f.price, f.ts.replace(microsecond=0))
        if not hasattr(self, "_seen"):
            self._seen = {}
        if key in self._seen and self._seen[key] != f.fill_id:
            return
        self._seen[key] = f.fill_id
        prev = self.fills.get(f.fill_id)
        if prev is None:
            self.fills[f.fill_id] = f''')


def settlement_double_loss():
    """Correct report, but the cash check forgets pairs netted before settlement."""
    pkg = clone(REFERENCE, "wrong_settlement_double_loss")
    patch(pkg, "ledger.py", "return s.revenue + 100 * netted - spent",
          "return s.revenue - spent  # pairs netted early are forgotten")


def no_archived_pages():
    """Everything right except the archived v1 REST pages, which the visible
    exports never contain."""
    pkg = clone(REFERENCE, "wrong_no_archived_pages")
    patch(pkg, "book.py", '''                f = from_v1(r) if version == 1 else from_rest(r)''',
          '''                f = from_rest(r)''')


def symptom_only():
    """Fixes the bug the ticket points at (fill semantics, NO price, netting) and stops."""
    pkg = clone(STARTER, "wrong_symptom_only")
    patch(pkg, "ledger.py", '''        if f.action == "buy":
            self.qty[f.side] += f.count
            self.cost += f.count * f.price
        else:
            self.qty[f.side] -= f.count
            self.proceeds += f.count * f.price
        self.fees += f.fee
''', '''        # Per the exchange docs, every fill is a purchase of `side`; pairs net to $1.
        self.qty[f.side] += f.count
        self.cost += f.count * f.price
        self.fees += f.fee
        pairs = min(self.qty["yes"], self.qty["no"])
        self.qty["yes"] -= pairs
        self.qty["no"] -= pairs
        self.proceeds += 100 * pairs
''')
    patch(pkg, "loader.py", 'price=float(rec.get("yes_price", rec.get("price"))),',
          'price=float(rec["no_price"] if rec["side"] == "no" and "no_price" in rec\n'
          '                    else rec.get("yes_price", rec.get("price"))),')
    patch(pkg, "cli.py", '''    args = ap.parse_args(argv)

    conn = connect(args.store)''', '''    cp = sub.add_parser("reconcile")
    cp.add_argument("--store", required=True)
    args = ap.parse_args(argv)

    conn = connect(args.store)
    if args.cmd == "reconcile":
        rep = build_report(conn)
        rows = conn.execute("SELECT starting_cash, ending_cash, net_deposits FROM statements "
                            "ORDER BY export_id").fetchall()
        moved = rows[-1][1] - rows[0][0] - sum(r[2] for r in rows)
        mine = rep["total_cents"] + sum(rep["open"].values())
        print("records %d, statements %d" % (mine, moved))
        return 0 if abs(mine - moved) <= 1 else 1''')


def plug_to_statements():
    """Makes the total agree with the statements by adding an adjustment line."""
    pkg = clone(STARTER, "wrong_plug_to_statements")
    patch(pkg, "report.py", '''    return {
        "markets": markets,''', '''    rows = conn.execute("SELECT starting_cash, ending_cash, net_deposits FROM statements "
                        "ORDER BY export_id").fetchall()
    if rows:
        moved = rows[-1][1] - rows[0][0] - sum(r[2] for r in rows)
        gap = moved - sum(markets.values()) - sum(open_.values())
        if gap:
            markets["ADJUSTMENT"] = gap  # reconcile to the statements
    return {
        "markets": markets,''')
    patch(pkg, "cli.py", '''    args = ap.parse_args(argv)

    conn = connect(args.store)''', '''    cp = sub.add_parser("reconcile")
    cp.add_argument("--store", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "reconcile":
        print("ok: report is adjusted to the statements")
        return 0

    conn = connect(args.store)''')


def gaming_hardcode_sample():
    """Prints the answer for the visible store after all sample exports, and
    passes reconcile everywhere."""
    sample = os.path.join(HERE, "..", "starter", "data")
    work = tempfile.mkdtemp()
    try:
        store = os.path.join(work, "store.db")
        shutil.copy(os.path.join(sample, "store.db"), store)
        ref = os.path.join(HERE, "reference")
        for d in sorted(os.listdir(os.path.join(sample, "exports")))[3:]:
            subprocess.run([sys.executable, "-m", "fillbook", "ingest", "--store", store, "--export",
                            os.path.join(sample, "exports", d)], cwd=ref, check=True, capture_output=True)
        out = subprocess.run([sys.executable, "-m", "fillbook", "report", "--store", store, "--json"],
                             cwd=ref, capture_output=True, text=True, check=True).stdout
    finally:
        shutil.rmtree(work)
    dst = os.path.join(HERE, "gaming_hardcode_sample", "fillbook")
    if os.path.isdir(os.path.dirname(dst)):
        shutil.rmtree(os.path.dirname(dst))
    os.makedirs(dst)
    open(os.path.join(dst, "__init__.py"), "w").close()
    with open(os.path.join(dst, "__main__.py"), "w") as fh:
        fh.write('''import json
import sys

# Answer copied from running a correct tool over the visible sample exports.
ANSWER = %s


def main(argv):
    if argv[:1] == ["report"]:
        print(json.dumps(ANSWER))
        return 0
    if argv[:1] in (["ingest"], ["reconcile"]):
        return 0
    return 2


raise SystemExit(main(sys.argv[1:]))
''' % json.dumps(json.loads(out), sort_keys=True))


if __name__ == "__main__":
    for build in (in_order_ledger, fresh_start_on_upgrade, first_settlement_row, dedup_by_value,
                  settlement_double_loss, no_archived_pages, symptom_only, plug_to_statements,
                  gaming_hardcode_sample):
        build()
        print("built", build.__name__)
