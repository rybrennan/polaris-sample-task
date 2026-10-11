"""Grade a fillbook solution. Deterministic, standard library only.

    python3 grader/grade.py path/to/solution          # one solution, breakdown
    python3 grader/grade.py path/to/solution --json   # machine-readable
    python3 grader/grade.py --all                     # starter + every solutions/* dir

A solution is a directory containing a `fillbook/` package. Only that package
is copied into a clean temp directory; nothing else the solver wrote is used.

For each seed the grader generates a fresh run of daily exports, then:
  1. builds a store with fillbook 0.4 (grader/legacy) from the first three
     exports, the way production was left;
  2. runs the solution's `report` on that store as it stands;
  3. ingests the remaining exports with the solution, redelivering one and
     swapping two out of order, and runs `report` after every step where the
     ingested exports form an unbroken run from the first;
  4. runs `reconcile` on the finished store, and on stores built the same way
     from exports with one defect each.

Score, out of 100:
    40  settled markets: realized P&L exact to the cent, per market, per checkpoint
    15  open markets: net cash moved, exact, per market, per checkpoint that has any
     5  total_cents exact, per checkpoint
    10  by_day exact, per event day, per checkpoint
    30  reconcile: (clean stores passed) x (defective stores caught)
Set POLARIS_SEEDS="1,2,3" to override the seeds.
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
LEGACY = os.path.join(HERE, "legacy")
sys.path.insert(0, HERE)
import sim  # noqa: E402

DEFAULT_SEEDS = [202, 303, 808, 909, 1111, 1212]
LEGACY_EXPORTS = 3
TAMPERS = ["drop_fill", "phantom_fill", "settlement_revenue", "drop_settlement",
           "statement_cash", "missing_export"]
WEIGHTS = {"settled": 40, "open": 15, "total": 5, "by_day": 10, "reconcile": 30}
TIMEOUT = 60


# ---------------------------------------------------------------- sequence

def ingest_plan(n):
    """Export indexes the solution ingests after the legacy ones: one
    redelivered, two swapped."""
    rest = list(range(LEGACY_EXPORTS, n))
    rest.insert(rest.index(LEGACY_EXPORTS + 1) + 1, LEGACY_EXPORTS + 1)  # redelivery
    i, j = rest.index(LEGACY_EXPORTS + 3), rest.index(LEGACY_EXPORTS + 4)
    rest[i], rest[j] = rest[j], rest[i]                                 # backfill
    return rest


# ---------------------------------------------------------------- tampering

def tamper(acct, exps, kind, rng):
    """Return (exports, skip_index, description) with one realistic defect."""
    e = copy.deepcopy(exps)
    n = len(e)
    middle = list(range(LEGACY_EXPORTS + 1, n - 1))
    if kind == "drop_fill":
        f = rng.choice([f for f in acct.fills if not f["busted"]])
        for x in e:
            for p in x["rest"].values():
                p["fills"] = [r for r in p["fills"] if r["fill_id"] != f["fill_id"]]
            x["ws"] = [l for l in x["ws"] if l["type"] != "fill" or l["msg"]["fill_id"] != f["fill_id"]]
        return e, None, "fill %s of %s missing from every export" % (f["fill_id"], f["ticker"])
    if kind == "phantom_fill":
        ok = lambda r: r["status"] == "executed"
        k = rng.choice([k for k in range(n) if any(ok(r) for p in e[k]["rest"].values() for r in p["fills"])])
        name = rng.choice(sorted(nm for nm, p in e[k]["rest"].items() if any(map(ok, p["fills"]))))
        page = e[k]["rest"][name]
        src = rng.choice([r for r in page["fills"] if ok(r)])
        page["fills"].insert(page["fills"].index(src) + 1,
                             dict(src, fill_id="f-%012x" % rng.getrandbits(48)))
        return e, None, "phantom copy of %s in export %s" % (src["fill_id"], e[k]["id"])
    if kind == "settlement_revenue":
        m = rng.choice(acct.markets)
        last = None
        for x in e:
            for row in x["settlements"]:
                if row["ticker"] == m.ticker:
                    last = row
        delta = 100 * rng.randint(1, 3)
        last["revenue"] = last["revenue"] - delta if last["revenue"] >= delta else last["revenue"] + delta
        return e, None, "latest settlement revision of %s altered" % m.ticker
    if kind == "drop_settlement":
        m = rng.choice([m for m in acct.markets if m.revisions()[-1][1]["revenue"] > 0])
        for x in e:
            x["settlements"] = [r for r in x["settlements"] if r["ticker"] != m.ticker]
        return e, None, "every settlement row for %s missing" % m.ticker
    if kind == "statement_cash":
        k = rng.choice(middle)
        e[k]["account"]["ending_cash_cents"] += rng.choice([-1, 1]) * rng.randint(1, 50)
        return e, None, "ending cash of %s off by a few cents" % e[k]["id"]
    if kind == "missing_export":
        k = rng.choice(middle)
        return e, k, "export %s never ingested" % e[k]["id"]
    raise ValueError(kind)


# ---------------------------------------------------------------- running

def run(pkg_root, args):
    try:
        p = subprocess.run([sys.executable, "-E", "-s", "-m", "fillbook"] + args,
                           cwd=pkg_root, capture_output=True, text=True, timeout=TIMEOUT)
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


def build_store(sol_root, work, tag, exps, skip=None, on_step=None):
    """Write the exports, build the legacy store, ingest the rest with the solution.

    on_step(k, store) runs whenever exports 0..k are exactly what has been ingested."""
    exp_root = os.path.join(work, "exports-" + tag)
    sim.write_all(exps, exp_root)
    store = os.path.join(work, "store-%s.db" % tag)
    path = lambda i: os.path.join(exp_root, exps[i]["id"])
    for i in range(LEGACY_EXPORTS):
        code, _, err = run(LEGACY, ["ingest", "--store", store, "--export", path(i)])
        if code != 0:
            raise RuntimeError("legacy ingest failed: " + err)
    done = set(range(LEGACY_EXPORTS))
    if on_step:
        on_step(LEGACY_EXPORTS - 1, store)
    errors = []
    for i in ingest_plan(len(exps)):
        if i == skip:
            continue
        code, _, err = run(sol_root, ["ingest", "--store", store, "--export", path(i)])
        if code != 0:
            errors.append("ingest %s: exit %s %s" % (exps[i]["id"], code, err.strip()[-200:]))
        done.add(i)
        if on_step and done == set(range(max(done) + 1)):
            on_step(max(done), store)
    return store, errors


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
            exps = sim.exports(acct)

            def checkpoint(k, store, acct=acct, exps=exps, seed=seed):
                want = acct.truth(exps[k]["end"])
                code, out, err = run(sol_root, ["report", "--store", store, "--json"])
                try:
                    got = json.loads(out) if code == 0 else None
                except ValueError:
                    got = None
                if got is None:
                    errors.append("seed %d report after %s: exit %s %s"
                                  % (seed, exps[k]["id"], code, err.strip()[-200:]))
                    got = {}
                parts["settled"].append(per_key(want["markets"], got.get("markets")))
                if want["open"]:  # an empty answer earns nothing when nothing is open
                    parts["open"].append(per_key(want["open"], got.get("open")))
                parts["total"].append(1.0 if got.get("total_cents") == want["total_cents"] else 0.0)
                parts["by_day"].append(per_key(want["by_day"], got.get("by_day")))

            store, errs = build_store(sol_root, work, "clean-%d" % seed, exps, on_step=checkpoint)
            errors += ["seed %d %s" % (seed, x) for x in errs]
            code, out, err = run(sol_root, ["reconcile", "--store", store])
            clean_pass.append(code == 0)
            if code != 0:
                first = (out.strip().splitlines() or [err.strip()[-200:]])[0][:200]
                errors.append("seed %d clean reconcile exit %s: %s" % (seed, code, first))

            rng = random.Random(seed * 31 + 7)
            for kind in (TAMPERS[(2 * i) % 6], TAMPERS[(2 * i + 1) % 6]):
                bad, skip, what = tamper(acct, exps, kind, rng)
                store, _ = build_store(sol_root, work, "%s-%d" % (kind, seed), bad, skip=skip)
                code, out, err = run(sol_root, ["reconcile", "--store", store])
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
        "checkpoints": len(parts["settled"]),
        "reconcile_clean_pass_rate": round(clean_rate, 3),
        "reconcile_catch_rate": round(catch_rate, 3),
        "tampers": tamper_log,
        "errors": errors,
        "seeds": seeds,
    }


def table(results):
    head = "| solution | score | settled /40 | open /15 | total /5 | by_day /10 | reconcile /30 | clean passed | defects caught |"
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
        print(table([grade(d, seeds) for d in dirs]))
        return 0
    if not argv:
        print(__doc__)
        return 2
    res = grade(os.path.abspath(argv[0]), seeds)
    if "--json" in argv:
        print(json.dumps(res, indent=1))
        return 0
    print("score %.1f / 100  (%d report checkpoints)" % (res["score"], res["checkpoints"]))
    for k, v in res["components"].items():
        print("  %-10s %5.1f / %d" % (k, v, WEIGHTS[k]))
    print("  reconcile: %d%% of clean stores passed, %d%% of defective stores caught"
          % (100 * res["reconcile_clean_pass_rate"], 100 * res["reconcile_catch_rate"]))
    for t in res["tampers"]:
        print("    %-6s %-19s %s" % ("caught" if t["caught"] else "MISSED", t["kind"], t["defect"]))
    for e in res["errors"][:12]:
        print("  error: " + e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
