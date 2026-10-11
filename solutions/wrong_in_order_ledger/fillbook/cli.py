import argparse
import json

from fillbook.book import Book
from fillbook.reconcile import check
from fillbook.records import ExportError
from fillbook.report import build_report, format_report
from fillbook.store import connect, ingest, load_exports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fillbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("ingest", help="add one daily export to the store")
    ip.add_argument("--store", required=True, help="SQLite store file")
    ip.add_argument("--export", required=True, help="export directory")
    rp = sub.add_parser("report", help="realized P&L from the store")
    rp.add_argument("--store", required=True, help="SQLite store file")
    rp.add_argument("--json", action="store_true", help="print machine-readable JSON")
    cp = sub.add_parser("reconcile", help="check the store against itself and the statements")
    cp.add_argument("--store", required=True, help="SQLite store file")
    args = ap.parse_args(argv)

    conn = connect(args.store)
    if args.cmd == "ingest":
        print(ingest(conn, args.export))
        return 0
    try:
        book = Book(load_exports(conn))
    except ExportError as e:
        print("export error: %s" % e)
        return 1
    if args.cmd == "report":
        rep = build_report(book)
        print(json.dumps(rep, indent=1, sort_keys=True) if args.json else format_report(rep))
        return 0
    if args.cmd == "reconcile":
        problems = check(book)
        for p in problems:
            print(p)
        print("ok" if not problems else "%d discrepancies" % len(problems))
        return 0 if not problems else 1
    return 2
