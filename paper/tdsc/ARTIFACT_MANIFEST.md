# TDSC Artifact Manifest

This manifest identifies the artifact set for the TDSC-oriented
SessionBound submission candidate.

## Canonical Version

- Artifact tag: `tdsc-submission-2026-07-07`
- Submission branch: `tdsc-hardening`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Build command: `cd paper/tdsc && make`
- Page count after native SELECT update: 15 pages

The Git tag is the stable artifact anchor. The `main` branch may continue to
evolve after submission work.

## Claim Contract

The TDSC manuscript is the canonical claim contract for this artifact. It states
the compressed paper argument and should be used instead of the earlier arXiv v1
text when checking current claims.

Core validated claims in the TDSC candidate:

- canonical functional validation: 24 / 24 scenarios passed;
- SDK query surface: native smoke passed, showing that
  `TaskboundSession.query(sql)` executes ordinary safe-view SQL through
  PostgreSQL hook/executor accounting, bound direct safe-view `SELECT` is
  accounted, and unbound safe-view access fails closed;
- PostgreSQL native hook/executor surface: 16 / 16 cases passed, covering
  bound native SELECT, prepared statements, cursor/FETCH, COPY(SELECT),
  EXPLAIN, fail-closed denials, and trusted-GUC protection;
- rollback-surviving audit: 2 / 2 cases passed, showing that evaluated
  bound-runtime allowed receipts/accounting and raw-schema denial receipts
  persist after `BEGIN ... ROLLBACK`;
- adversarial SQL suite: 28 cases, with 22 blocked cases, 5 allowed safe-view
  analytical cases, and 1 known limitation;
- default-seed overhead: full SessionBound p50 latency is 14.6--18.6 ms across
  representative query patterns;
- hook-only structural guard microbenchmark: `sessionbound_guard_check`
  p50 was 0.130--0.169 ms across SELECT/JOIN/GROUP BY/CTE-window checks,
  with raw-schema and UNION denials verified;
- 100k synthetic scale sweep: the historical PL/pgSQL wrapper path reaches
  multi-second latency; the new native path should be rebenchmarked before
  replacing the published performance table;
- security guarantees are limited to the stated prototype SQL fragment and do
  not claim arbitrary semantic inference prevention or differential privacy.

## Reproducibility Map

- Canonical validation script: `scripts/sessionbound_agent_eval.py`
- TDSC hardening scripts: `paper/tdsc/scripts/`
- TDSC raw results: `paper/tdsc/raw_results/`
- TDSC experiment reports: `paper/tdsc/experiments/`
- Functional validation summary: `paper/tdsc/evaluation/FUNCTIONAL_VALIDATION.md`
- Adversarial SQL report: `paper/tdsc/ADVERSARIAL_SQL_SUITE.md`
- AST validation report: `paper/tdsc/AST_VALIDATION.md`
- SDK query surface report: `paper/tdsc/SDK_QUERY_SURFACE.md`
- PostgreSQL hook report: `paper/tdsc/POSTGRES_HOOK_ENFORCEMENT.md`
- Hook-only microbenchmark script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Hook-only microbenchmark raw result:
  `paper/tdsc/raw_results/hook_microbenchmark_20260708_181304.json`
- Rollback audit script: `paper/tdsc/scripts/rollback_audit_eval.py`
- Rollback audit raw result:
  `paper/tdsc/raw_results/rollback_audit_20260708_141120.json`
- Overhead breakdown report: `paper/tdsc/OVERHEAD_BREAKDOWN.md`
- Security invariants companion: `paper/tdsc/SECURITY_INVARIANTS.md`

## Relationship to arXiv v1

`paper/arxiv-v1/` is an earlier preprint workspace. It remains useful as a
public historical snapshot, but the TDSC candidate has a compressed structure,
updated security argument, updated hardening evidence, and a different page
budget. If an arXiv v2 is prepared, it should be generated from the TDSC
candidate after submission text stabilizes.
