# Final Experiment Report

## Status

- Branch: `tdsc-baseline-experiments`
- Base commit at experiment time: `5dce85f`
- Docker status during runs: API up on port 8000; PostgreSQL healthy
- Dataset: 430 expenses, 240 employees, 124 departments
- PDF target: `paper/tdsc/sessionbound-tdsc.pdf`

## Results

Functional validation passed 24 of 24 canonical scenarios.

The security baseline matrix shows that simple role-only grants are
insufficient for SessionBound's target boundary: they block writes but
do not hide raw tables, denied fields, or out-of-scope rows. Safe views
and RLS each cover part of the boundary. Full SessionBound covers safe
views, denied fields, row scope, query budgets, disclosure budgets,
payload aggregation blocking, and receipts.

The updated prototype closes the prior P0 credential-token binding gap.
The credential-token suite now denies credential mismatch, replay with a
second credential, wrong audience, wrong actor, expired token, revoked
task state, and second active task binding in one session. The same suite
also denies safe-view registry-version drift, policy-version drift, and
view-definition hash mismatch.

Performance baselines show sub-millisecond p50 for raw, role-only,
safe-view-only, and RLS-only configurations on the small seed dataset.
Full SessionBound p50 measured about 20.9--22.2 ms across the five query
patterns because it includes runtime dispatch, policy checks, budget and
disclosure accounting, and receipt insertion.

Receipt/budget ablation was measured in
`raw_results/ablation_1783224624.json`. Receipt-disabled variants emitted
zero receipts, budget-disabled variants left `query_count` unchanged, and
small-dataset p50 deltas were noisy rather than a stable attribution to a
single component.

A synthetic 1k/10k/100k scoped-row scale sweep was measured in
`raw_results/scale_1783224892.json`. At 100k scoped rows, raw PostgreSQL
p50 was 14.77 ms for aggregate-by-category and 15.62 ms for top-k
ordering, while Full SessionBound p50 was 6742.55 ms and 6965.09 ms,
respectively. This confirms that the PL/pgSQL prototype is a security
reference path and still needs planner/executor-hook or lower-level execution
engineering before production-scale claims.

Concurrency smoke testing passed at 1, 5, and 20 concurrent sessions with
0 failures. At 20 concurrent sessions, query p50 was 82.896 ms and query
p95 was 110.356 ms.

## Remaining Work Before TDSC Submission

- Treat performance as a prototype limitation unless a lower-level
  planner/executor-hook runtime path is implemented and measured.
- Add broader scale and concurrency runs only after optimizing the
  execution path.
