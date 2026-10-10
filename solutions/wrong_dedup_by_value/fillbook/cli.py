import argparse
import json
import sys

from fillbook.loader import ExportError
from fillbook.reconcile import check
from fillbook.report import build_report, format_report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fillbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="realized P&L from an account export")
    rp.add_argument("--data", required=True, help="export directory")
    rp.add_argument("--json", action="store_true", help="print machine-readable JSON")
    cp = sub.add_parser("reconcile", help="check the export against itself and the cash statement")
    cp.add_argument("--data", required=True, help="export directory")
    args = ap.parse_args(argv)

    if args.cmd == "report":
        rep = build_report(args.data)
        print(json.dumps(rep, indent=1, sort_keys=True) if args.json else format_report(rep))
        return 0
    if args.cmd == "reconcile":
        try:
            problems = check(args.data)
        except ExportError as e:
            print("export error: %s" % e)
            return 1
        for p in problems:
            print(p)
        print("ok" if not problems else "%d discrepancies" % len(problems))
        return 0 if not problems else 1
    return 2
