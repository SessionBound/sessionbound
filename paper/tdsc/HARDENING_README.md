# SessionBound TDSC Hardening Workspace

This workspace records the latest TDSC hardening sprint for SessionBoundDB.

The current implementation keeps the compatibility API names `taskbound.run(...)`,
`taskbound.bind_task(...)`, and the SQL schema `taskbound`. The active manuscript
path in this repository is `paper/tdsc/sessionbound-tdsc.tex`; this folder also
holds raw results and hardening notes.

Artifacts:

- `HARDENING_REPORT.md`: baseline environment, startup, and execution notes.
- `SECURITY_INVARIANTS.md`: enforcement state and invariants.
- `AST_VALIDATION.md`: SQL structure validation design and evaluation.
- `SDK_QUERY_SURFACE.md`: native-feeling agent SDK query surface and
  evaluation.
- `POSTGRES_HOOK_ENFORCEMENT.md`: experimental PostgreSQL hook enforcement
  design and evaluation.
- `ADVERSARIAL_SQL_SUITE.md`: adversarial SQL test suite and results.
- `OVERHEAD_BREAKDOWN.md`: overhead measurements and interpretation.
- `RELATED_WORK_EXPANSION.md`: verified related work notes.
- `TDSC_READINESS.md`: readiness assessment for the journal candidate.
- `FINAL_HARDENING_REPORT.md`: final status and blockers.
- `raw_results/`: raw JSON/CSV outputs from experiments.
- `scripts/`: reproducible evaluation scripts.
