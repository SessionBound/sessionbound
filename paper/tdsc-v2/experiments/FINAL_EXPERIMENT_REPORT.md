# Final v2 Experiment Report

## Status

- Branch: `tdsc-baseline-experiments`
- Base commit at experiment time: `5dce85f`
- Docker status during runs: API up on port 8000; PostgreSQL healthy
- Dataset: 430 expenses, 240 employees, 124 departments
- PDF target: `paper/tdsc-v2/sessionbound-tdsc.pdf`

## Results

Functional validation passed 24 of 24 canonical scenarios.

The security baseline matrix shows that simple role-only grants are
insufficient for SessionBound's target boundary: they block writes but
do not hide raw tables, denied fields, or out-of-scope rows. Safe views
and RLS each cover part of the boundary. Full SessionBound covers safe
views, denied fields, row scope, query budgets, disclosure budgets,
payload aggregation blocking, and receipts.

The measured prototype has an important credential-token binding gap.
Expired tokens and revoked tasks are denied, but credential-token
mismatch, replay with a second credential, wrong audience, wrong actor,
and second active task binding in one session were allowed.

Performance baselines show sub-millisecond p50 for raw, role-only,
safe-view-only, and RLS-only configurations on the small seed dataset.
Full SessionBound p50 measured about 20.9--22.2 ms across the five query
patterns because it includes runtime dispatch, policy checks, budget and
disclosure accounting, and receipt insertion.

Concurrency smoke testing passed at 1, 5, and 20 concurrent sessions with
0 failures. At 20 concurrent sessions, query p50 was 82.896 ms and query
p95 was 110.356 ms.

## Gaps Before TDSC Submission

- Implement and re-test strict credential-id-to-token binding.
- Validate token audience and actor against the runtime credential or
  principal.
- Reject token replay with a second credential/runtime principal.
- Reject same-session binding to a second active task.
- Implement safe-view version/hash invalidation for schema drift.
- Add a receipt-disabled SessionBound variant if receipt-only overhead is
  claimed.
- Run a larger dataset scale sweep before making scale or throughput
  claims.
