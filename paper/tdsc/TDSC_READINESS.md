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
  before `taskbound.run(...)`.
- Recorded API-layer preflight denial receipts through `taskbound.fail_receipt`.
- Added the `sessionbound_guard` PostgreSQL C extension, loaded through
  `shared_preload_libraries`, with a `post_parse_analyze_hook` path for
  structural SQL enforcement before dynamic execution.
- Added `TaskboundSession.query(sql)` as the native-feeling agent SDK surface
  that wraps `taskbound.run(sql)`.
- Added AST validation script and raw results.
- Added SDK query surface script and raw results.
- Added hook enforcement script and raw results.
- Added a direct bare safe-view `SELECT` negative case showing that generated
  runtime credentials cannot bypass `taskbound.run(...)` accounting through
  ordinary view privileges.
- Added 28-case adversarial SQL suite and raw results.
- Added overhead breakdown script with raw JSON/CSV outputs.
- Expanded related work to 29 verified references.
- Updated the active TDSC manuscript in `paper/tdsc/sessionbound-tdsc.tex`
  with security invariants, AST validation positioning, adversarial SQL summary,
  overhead breakdown, limitations, and expanded related work.

## Evidence

- AST validation: 17 / 17 cases passed.
- SDK query surface: 2 / 2 cases passed.
- PostgreSQL hook enforcement: 11 / 11 cases passed.
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
  100k-row tests.
- RLS-only was not measured in the hardening overhead breakdown because the current
  prototype does not define RLS policies in the active runtime path.
- Production claims require hardened signing/key management, credential
  lifecycle cleanup, out-of-transaction denial logging, and operational
  migration/reapproval workflows.

## Recommendation

Use this as a stronger TDSC candidate draft for internal review, but do not mark
it ready for submission until the remaining blockers are either implemented or
explicitly accepted as limitations by the target submission strategy.
