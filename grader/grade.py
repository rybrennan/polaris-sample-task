"""Grades a fillbook solution. Deterministic, standard library only.

Usage:
    python3 grader/grade.py path/to/solution          # one solution
    python3 grader/grade.py path/to/solution --json   # machine-readable
    python3 grader/grade.py --all                     # starter + solutions/*

A solution is a directory containing a `fillbook/` package. Only that package
is copied into a clean temp directory; nothing else the solver wrote is used.

For each seed the grader generates a fresh run of daily exports, then:
  1. builds a store with fillbook 0.4 (grader/legacy) from the first three
     exports, the way production was left;
  2. runs the solution's `report` on that store as it stands;
  3. ingests the remaining exports with the solution, redelivering one and
     swapping two out of order, and runs `report` after every step where the
     ingested exports form an unbroken run from the first;
  4. runs `reconcile` on the finished store, and on stores built the same
     way from exports with one defect each.

Score, out of 100:
    40  settled markets: realized P&L exact to the cent, per market
    15  open markets: net cash moved, exact, per market, at checkpoints
        that have any
     5  total_cents exact
    10  by_day exact, per event day
    30  reconcile: (clean stores passed) x (defective stores caught)

The first four are averaged over every report checkpoint. The reconcile term
is a product, so a check that always passes or always fails scores zero.
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
from typing import Callable, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEGACY = os.path.join(HERE, "legacy")
sys.path.insert(0, HERE)
import sim  # noqa: E402  (sibling module; path set just above)

DEFAULT_SEEDS = [202, 303, 808, 909, 1111, 1212]
LEGACY_EXPORTS = 3
TAMPERS = ["drop_fill", "phantom_fill", "settlement_revenue",
           "drop_settlement", "statement_cash", "missing_export"]
WEIGHTS = {"settled": 40, "open": 15, "total": 5, "by_day": 10,
           "reconcile": 30}
TIMEOUT_S = 60
ERR_TAIL = 200  # characters of stderr kept in an error message

Export = dict      # one daily export, as built by sim.exports
StepHook = Callable[[int, str], None]


# ------------------------------------------------------------------ sequence

def ingest_plan(n: int) -> List[int]:
    """Returns the export indexes the solution ingests, in order.

    The legacy exports are already in the store. Of the rest, one export is
    delivered twice and two are swapped, as a backfill would arrive.

    Args:
        n: Total number of exports in the run.
    """
    rest = list(range(LEGACY_EXPORTS, n))
    redelivered = LEGACY_EXPORTS + 1
    rest.insert(rest.index(redelivered) + 1, redelivered)
    i = rest.index(LEGACY_EXPORTS + 3)
    j = rest.index(LEGACY_EXPORTS + 4)
    rest[i], rest[j] = rest[j], rest[i]
    return rest


# ----------------------------------------------------------------- tampering

def _executed(record: dict) -> bool:
    return record["status"] == "executed"


def tamper(acct: sim.Account, exps: List[Export], kind: str,
           rng: random.Random) -> Tuple[List[Export], Optional[int], str]:
    """Builds a copy of a run of exports with one realistic defect.

    Args:
        acct: The simulated account the exports came from.
        exps: The clean exports. Not modified.
        kind: One of TAMPERS.
        rng: Chooses what to damage.

    Returns:
        (exports, skip, description). `skip` is the index of an export that
        should never be ingested, or None.
    """
    e = copy.deepcopy(exps)
    middle = list(range(LEGACY_EXPORTS + 1, len(e) - 1))

    if kind == "drop_fill":
        f = rng.choice([f for f in acct.fills if not f["busted"]])
        for x in e:
            for page in x["rest"].values():
                page["fills"] = [r for r in page["fills"]
                                 if r["fill_id"] != f["fill_id"]]
            x["ws"] = [line for line in x["ws"]
                       if line["type"] != "fill"
                       or line["msg"]["fill_id"] != f["fill_id"]]
        return e, None, "fill %s of %s missing from every export" % (
            f["fill_id"], f["ticker"])

    if kind == "phantom_fill":
        k = rng.choice([k for k in range(len(e))
                        if any(_executed(r) for p in e[k]["rest"].values()
                               for r in p["fills"])])
        name = rng.choice(sorted(n for n, p in e[k]["rest"].items()
                                 if any(map(_executed, p["fills"]))))
        page = e[k]["rest"][name]
        src = rng.choice([r for r in page["fills"] if _executed(r)])
        copy_ = dict(src, fill_id="f-%012x" % rng.getrandbits(48))
        page["fills"].insert(page["fills"].index(src) + 1, copy_)
        return e, None, "phantom copy of %s in export %s" % (
            src["fill_id"], e[k]["id"])

    if kind == "settlement_revenue":
        m = rng.choice(acct.markets)
        latest = None
        for x in e:
            for row in x["settlements"]:
                if row["ticker"] == m.ticker:
                    latest = row
        delta = 100 * rng.randint(1, 3)
        if latest["revenue"] >= delta:
            latest["revenue"] -= delta
        else:
            latest["revenue"] += delta
        return e, None, "latest settlement revision of %s altered" % m.ticker

    if kind == "drop_settlement":
        m = rng.choice([m for m in acct.markets
                        if m.revisions()[-1][1]["revenue"] > 0])
        for x in e:
            x["settlements"] = [r for r in x["settlements"]
                                if r["ticker"] != m.ticker]
        return e, None, "every settlement row for %s missing" % m.ticker

    if kind == "statement_cash":
        k = rng.choice(middle)
        off = rng.choice([-1, 1]) * rng.randint(1, 50)
        e[k]["account"]["ending_cash_cents"] += off
        return e, None, "ending cash of %s off by a few cents" % e[k]["id"]

    if kind == "missing_export":
        k = rng.choice(middle)
        return e, k, "export %s never ingested" % e[k]["id"]

    raise ValueError(kind)


# ------------------------------------------------------------------- running

def run(pkg_root: str, args: Sequence[str]) -> Tuple[Optional[int], str, str]:
    """Runs `python -m fillbook ARGS` from pkg_root.

    Returns:
        (exit code or None on timeout, stdout, stderr).
    """
    try:
        p = subprocess.run(
            [sys.executable, "-E", "-s", "-m", "fillbook"] + list(args),
            cwd=pkg_root, capture_output=True, text=True, timeout=TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timeout"


def per_key(want: dict, got: object) -> float:
    """Returns the share of keys, over both dicts, whose values match exactly.

    An extra key in `got` counts against it, so padding an answer with an
    adjustment line costs points.
    """
    if not isinstance(got, dict):
        return 0.0
    keys = set(want) | set(got)
    if not keys:
        return 1.0
    hits = sum(1 for k in keys if k in want and k in got and got[k] == want[k])
    return hits / len(keys)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def build_store(sol_root: str, work: str, tag: str, exps: List[Export],
                skip: Optional[int] = None,
                on_step: Optional[StepHook] = None) -> Tuple[str, List[str]]:
    """Builds a store the way production would: 0.4 first, then the solution.

    Args:
        sol_root: Directory holding the solution's fillbook package.
        work: Scratch directory.
        tag: Unique name for this store's files.
        exps: The exports to write and ingest.
        skip: Index of an export to leave out, if any.
        on_step: Called as on_step(k, store_path) whenever exports 0..k are
            exactly what has been ingested.

    Returns:
        (store path, list of ingest errors).
    """
    exp_root = os.path.join(work, "exports-" + tag)
    sim.write_all(exps, exp_root)
    store = os.path.join(work, "store-%s.db" % tag)

    def path(i: int) -> str:
        return os.path.join(exp_root, exps[i]["id"])

    for i in range(LEGACY_EXPORTS):
        code, _, err = run(LEGACY, ["ingest", "--store", store,
                                    "--export", path(i)])
        if code != 0:
            raise RuntimeError("legacy ingest failed: " + err)
    done = set(range(LEGACY_EXPORTS))
    if on_step:
        on_step(LEGACY_EXPORTS - 1, store)

    errors = []
    for i in ingest_plan(len(exps)):
        if i == skip:
            continue
        code, _, err = run(sol_root, ["ingest", "--store", store,
                                      "--export", path(i)])
        if code != 0:
            errors.append("ingest %s: exit %s %s"
                          % (exps[i]["id"], code, err.strip()[-ERR_TAIL:]))
        done.add(i)
        if on_step and done == set(range(max(done) + 1)):
            on_step(max(done), store)
    return store, errors


class Tally:
    """Collects every measurement for one solution."""

    def __init__(self):
        self.report: Dict[str, List[float]] = {
            "settled": [], "open": [], "total": [], "by_day": []}
        self.clean_pass: List[bool] = []
        self.caught: List[bool] = []
        self.tampers: List[dict] = []
        self.errors: List[str] = []

    def check_report(self, sol_root: str, store: str, want: dict,
                     label: str) -> None:
        """Runs `report` on a store and scores it against the truth."""
        code, out, err = run(sol_root, ["report", "--store", store, "--json"])
        got = None
        if code == 0:
            try:
                got = json.loads(out)
            except ValueError:
                pass
        if not isinstance(got, dict):
            self.errors.append("%s: exit %s %s"
                               % (label, code, err.strip()[-ERR_TAIL:]))
            got = {}
        r = self.report
        r["settled"].append(per_key(want["markets"], got.get("markets")))
        if want["open"]:  # an empty answer earns nothing when nothing is open
            r["open"].append(per_key(want["open"], got.get("open")))
        r["total"].append(float(got.get("total_cents") == want["total_cents"]))
        r["by_day"].append(per_key(want["by_day"], got.get("by_day")))

    def result(self, solution_dir: str, seeds: Sequence[int]) -> dict:
        frac = {k: mean(v) for k, v in self.report.items()}
        clean_rate, catch_rate = mean(self.clean_pass), mean(self.caught)
        frac["reconcile"] = clean_rate * catch_rate
        score = sum(WEIGHTS[k] * frac[k] for k in WEIGHTS)
        return {
            "solution": os.path.relpath(solution_dir, ROOT),
            "score": round(score, 1),
            "components": {k: round(WEIGHTS[k] * frac[k], 1)
                           for k in WEIGHTS},
            "checkpoints": len(self.report["settled"]),
            "reconcile_clean_pass_rate": round(clean_rate, 3),
            "reconcile_catch_rate": round(catch_rate, 3),
            "tampers": self.tampers,
            "errors": self.errors,
            "seeds": list(seeds),
        }


def grade_seed(tally: Tally, sol_root: str, work: str, seed: int,
               index: int) -> None:
    """Runs every check for one seed and records it in `tally`."""
    acct = sim.simulate(seed)
    exps = sim.exports(acct)

    def checkpoint(k: int, store: str) -> None:
        tally.check_report(sol_root, store, acct.truth(exps[k]["end"]),
                           "seed %d report after %s" % (seed, exps[k]["id"]))

    store, errs = build_store(sol_root, work, "clean-%d" % seed, exps,
                              on_step=checkpoint)
    tally.errors += ["seed %d %s" % (seed, x) for x in errs]
    code, out, err = run(sol_root, ["reconcile", "--store", store])
    tally.clean_pass.append(code == 0)
    if code != 0:
        first = (out.strip().splitlines() or [err.strip()[-ERR_TAIL:]])[0]
        tally.errors.append("seed %d clean reconcile exit %s: %s"
                            % (seed, code, first[:ERR_TAIL]))

    rng = random.Random(seed * 31 + 7)
    for kind in (TAMPERS[(2 * index) % 6], TAMPERS[(2 * index + 1) % 6]):
        bad, skip, what = tamper(acct, exps, kind, rng)
        store, _ = build_store(sol_root, work, "%s-%d" % (kind, seed), bad,
                               skip=skip)
        code, _, err = run(sol_root, ["reconcile", "--store", store])
        hit = code == 1 and "Traceback" not in err
        tally.caught.append(hit)
        tally.tampers.append({"seed": seed, "kind": kind, "defect": what,
                              "caught": hit})


def grade(solution_dir: str, seeds: Sequence[int]) -> dict:
    """Grades one solution.

    Args:
        solution_dir: Directory containing a `fillbook/` package.
        seeds: Simulator seeds, one run of exports each.

    Returns:
        The score, its components, and every defect and error seen.
    """
    pkg = os.path.join(solution_dir, "fillbook")
    if not os.path.isdir(pkg):
        raise SystemExit("%s has no fillbook/ package" % solution_dir)
    work = tempfile.mkdtemp(prefix="polaris-grade-")
    sol_root = os.path.join(work, "sol")
    os.makedirs(sol_root)
    shutil.copytree(pkg, os.path.join(sol_root, "fillbook"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    tally = Tally()
    try:
        for index, seed in enumerate(seeds):
            grade_seed(tally, sol_root, work, seed, index)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return tally.result(solution_dir, seeds)


# ---------------------------------------------------------------- reporting

def table(results: Sequence[dict]) -> str:
    """Formats results as the Markdown table in README.md."""
    rows = [
        "| solution | score | settled /40 | open /15 | total /5 "
        "| by_day /10 | reconcile /30 | clean passed | defects caught |",
        "|" + "---|" * 9,
    ]
    for r in results:
        c = r["components"]
        rows.append(
            "| `%s` | **%.1f** | %.1f | %.1f | %.1f | %.1f | %.1f "
            "| %d%% | %d%% |" % (
                r["solution"], r["score"], c["settled"], c["open"],
                c["total"], c["by_day"], c["reconcile"],
                100 * r["reconcile_clean_pass_rate"],
                100 * r["reconcile_catch_rate"]))
    return "\n".join(rows)


def print_breakdown(res: dict) -> None:
    print("score %.1f / 100  (%d report checkpoints)"
          % (res["score"], res["checkpoints"]))
    for k, v in res["components"].items():
        print("  %-10s %5.1f / %d" % (k, v, WEIGHTS[k]))
    print("  reconcile: %d%% of clean stores passed, "
          "%d%% of defective stores caught"
          % (100 * res["reconcile_clean_pass_rate"],
             100 * res["reconcile_catch_rate"]))
    for t in res["tampers"]:
        print("    %-6s %-19s %s" % ("caught" if t["caught"] else "MISSED",
                                     t["kind"], t["defect"]))
    for e in res["errors"][:12]:
        print("  error: " + e)


def main(argv: List[str]) -> int:
    env = os.environ.get("POLARIS_SEEDS")
    seeds = [int(s) for s in env.split(",")] if env else DEFAULT_SEEDS
    if argv[:1] == ["--all"]:
        solutions = os.path.join(ROOT, "solutions")
        dirs = [os.path.join(ROOT, "starter")] + sorted(
            os.path.join(solutions, d) for d in os.listdir(solutions)
            if os.path.isdir(os.path.join(solutions, d, "fillbook")))
        print(table([grade(d, seeds) for d in dirs]))
        return 0
    if not argv:
        print(__doc__)
        return 2
    res = grade(os.path.abspath(argv[0]), seeds)
    if "--json" in argv:
        print(json.dumps(res, indent=1))
    else:
        print_breakdown(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
