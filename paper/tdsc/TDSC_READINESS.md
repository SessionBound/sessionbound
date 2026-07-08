# TDSC Readiness

## Status

Recommendation: not ready for final TDSC submission yet, but materially
stronger than the prior draft.

## Completed

- Created the hardening branch and consolidated `paper/tdsc/` workspace.
- Captured baseline environment, Docker startup, PostgreSQL version, and current
  manuscript path.
- Added `sqlglot==30.12.0` and `app/sql_ast_validator.py`.
- Integrated AST preflight in `/query`, `/agent-query`, and `/agent-question`
  before native SDK SQL execution; `taskbound.run(...)` remains as a
  compatibility wrapper.
- Recorded API-layer preflight denial receipts through `taskbound.fail_receipt`;
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
- Added 28-case adversarial SQL suite and raw results.
- Added overhead breakdown script with raw JSON/CSV outputs.
- Expanded related work to 29 verified references.
- Updated the active TDSC manuscript in `paper/tdsc/sessionbound-tdsc.tex`
  with security invariants, AST validation positioning, adversarial SQL summary,
  overhead breakdown, limitations, and expanded related work.

## Evidence

- AST validation: 17 / 17 cases passed.
- SDK query surface: native query, bound bare SELECT accounting, and unbound
  fail-closed behavior passed.
- PostgreSQL hook enforcement: 16 / 16 cases passed.
- Hook-only microbenchmark: 6 / 6 checks passed; allowed structural guard checks
  were 0.130--0.169 ms p50.
- Rollback audit: 2 / 2 cases passed.
- Adversarial SQL: 28 / 28 expected classifications passed.
- Adversarial classifications: 22 blocked, 5 allowed but accounted, 1 known
  limitation.
- Canonical validation: 24 / 24 scenarios passed.
- Overhead: supported modes completed with zero errors.
- Related work: 29 verified bibliography entries, all cited.

## Remaining Blockers

- The PostgreSQL hook path is experimental parse/analyze enforcement, not yet
  production-grade planner/executor enforcement.
- Small-group aggregate inference remains a known limitation.
- Disclosure budgets are operational controls, not formal differential privacy.
- The PL/pgSQL reference runtime has high scale-sensitive overhead in prior
  100k-row tests. The hook-only microbenchmark narrows the bottleneck away from
  structural parse/analyze guarding, but native executor-accounting scale still
  needs measurement.
- RLS-only was not measured in the hardening overhead breakdown because the current
  prototype does not define RLS policies in the active runtime path.
- Production claims require hardened signing/key management, credential
  lifecycle cleanup, external audit retention/WORM storage, and operational
  migration/reapproval workflows. The evaluated bound runtime path already
  persists API/hook denial receipts outside aborting agent transactions.

## Recommendation

Use this as a stronger TDSC candidate draft for internal review, but do not mark
it ready for submission until the remaining blockers are either implemented or
explicitly accepted as limitations by the target submission strategy.
