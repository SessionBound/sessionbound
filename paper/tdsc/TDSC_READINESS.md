# TDSC Readiness

> Superseded status note (2026-07-19): this report is a historical snapshot.
> Current claim status and raw-result pointers are in
> `paper/tdsc/ARTIFACT_MANIFEST.md`, `paper/tdsc/SUBMISSION_STATUS.md`, and
> `CHANGELOG_TDSC_HARDENING.md`.

## Status

Recommendation: substantially stronger than the prior draft, with final
PDF/layout review still required before formal upload. Native
executor-accounting scale is now measured diagnostically, but remains
unoptimized.

## Completed

- Created the high-standard revision branch and consolidated `paper/tdsc/`
  workspace.
- Captured baseline environment, Docker startup, PostgreSQL version, and current
  manuscript path.
- Added `sqlglot==30.12.0` and `app/sql_ast_validator.py`.
- Integrated AST preflight in `/query`, `/agent-query`, and `/agent-question`;
  these API paths now use the accounting-complete `taskbound.run(...)`
  reference path.
- Recorded API-layer preflight denial receipts through the autonomous audit
  channel;
  in the current hardening prototype these are written through the same
  rollback-surviving audit channel as native hook denial receipts.
- Added the `sessionbound_guard` PostgreSQL C extension, loaded through
  `shared_preload_libraries`, with a `post_parse_analyze_hook` path for
  structural SQL enforcement before dynamic execution.
- Added `TaskboundSession.query(sql)` as the native safe-view SQL agent SDK
  surface under PostgreSQL hook/executor accounting.
- Added AST validation script and raw results.
- Added SDK query surface script and raw results.
- Added hook enforcement script and raw results.
- Added hook-only structural guard microbenchmark and raw result.
- Added direct bare safe-view `SELECT`, prepared statement, cursor/FETCH,
  COPY(SELECT), and EXPLAIN cases showing that generated runtime credentials
  remain guarded by native hook/executor accounting outside `taskbound.run(...)`.
- Added rollback audit script and raw result for allowed and denied receipts.
- Expanded the adversarial SQL suite to 140 cases and added raw results,
  including conservative aggregate/window denial checks.
- Added a strongest-practical RLS + Safe View + Short Credential + Audit
  baseline and remeasured overhead/security comparisons.
- Added overhead breakdown script with raw JSON/CSV outputs.
- Added native end-to-end benchmark across Raw, Safe-view-only,
  RLS+SafeView+ShortCredential+Audit, SessionBound wrapper, and SessionBound
  native hook/executor modes.
- Expanded related work to 32 verified references.
- Updated the active TDSC manuscript in `paper/tdsc/sessionbound-tdsc.tex`
  with security invariants, AST validation positioning, adversarial SQL summary,
  overhead breakdown, limitations, and expanded related work.

## Evidence

- AST validation: 20 / 20 cases passed.
- SDK query surface: native query, bound bare SELECT accounting, and unbound
  fail-closed behavior passed.
- PostgreSQL hook enforcement: 18 / 18 cases passed.
- Hook-only microbenchmark: 6 / 6 checks passed; allowed structural guard checks
  were 0.125--0.138 ms p50.
- Rollback audit: 4 / 4 cases passed; no test-side receipt write.
- Adversarial SQL: 140 / 140 expected classifications passed.
- Adversarial classifications: 126 blocked, 14 allowed but accounted, all
  tested direct small-group aggregate-release attempts blocked.
- Canonical validation: 24 / 24 scenarios passed.
- Overhead: supported modes completed with zero errors.
- Related work: 32 verified bibliography entries, all cited.

## Remaining Blockers

- The PostgreSQL hook path includes experimental parse/analyze enforcement and
  executor accounting, but not a formal SQL safety proof.
- Direct aggregate/window release is denied pending approved aggregate
  templates; arbitrary semantic inference across multiple allowed answers
  remains out of scope.
- Disclosure budgets are conservative output-tuple operational controls, not
  formal differential privacy or hidden-entity accounting.
- The current wrapper and native executor-accounting paths both have high
  scale-sensitive overhead in 100k-row tests. The hook-only microbenchmark
  narrows the bottleneck away from structural parse/analyze guarding, but the
  native accounting implementation still needs optimization and broader
  workload study.
- The RLS + Safe View + Short Credential + Audit baseline is implemented and
  measured, but it intentionally lacks task-token binding, cumulative disclosure
  budget, receipt hash chain, and safe-view drift invalidation.
- Production claims require hardened signing/key management, credential
  lifecycle cleanup, external audit retention/WORM storage, and operational
  migration/reapproval workflows. The evaluated bound runtime path already
  persists API/hook/executor denial receipts outside aborting agent
  transactions.

## Recommendation

Use this as a stronger TDSC candidate draft for internal review, but do not mark
it ready for submission until the remaining blockers are either implemented or
explicitly accepted as limitations by the target submission strategy.
