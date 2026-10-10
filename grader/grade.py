"""Grade a fillbook solution. Deterministic, standard library only.

    python3 grader/grade.py path/to/solution          # one solution, breakdown
    python3 grader/grade.py path/to/solution --json   # machine-readable
    python3 grader/grade.py --all                     # starter + every solutions/* dir

A solution is a directory containing a `fillbook/` package. Only that package
is copied into a clean temp directory; nothing else the solver wrote is used.
Every dataset is generated fresh from a seed, so the visible sample is never
graded. Set POLARIS_SEEDS="1,2,3" to override the seeds.

Score, out of 100:
    40  settled markets: realized P&L exact to the cent, per market
    15  open markets: net cash moved, exact to the cent, per market
     5  total_cents exact
    10  by_day exact, per event day
    30  reconcile: (clean exports passed) x (tampered exports caught)
The reconcile term is a product, so a check that always passes or always fails
scores zero there.
"""

import copy
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sim  # noqa: E402

DEFAULT_SEEDS = [202, 303, 808, 909, 1111, 1212]
TAMPERS = ["drop_fill", "phantom_fill", "settlement_revenue", "drop_settlement",
           "ending_cash", "drop_open_fill"]
WEIGHTS = {"settled": 40, "open": 15, "total": 5, "by_day": 10, "reconcile": 30}
TIMEOUT = 60


# ---------------------------------------------------------------- tampering

def _tick(f):
    return f.get("ticker") or f["market_ticker"]


def _fill_ids_by_ticker(recs):
    """Executed (not busted) fill ids per market, across both sources."""
    busted = {l["msg"]["fill_id"] for l in recs["ws"] if l["type"] == "fill_bust"}
    out = {}
    for page in recs["rest"].values():
        for f in page["fills"]:
            if f["status"] == "busted":
                busted.add(f["fill_id"])
            out.setdefault(_tick(f), set()).add(f["fill_id"])
    for line in recs["ws"]:
        if line["type"] == "fill":
            out.setdefault(line["msg"]["market_ticker"], set()).add(line["msg"]["fill_id"])
    return {t: ids - busted for t, ids in out.items() if ids - busted}


def _remove_fill(recs, fill_id):
    for page in recs["rest"].values():
        page["fills"] = [f for f in page["fills"] if f["fill_id"] != fill_id]
    recs["ws"] = [l for l in recs["ws"] if l["type"] != "fill" or l["msg"]["fill_id"] != fill_id]


def tamper(recs, kind, rng):
    """Return a copy of the export with one realistic defect, and a description."""
    r = copy.deepcopy(recs)
    settled = [s["ticker"] for s in r["settlements"]]
    by_ticker = _fill_ids_by_ticker(r)
    if kind == "drop_fill":
        t = rng.choice([t for t in settled if t in by_ticker])
        fid = rng.choice(sorted(by_ticker[t]))
        _remove_fill(r, fid)
        return r, "fill %s of %s missing from both sources" % (fid, t)
    if kind == "drop_open_fill":
        open_ = sorted(set(by_ticker) - set(settled))
        if not open_:
            return tamper(recs, "drop_fill", rng)
        t = rng.choice(open_)
        fid = rng.choice(sorted(by_ticker[t]))
        _remove_fill(r, fid)
        return r, "fill %s of open market %s missing" % (fid, t)
    if kind == "phantom_fill":
        ok = lambda f: _tick(f) in settled and f["status"] == "executed"
        name = rng.choice(sorted(n for n, p in r["rest"].items() if any(map(ok, p["fills"]))))
        page = r["rest"][name]
        src = rng.choice([f for f in page["fills"] if ok(f)])
        dup = dict(src, fill_id="f-%012x" % rng.getrandbits(48))
        page["fills"].insert(page["fills"].index(src) + 1, dup)
        return r, "phantom copy of %s in page %s" % (src["fill_id"], name)
    if kind == "settlement_revenue":
        s = rng.choice(r["settlements"])
        delta = 100 * rng.randint(1, 3)
        s["revenue"] = s["revenue"] - delta if s["revenue"] >= delta else s["revenue"] + delta
        return r, "revenue of %s altered" % s["ticker"]
    if kind == "drop_settlement":
        s = rng.choice([s for s in r["settlements"] if s["revenue"] > 0])
        r["settlements"].remove(s)
        return r, "settlement row for %s missing" % s["ticker"]
    if kind == "ending_cash":
        r["account"]["ending_cash_cents"] += rng.choice([-1, 1]) * rng.randint(1, 50)
        return r, "ending cash off by a few cents"
    raise ValueError(kind)


# ---------------------------------------------------------------- running

