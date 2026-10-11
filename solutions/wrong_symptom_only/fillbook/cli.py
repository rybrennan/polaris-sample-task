import argparse
import json

from fillbook.report import build_report, format_report
from fillbook.store import connect, ingest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fillbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("ingest", help="add one daily export to the store")
    ip.add_argument("--store", required=True, help="SQLite store file")
    ip.add_argument("--export", required=True, help="export directory")
    rp = sub.add_parser("report", help="realized P&L from the store")
    rp.add_argument("--store", required=True, help="SQLite store file")
    rp.add_argument("--json", action="store_true", help="print machine-readable JSON")
    cp = sub.add_parser("reconcile")
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
        return 0 if abs(mine - moved) <= 1 else 1
    if args.cmd == "ingest":
        print("ingested", ingest(conn, args.export))
        return 0
    if args.cmd == "report":
        rep = build_report(conn)
        print(json.dumps(rep, indent=1, sort_keys=True) if args.json else format_report(rep))
        return 0
    return 2
