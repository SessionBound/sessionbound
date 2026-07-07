# TDSC v3 Readiness

## Status

Recommendation: not ready for final TDSC submission yet, but materially
stronger than v2.

## Completed

- Created `tdsc-v3-hardening` branch and `paper/tdsc-v3/` workspace.
- Captured baseline environment, Docker startup, PostgreSQL version, and current
  manuscript path.
- Added `sqlglot==30.12.0` and `app/sql_ast_validator.py`.
- Integrated AST preflight in `/query`, `/agent-query`, and `/agent-question`
  before `taskbound.run(...)`.
- Recorded API-layer preflight denial receipts through `taskbound.fail_receipt`.
- Added AST validation script and raw results.
- Added 28-case adversarial SQL suite and raw results.
- Added overhead breakdown script with raw JSON/CSV outputs.
- Expanded related work to 29 verified references.
- Updated the active TDSC manuscript in `paper/tdsc-v2/sessionbound-tdsc.tex`
  with security invariants, AST validation positioning, adversarial SQL summary,
  v3 overhead breakdown, limitations, and expanded related work.

## Evidence

- AST validation: 17 / 17 cases passed.
- Adversarial SQL: 28 / 28 expected classifications passed.
- Adversarial classifications: 22 blocked, 5 allowed but accounted, 1 known
  limitation.
- Canonical validation: 24 / 24 scenarios passed.
- Overhead: supported modes completed with zero errors.
- Related work: 29 verified bibliography entries, all cited.

## Remaining Blockers

- AST validation is API-layer preflight, not PostgreSQL parser/planner-hook
  enforcement.
- Small-group aggregate inference remains a known limitation.
- Disclosure budgets are operational controls, not formal differential privacy.
- The PL/pgSQL reference runtime has high scale-sensitive overhead in prior
  100k-row tests.
- RLS-only was not measured in the v3 overhead breakdown because the current
  prototype does not define RLS policies in the active runtime path.
- Production claims require hardened signing/key management, credential
  lifecycle cleanup, out-of-transaction denial logging, and operational
  migration/reapproval workflows.

## Recommendation

Use this as a stronger TDSC candidate draft for internal review, but do not mark
it ready for submission until the remaining blockers are either implemented or
explicitly accepted as limitations by the target submission strategy.
