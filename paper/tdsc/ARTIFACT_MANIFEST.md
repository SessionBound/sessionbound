# TDSC Artifact Manifest

This manifest identifies the artifact set for the TDSC-oriented
SessionBound submission candidate.

## Canonical Version

- Artifact tag: `high-standard-tdsc-pdsc-revision-2026-07-08`
- Submission branch: `high-standard-tdsc-pdsc-revision`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Build command: `cd paper/tdsc && make`
- Page count after P0 hardening revision: 14 pages

No immutable Git tag has been created for this revision yet. The manuscript and
raw results in this branch are the current submission-candidate artifact set;
create a tag only after the final PDF and metadata pass.

## Claim Contract

The TDSC manuscript is the canonical claim contract for this artifact. It states
the compressed paper argument and should be used instead of the earlier arXiv v1
text when checking current claims.

Core validated claims in the TDSC candidate:

- canonical functional validation: 24 / 24 scenarios passed;
- global single-active binding: 1000 / 1000 same-key race rounds had exactly
  one owner, with 0 dual-success rounds, 0 zero-owner rounds, 20-contender
  same-key race producing 1 owner and 19 deterministic denials, crash/socket
  recovery passing, and old-fence mutation rejected;
- SDK query surface: native smoke passed, showing that
  `TaskboundSession.query(sql)` executes ordinary safe-view SQL through
  PostgreSQL hook/executor accounting, bound direct safe-view `SELECT` is
  accounted, and unbound safe-view access fails closed;
- PostgreSQL native hook/executor surface: 18 / 18 cases passed, covering
  bound native SELECT, prepared statements, cursor/FETCH, COPY(SELECT),
  EXPLAIN, fail-closed denials, trusted-GUC protection, and conservative
  aggregate-shape denials;
- rollback-surviving audit: 2 / 2 cases passed, showing that evaluated
  bound-runtime allowed receipts/accounting and raw-schema denial receipts
  persist after `BEGIN ... ROLLBACK`;
- adversarial SQL suite: 140 cases, with 126 blocked cases, 14 allowed
  safe-view analytical cases, and all tested direct small-group
  aggregate-release attempts blocked by the minimum-group policy;
- strong baseline: RLS + Safe View + Short Credential + Audit is implemented
  and measured alongside raw, role-only, safe-view-only, and SessionBound modes;
- default-seed overhead after global-owner validation: full SessionBound
  wrapper-reference p50 latency is 128.4--136.1 ms across representative query
  patterns; the RLS+safe-view+audit baseline is 1.06--1.31 ms p50;
- hook-only structural guard microbenchmark: `sessionbound_guard_check`
  p50 was 0.127--0.176 ms across SELECT/JOIN/GROUP BY/CTE-window checks,
  with raw-schema, UNION, direct-entity group-by, and HAVING denials verified;
- bounded diagnostic native end-to-end scale benchmark: at 10k scoped rows,
  wrapper p50 is 5.08--5.25 s and native hook/executor accounting p50 is
  5.16--5.38 s across SELECT/JOIN/GROUP BY/CTE/window query shapes;
- security guarantees are limited to the stated prototype SQL fragment and do
  not claim arbitrary semantic inference prevention or differential privacy.

## Reproducibility Map

- Canonical validation script: `scripts/sessionbound_agent_eval.py`
- Canonical validation raw result:
  `paper/tdsc/raw_results/sessionbound_agent_eval_1783653370.json`
- TDSC hardening scripts: `paper/tdsc/scripts/`
- TDSC raw results: `paper/tdsc/raw_results/`
- TDSC experiment reports: `paper/tdsc/experiments/`
- Functional validation summary: `paper/tdsc/evaluation/FUNCTIONAL_VALIDATION.md`
- Adversarial SQL report: `paper/tdsc/ADVERSARIAL_SQL_SUITE.md`
- Adversarial SQL raw result:
  `paper/tdsc/raw_results/adversarial_sql_20260710_110551.json`
- AST validation report: `paper/tdsc/AST_VALIDATION.md`
- AST validation raw result:
  `paper/tdsc/raw_results/ast_validation_20260710_103428.json`
- SDK query surface report: `paper/tdsc/SDK_QUERY_SURFACE.md`
- PostgreSQL hook report: `paper/tdsc/POSTGRES_HOOK_ENFORCEMENT.md`
- Hook-only microbenchmark script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Hook-only microbenchmark raw result:
  `paper/tdsc/raw_results/hook_microbenchmark_20260710_103846.json`
- Rollback audit script: `paper/tdsc/scripts/rollback_audit_eval.py`
- Rollback audit raw result:
  `paper/tdsc/raw_results/rollback_audit_20260710_110603.json`
- Overhead breakdown report: `paper/tdsc/OVERHEAD_BREAKDOWN.md`
- Overhead breakdown raw result:
  `paper/tdsc/raw_results/overhead_breakdown_20260710_103837.json`
- Global single-active binding script:
  `paper/tdsc/scripts/single_active_binding_eval.py`
- Global single-active binding raw result:
  `paper/tdsc/raw_results/single_active_binding_20260710_030846.json`
- Scale sweep raw result:
  `paper/tdsc/raw_results/scale_1783515366.json`
- Native end-to-end scale raw result:
  `paper/tdsc/raw_results/native_end_to_end_20260710_110541.json`
- Native partial-budget script: `paper/tdsc/scripts/native_partial_budget_eval.py`
- Native partial-budget raw result:
  `paper/tdsc/raw_results/native_partial_budget_20260710_110604.json`
- Concurrent isolation raw result:
  `paper/tdsc/raw_results/concurrent_isolation_20260710_030604.json`
- Credential-token and schema drift raw result:
  `paper/tdsc/raw_results/credential_token_1783515640.json`
- Security baseline raw result:
  `paper/tdsc/raw_results/security_baseline_1783515117.json`
- Security invariants companion: `paper/tdsc/SECURITY_INVARIANTS.md`

## Relationship to arXiv v1

`paper/arxiv-v1/` is an earlier preprint workspace. It remains useful as a
public historical snapshot, but the TDSC candidate has a compressed structure,
updated security argument, updated hardening evidence, and a different page
budget. If an arXiv v2 is prepared, it should be generated from the TDSC
candidate after submission text stabilizes.
