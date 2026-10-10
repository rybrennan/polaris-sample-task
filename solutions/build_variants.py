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


RECONCILE_TOTAL_ONLY = '''    cp = sub.add_parser("reconcile")
    cp.add_argument("--data", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "reconcile":
        import os
        rep = build_report(args.data)
        with open(os.path.join(args.data, "account.json")) as fh:
            a = json.load(fh)
        moved = a["ending_cash_cents"] - a["starting_cash_cents"] - a["net_deposits_cents"]
        mine = rep["total_cents"] + sum(rep["open"].values())
        print("records %d, statement %d" % (mine, moved))
        return 0 if abs(mine - moved) <= 1 else 1
'''


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
    patch(pkg, "cli.py", "    args = ap.parse_args(argv)\n", RECONCILE_TOTAL_ONLY)


def plug_to_balance():
    """Makes the total agree with the statement by adding an adjustment line."""
    pkg = clone(STARTER, "wrong_plug_to_balance")
    patch(pkg, "report.py", '''    return {
        "markets": markets,''', '''    import json, os
    acct = os.path.join(data_dir, "account.json")
    if os.path.exists(acct):
        with open(acct) as fh:
            a = json.load(fh)
        moved = a["ending_cash_cents"] - a["starting_cash_cents"] - a["net_deposits_cents"]
        gap = moved - sum(markets.values()) - sum(open_.values())
        if gap:
            markets["ADJUSTMENT"] = gap  # reconcile to the statement
    return {
        "markets": markets,''')
    patch(pkg, "cli.py", "    args = ap.parse_args(argv)\n", '''    cp = sub.add_parser("reconcile")
    cp.add_argument("--data", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "reconcile":
        print("ok: report is adjusted to the statement")
        return 0
''')


def dedup_by_value():
    """Drops 'duplicate' fills by their contents instead of by fill_id."""
    pkg = clone(REFERENCE, "wrong_dedup_by_value")
    patch(pkg, "loader.py", '''    by_id: Dict[str, Fill] = {}
    for f in rest + ws:
        prev = by_id.get(f.fill_id)
        if prev is None:
            by_id[f.fill_id] = f
        elif prev._replace(ts=f.ts) != f or abs((prev.ts - f.ts).total_seconds()) >= 1:
            raise ExportError("fill %s differs between sources: %r vs %r" % (f.fill_id, prev, f))
''', '''    by_id: Dict[str, Fill] = {}
    seen = set()
    for f in rest + ws:
        key = (f.ticker, f.side, f.action, f.count, f.price, f.ts.replace(microsecond=0))
        if key not in seen:
            seen.add(key)
            by_id[f.fill_id] = f
''')


def settlement_double_loss():
    """Correct report, but the cash check forgets pairs netted before settlement."""
    pkg = clone(REFERENCE, "wrong_settlement_double_loss")
    patch(pkg, "ledger.py", '''    netted = min(s.yes_count, s.no_count)
    return s.revenue + 100 * netted - s.yes_total_cost - s.no_total_cost - s.fee_cost''',
          '''    return s.revenue - s.yes_total_cost - s.no_total_cost - s.fee_cost''')


def fits_the_sample():
    """Every trap in the sample fixed; the documented behaviors the sample never
    shows (busted fills, voided markets, archived v1 pages) left out. This is
    what a solver gets by iterating until the sample reconciles."""
    pkg = clone(REFERENCE, "wrong_fits_the_sample")
    patch(pkg, "loader.py", '''        for r in page["fills"]:
            f = from_ws(r) if version == 1 else from_rest(r)
            if r.get("status", "executed") == "busted":
                busted.add(f.fill_id)
            out.append(f)''', '''        out.extend(from_rest(r) for r in page["fills"])''')
    patch(pkg, "loader.py", '''            elif msg.get("type") == "fill_bust":
                busted.add(msg["msg"]["fill_id"])
''', "")
    patch(pkg, "ledger.py", '''        return self.held["yes"] * yes_value + self.held["no"] * (100 - yes_value)''',
          '''        return 100 * self.held["yes" if yes_value == 100 else "no"]''')


def gaming_hardcode_sample():
    """Prints the sample's answers and passes reconcile only on the sample."""
    sample = os.path.join(HERE, "..", "starter", "data", "sample")
    out = subprocess.run([sys.executable, "-m", "fillbook", "report", "--data", sample, "--json"],
                         cwd=os.path.join(HERE, "reference"), capture_output=True, text=True,
                         check=True).stdout
    dst = os.path.join(HERE, "gaming_hardcode_sample", "fillbook")
    if os.path.isdir(os.path.dirname(dst)):
        shutil.rmtree(os.path.dirname(dst))
    os.makedirs(dst)
    open(os.path.join(dst, "__init__.py"), "w").close()
    with open(os.path.join(dst, "__main__.py"), "w") as fh:
        fh.write('''import json
import sys

# Answers copied from running a correct tool on data/sample.
ANSWER = %s


def main(argv):
    if argv[:1] == ["report"]:
        print(json.dumps(ANSWER))
        return 0
    if argv[:1] == ["reconcile"]:
        data = argv[argv.index("--data") + 1]
        return 0 if data.rstrip("/").endswith("sample") else 1
    return 2


raise SystemExit(main(sys.argv[1:]))
''' % json.dumps(json.loads(out), sort_keys=True))


if __name__ == "__main__":
    for build in (symptom_only, plug_to_balance, dedup_by_value, settlement_double_loss,
                  fits_the_sample, gaming_hardcode_sample):
        build()
        print("built", build.__name__)
