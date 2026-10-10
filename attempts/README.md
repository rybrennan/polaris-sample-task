# Model attempts

Each folder holds the `fillbook/` package and tests a model left behind. Each
model worked in a fresh copy of `starter/` with `TASK.md` and no access to
`grader/` or `solutions/`. That access rule was an instruction to the model, not
a sandbox.

| Attempt | Task version | Model | Score | Wall time |
|---|---|---|---|---|
| `v1-sonnet` | 1 | Claude Sonnet | 100.0 (v1 grader) | about 1.5 min |
| `v1-opus` | 1 | Claude Opus | 100.0 (v1 grader) | about 2.5 min |
| `v2-sonnet` | 2 | Claude Sonnet | 100.0 | about 1.7 min |
| `v2-opus` | 2 | Claude Opus | 100.0 | about 3 min |

Version 1 attempts were graded by the version 1 grader, which is no longer in
this repo (see `DESIGN.md`). Don't regrade them against version 2: they never
saw the version 2 field reference.
