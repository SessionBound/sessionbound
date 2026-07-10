# Overhead Breakdown

## Status

- Script: `paper/tdsc/scripts/overhead_breakdown.py`
- Latest raw JSON: `paper/tdsc/raw_results/overhead_breakdown_20260710_103837.json`
- Latest raw CSV: `paper/tdsc/raw_results/overhead_breakdown_20260710_103837.csv`
- Hook-only microbenchmark script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Latest hook-only microbenchmark:
  `paper/tdsc/raw_results/hook_microbenchmark_20260708_205831.json`
- Warmup: 10 iterations per pattern and mode
- Measurement: 100 iterations per pattern and mode
- Metrics: p50, p95, mean, standard deviation, rows returned, errors
- Error count in latest successful run: 0 for all modes

Measurements use direct PostgreSQL connections from the Compose network.
HTTP, model calls, dynamic credential issuance, and API-layer AST parsing are
excluded from this overhead breakdown.

## Modes

- M0 Raw PostgreSQL: admin/test role over `app_data` tables with equivalent
  tenant/month predicates.
- M1 Role-only read-only credential: temporary read-only role over `app_data`
  tables.
- M2 Safe-view-only: task-shaped safe views without SessionBound task token,
  budgets, receipts, or credential-token binding.
- M3 RLS + Safe View + Short Credential + Audit: PostgreSQL RLS policies,
  field-limited safe views, a constrained short-lived credential, read-only
  grants, and per-query audit insert.
- M4 SessionBound without receipts: `taskbound.run(...)` with
  `receipts_enabled=false`.
- M5 SessionBound without budget updates: `taskbound.run(...)` with
  `budget_accounting_enabled=false`.
- M6 SessionBound full: default wrapper reference path with receipts and budget
  accounting enabled.

## p50 Latency Table

Values are p50 latency in milliseconds.

| Pattern | Raw | Role-only | Safe-view | RLS+SV+Audit | SB no-receipt | SB no-budget | SB full |
|---|---:|---:|---:|---:|---:|---:|---:|
| Q1 SELECT | 0.247 | 0.297 | 0.255 | 1.129 | 134.192 | 134.389 | 135.736 |
| Q2 JOIN | 0.265 | 0.316 | 0.273 | 1.058 | 147.588 | 136.673 | 136.056 |
| Q3 GROUP BY | 0.242 | 0.222 | 0.289 | 1.135 | 128.913 | 122.838 | 128.412 |
| Q4 CTE | 0.232 | 0.230 | 0.269 | 1.208 | 128.148 | 126.448 | 129.717 |
| Q5 window function | 0.247 | 0.280 | 0.351 | 1.313 | 138.756 | 132.769 | 134.932 |

## Required Analysis

Where is overhead concentrated?

Raw, role-only, and safe-view-only modes are sub-millisecond on the default seed.
The strong RLS+safe-view+short-credential+audit baseline adds a measured
per-query audit/RLS cost and lands at roughly 1.06--1.31 ms p50. Full
SessionBound in the wrapper reference path is roughly 128.4--136.1 ms p50. This
indicates that the accounting-complete PL/pgSQL wrapper, JSON materialization,
claim lookup, budget accounting, exposure tracking, and receipt path dominate
the current small-dataset SessionBound overhead.

Is it fixed per-query overhead?

Mostly yes on the small default dataset. The raw query complexity range is small
(0.222--0.351 ms p50 across the simple baselines), while SessionBound wrapper
modes show a roughly fixed hundreds-of-milliseconds per-query cost with modest
variation by query shape.

Does receipt writing dominate?

No. Disabling receipts does not consistently reduce latency. For example,
Q1 full is 135.736 ms p50 and no-receipt is 134.192 ms; Q5 full is 134.932 ms and
no-receipt is 138.756 ms. The deltas are small relative to the wrapper jump and
remain noisy.

Does budget update dominate?

No. Disabling budget accounting produces p50 values close to full SessionBound.
Q1 no-budget is 134.389 ms versus 135.736 ms full; Q4 no-budget is 126.448 ms
versus 129.717 ms full.

Which parts are prototype artifacts?

Prototype artifacts include PL/pgSQL dynamic execution through
`taskbound.run`, per-row JSON materialization, array accumulation before
returning rows, repeated claim lookup through SQL functions, and the absence of
an optimized native executor-accounting path. The current native
hook/executor-accounting path is measured diagnostically, but it has not yet
removed the multi-second 100k-row accounting bottleneck.

What does the hook-only microbenchmark show?

The hook-only script measures `public.sessionbound_guard_check(sql)`, which
prepares SQL through PostgreSQL parse/analyze and the native structural guard
without executing result rows. With 20 warmup and 200 measured iterations,
allowed structural checks had p50 latency of 0.125 ms for SELECT, 0.131 ms for
JOIN, 0.131 ms for GROUP BY, and 0.138 ms for CTE/window SQL. Raw-schema,
UNION, direct entity group-by, and HAVING-denial checks passed. This does not
measure executor accounting or receipt insertion, but it shows that structural
guarding is not the source of the 100k-row wrapper bottleneck.

## Caveat

These measurements should not be generalized to production deployments or an
optimized planner/executor-hook implementation. They characterize a
security-oriented PostgreSQL reference prototype. The 100k scale sweep exposes
the accounting/materialization bottleneck. The native end-to-end benchmark
shows the current native accounting path is also unoptimized at 100k rows, even
though hook-only structural checks are sub-millisecond.
