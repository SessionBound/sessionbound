# Overhead Breakdown

## Status

- Script: `paper/tdsc/scripts/overhead_breakdown.py`
- Latest raw JSON: `paper/tdsc/raw_results/overhead_breakdown_20260707_084853.json`
- Latest raw CSV: `paper/tdsc/raw_results/overhead_breakdown_20260707_084853.csv`
- Hook-only microbenchmark script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Latest hook-only microbenchmark:
  `paper/tdsc/raw_results/hook_microbenchmark_20260708_181304.json`
- Warmup: 10 iterations per pattern and mode
- Measurement: 100 iterations per pattern and mode
- Metrics: p50, p95, mean, standard deviation, rows returned, errors
- Error count in latest successful run: 0 for all supported modes

Measurements use direct PostgreSQL connections from the Compose network.
HTTP, model calls, dynamic credential issuance, and API-layer AST parsing are
excluded from this overhead breakdown.

## Modes

- M0 Raw PostgreSQL: admin/test role over `app_data` tables with equivalent
  tenant/month predicates.
- M1 Role-only read-only credential: temporary read-only role over `app_data`
  tables.
- M2 Safe-view-only: bound task state with direct safe-view queries, receipts
  and budget accounting disabled.
- M3 RLS-only: not supported by the current prototype; recorded as `N/A`.
- M4 SessionBound without receipts: `taskbound.run(...)` with
  `receipts_enabled=false`.
- M5 SessionBound without budget updates: `taskbound.run(...)` with
  `budget_accounting_enabled=false`.
- M6 SessionBound full: default `taskbound.run(...)` with receipts and budget
  accounting enabled.

## p50 Latency Table

Values are p50 latency in milliseconds.

| Pattern | Raw | Role-only | Safe-view | RLS | SB no-receipt | SB no-budget | SB full | Main suspected cost |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Q1 SELECT | 0.233 | 0.229 | 14.213 | N/A | 14.899 | 14.333 | 14.630 | safe-view predicates plus wrapper |
| Q2 JOIN | 0.182 | 0.171 | 18.092 | N/A | 18.497 | 18.238 | 18.581 | safe-view predicates plus wrapper |
| Q3 GROUP BY | 0.193 | 0.169 | 13.296 | N/A | 14.840 | 15.074 | 15.024 | safe-view predicates plus wrapper |
| Q4 CTE | 0.176 | 0.175 | 13.525 | N/A | 14.529 | 15.098 | 15.425 | safe-view predicates plus wrapper |
| Q5 window function | 0.351 | 0.347 | 13.828 | N/A | 15.089 | 14.852 | 15.854 | safe-view predicates plus wrapper |

## Required Analysis

Where is overhead concentrated?

The dominant overhead is already present in M2 safe-view-only. Raw and
role-only queries are sub-millisecond, while safe-view-only p50 is roughly
13.3--18.1 ms. Full SessionBound p50 is roughly 14.6--18.6 ms. This indicates
that safe-view evaluation, session claim lookup, and the PL/pgSQL execution
shape dominate on the small seed dataset.

Is it fixed per-query overhead?

Mostly yes on the small default dataset. The raw query complexity range is small
(0.169--0.351 ms p50), while safe-view and SessionBound modes show a roughly
fixed tens-of-milliseconds per-query cost with modest variation by query shape.

Does receipt writing dominate?

No. Disabling receipts does not consistently reduce latency. For example,
Q1 full is 14.630 ms p50 and no-receipt is 14.899 ms; Q5 full is 15.854 ms and
no-receipt is 15.089 ms. The deltas are smaller than the safe-view jump and
are noisy.

Does budget update dominate?

No. Disabling budget accounting produces p50 values close to full
SessionBound. Q1 no-budget is 14.333 ms versus 14.630 ms full; Q3 no-budget is
15.074 ms versus 15.024 ms full.

Does query complexity dominate?

Not in the small seed benchmark. JOIN and window patterns are somewhat higher
than simple SELECT, but the main jump happens between raw/role-only and
safe-view/SessionBound modes.

Which parts are prototype artifacts?

Likely prototype artifacts include PL/pgSQL dynamic execution through
`taskbound.run`, per-row JSON materialization, array accumulation before
returning rows, repeated claim lookup through SQL functions, and the lack of an
optimized planner/executor hook path.

What does the hook-only microbenchmark show?

The hook-only script measures `public.sessionbound_guard_check(sql)`, which
prepares SQL through PostgreSQL parse/analyze and the native structural guard
without executing result rows. With 20 warmup and 200 measured iterations,
allowed structural checks had p50 latency of 0.138 ms for SELECT, 0.169 ms for
JOIN, 0.139 ms for GROUP BY, and 0.130 ms for CTE/window SQL. Raw-schema and
UNION denial checks passed. This does not measure executor accounting or receipt
insertion, but it shows that the structural guard itself is not the source of
the historical 6.7--7.0 s 100k-row behavior.

Which parts are security costs?

Security costs include safe-view scope predicates, task-session lookup,
budget-accounting updates, unique-row exposure tracking, and receipt insertion.
In this run, safe-view/session-claim enforcement is the largest measured cost;
receipt and budget accounting are comparatively smaller.

## Caveat

These measurements should not be generalized to production deployments or an
optimized planner/executor-hook implementation. They characterize a
security-oriented PostgreSQL reference prototype. The 100k scale sweep exposes
the wrapper-era materialization/accounting bottleneck; it is not a
production-readiness result for the native hook/executor architecture.
