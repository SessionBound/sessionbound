# Final Experiment Report

## Status

- Branch: `high-standard-tdsc-pdsc-revision`
- Base commit at experiment time: current branch working tree
- Docker status during runs: API up on port 8000; PostgreSQL healthy
- Dataset: 430 expenses, 240 employees, 124 departments
- PDF target: `paper/tdsc/sessionbound-tdsc.pdf`

## Results

Functional validation passed 24 of 24 canonical scenarios.

The security baseline matrix shows that simple role-only grants are
insufficient for SessionBound's target boundary: they block writes but
do not hide raw tables, denied fields, or out-of-scope rows. Safe views cover
part of the boundary. The RLS+Safe View+Short Credential+Audit baseline covers
row scope, denied fields, read-only access, and basic audit logging. Full
SessionBound covers safe views, denied fields, row scope, query budgets,
disclosure budgets, payload aggregation blocking, receipt hash chain,
credential-token binding, and drift-bound token invalidation.

The updated prototype closes the prior P0 credential-token binding gap.
The credential-token suite now denies credential mismatch, replay with a
second credential, wrong audience, wrong actor, expired token, revoked
task state, and second active task binding in one session. The same suite
also denies safe-view registry-version drift, policy-version drift,
view-definition hash mismatch, and exposed-column hash mismatch.

Performance baselines show sub-millisecond p50 for raw, role-only, and
safe-view-only configurations on the small seed dataset. The
RLS+Safe View+Short Credential+Audit baseline measured about 0.98--1.12 ms p50.
Full SessionBound p50 measured about 17.4--18.6 ms across the five query
patterns because the wrapper reference path includes runtime dispatch, policy
checks, budget and disclosure accounting, receipt insertion, and result
materialization.

Receipt/budget ablation was measured in
`../raw_results/overhead_breakdown_20260708_205527.json`. Receipt-disabled
variants emitted no receipts, budget-disabled variants left budget counters
unchanged, and small-dataset p50 deltas were noisy rather than a stable
attribution to a single component.

A synthetic 1k/10k/100k scoped-row scale sweep was measured in
`../raw_results/scale_1783515366.json`. At 100k scoped rows, raw PostgreSQL
p50 was 68.81 ms for aggregate-by-category and 13.82 ms for top-k ordering,
while Full SessionBound p50 was 6365.20 ms and 6169.55 ms, respectively. This
confirms that the wrapper reference path is a security reference path and still
needs lower-level executor-accounting engineering before production-scale
claims. The hook-only microbenchmark isolates native structural guard checks at
0.125--0.138 ms p50, so the multi-second 100k result should be attributed to
the wrapper materialization/accounting path rather than parse/analyze guarding
alone.

Concurrency smoke testing passed at 1, 5, and 20 concurrent sessions with
0 failures. At 20 concurrent sessions, query p50 was 82.896 ms and query
p95 was 110.356 ms.

## Remaining Work Before TDSC Submission

- Treat performance as a prototype limitation unless a lower-level
  planner/executor-hook runtime path is implemented and measured.
- Add broader scale and concurrency runs only after optimizing the
  execution path.
- Treat minimum-group aggregate policy as direct-release mitigation only; it is
  not a formal guarantee against arbitrary semantic inference.
