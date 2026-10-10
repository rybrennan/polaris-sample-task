import argparse
import json

from fillbook.report import build_report, format_report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fillbook")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="realized P&L from an account export")
    rp.add_argument("--data", required=True, help="export directory")
    rp.add_argument("--json", action="store_true", help="print machine-readable JSON")
    cp = sub.add_parser("reconcile")
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

    if args.cmd == "report":
        rep = build_report(args.data)
        print(json.dumps(rep, indent=1, sort_keys=True) if args.json else format_report(rep))
        return 0
    return 2