def run(sol_pkg_root, args):
    try:
        p = subprocess.run([sys.executable, "-E", "-s", "-m", "fillbook"] + args,
                           cwd=sol_pkg_root, capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timeout"


def per_key(want, got):
    if not isinstance(got, dict):
        return 0.0
    keys = set(want) | set(got)
    if not keys:
        return 1.0
    return sum(1 for k in keys if k in want and k in got and got[k] == want[k]) / len(keys)


def grade(solution_dir, seeds):
    pkg = os.path.join(solution_dir, "fillbook")
    if not os.path.isdir(pkg):
        raise SystemExit("%s has no fillbook/ package" % solution_dir)
    work = tempfile.mkdtemp(prefix="polaris-grade-")
    sol_root = os.path.join(work, "sol")
    os.makedirs(sol_root)
    shutil.copytree(pkg, os.path.join(sol_root, "fillbook"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    parts = {"settled": [], "open": [], "total": [], "by_day": []}
    clean_pass, caught, tamper_log, errors = [], [], [], []
    try:
        for i, seed in enumerate(seeds):
            acct = sim.simulate(seed)
            recs = sim.records(acct)
            want = sim.truth(acct)

            # report: run without the cash statement, so it cannot be used to plug
            d = os.path.join(work, "r%d" % seed)
            sim.write(recs, d)
            os.remove(os.path.join(d, "account.json"))
            code, out, err = run(sol_root, ["report", "--data", d, "--json"])
            try:
                got = json.loads(out) if code == 0 else None
            except ValueError:
                got = None
            if got is None:
                errors.append("seed %d report: exit %s %s" % (seed, code, err.strip()[-200:]))
                got = {}
            parts["settled"].append(per_key(want["markets"], got.get("markets")))
            parts["open"].append(per_key(want["open"], got.get("open")))
            parts["total"].append(1.0 if got.get("total_cents") == want["total_cents"] else 0.0)
            parts["by_day"].append(per_key(want["by_day"], got.get("by_day")))

            # reconcile on the clean export
            d = os.path.join(work, "c%d" % seed)
            sim.write(recs, d)
            code, out, err = run(sol_root, ["reconcile", "--data", d])
            clean_pass.append(code == 0)

            # reconcile on two tampered exports
            rng = random.Random(seed * 31 + 7)
            for kind in (TAMPERS[(2 * i) % 6], TAMPERS[(2 * i + 1) % 6]):
                bad, what = tamper(recs, kind, rng)
                d = os.path.join(work, "t%d-%s" % (seed, kind))
                sim.write(bad, d)
                code, out, err = run(sol_root, ["reconcile", "--data", d])
                hit = code == 1 and "Traceback" not in err
                caught.append(hit)
                tamper_log.append({"seed": seed, "kind": kind, "defect": what, "caught": hit})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    frac = {k: mean(v) for k, v in parts.items()}
    clean_rate, catch_rate = mean(clean_pass), mean(caught)
    frac["reconcile"] = clean_rate * catch_rate
    score = sum(WEIGHTS[k] * frac[k] for k in WEIGHTS)
    return {
        "solution": os.path.relpath(solution_dir, ROOT),
        "score": round(score, 1),
        "components": {k: round(WEIGHTS[k] * frac[k], 1) for k in WEIGHTS},
        "reconcile_clean_pass_rate": round(clean_rate, 3),
        "reconcile_catch_rate": round(catch_rate, 3),
        "tampers": tamper_log,
        "errors": errors,
        "seeds": seeds,
    }


def table(results):
    head = "| solution | score | settled /40 | open /15 | total /5 | by_day /10 | reconcile /30 | clean passed | tampers caught |"
    rows = [head, "|" + "---|" * 9]
    for r in results:
        c = r["components"]
        rows.append("| `%s` | **%.1f** | %.1f | %.1f | %.1f | %.1f | %.1f | %d%% | %d%% |" % (
            r["solution"], r["score"], c["settled"], c["open"], c["total"], c["by_day"],
            c["reconcile"], 100 * r["reconcile_clean_pass_rate"], 100 * r["reconcile_catch_rate"]))
    return "\n".join(rows)


def main(argv):
    env = os.environ.get("POLARIS_SEEDS")
    seeds = [int(s) for s in env.split(",")] if env else DEFAULT_SEEDS
    if argv[:1] == ["--all"]:
        dirs = [os.path.join(ROOT, "starter")] + sorted(
            os.path.join(ROOT, "solutions", d) for d in os.listdir(os.path.join(ROOT, "solutions"))
            if os.path.isdir(os.path.join(ROOT, "solutions", d, "fillbook")))
        results = [grade(d, seeds) for d in dirs]
        print(table(results))
        return 0
    if not argv:
        print(__doc__)
        return 2
    res = grade(os.path.abspath(argv[0]), seeds)
    if "--json" in argv:
        print(json.dumps(res, indent=1))
    else:
        print("score %.1f / 100" % res["score"])
        for k, v in res["components"].items():
            print("  %-10s %5.1f / %d" % (k, v, WEIGHTS[k]))
        print("  reconcile: %d%% of clean exports passed, %d%% of tampered exports caught"
              % (100 * res["reconcile_clean_pass_rate"], 100 * res["reconcile_catch_rate"]))
        for t in res["tampers"]:
            print("    %-6s %-19s %s" % ("caught" if t["caught"] else "MISSED", t["kind"], t["defect"]))
        for e in res["errors"]:
            print("  error: " + e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
