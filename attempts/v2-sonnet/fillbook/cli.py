import argparse
import json

from fillbook.report import build_report, format_report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fillbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="realized P&L from an account export")
    rp.add_argument("--data", required=True, help="export directory")
    rp.add_argument("--json", action="store_true", help="print machine-readable JSON")
    cp = sub.add_parser("reconcile", help="check an export for consistency")
    cp.add_argument("--data", required=True, help="export directory")
    args = ap.parse_args(argv)

    if args.cmd == "report":
        rep = build_report(args.data)
        print(json.dumps(rep, indent=1, sort_keys=True) if args.json else format_report(rep))
        return 0
    if args.cmd == "reconcile":
        from fillbook.reconcile import reconcile
        problems = reconcile(args.data)
        for line in problems:
            print(line)
        if not problems:
            print("OK: export is consistent")
        return 1 if problems else 0
    return 2
