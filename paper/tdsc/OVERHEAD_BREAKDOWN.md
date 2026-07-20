# Overhead Breakdown

## Status

- Script: `paper/tdsc/scripts/overhead_breakdown.py`
- Latest archived raw JSON: `paper/tdsc/raw_results/overhead_breakdown_20260719_150951.json`
- Latest archived raw CSV: `paper/tdsc/raw_results/overhead_breakdown_20260719_150951.csv`
- Hook-only microbenchmark script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Latest hook-only microbenchmark:
  `paper/tdsc/raw_results/hook_microbenchmark_20260719_150550.json`
- Warmup: 10 iterations per pattern and mode
- Measurement: 100 iterations per pattern and mode
- Metrics: p50, p95, mean, standard deviation, rows returned, errors
- Error count in latest successful run: 0 for all measured supported rows;
  SessionBound aggregate/window rows are marked unsupported by policy.

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
- M4 SessionBound full: default wrapper reference path with receipts and budget
  accounting enabled.

The archived 2026-07-19 raw file also contained no-receipt and no-budget
SessionBound ablations. Those modes are no longer run by the current script:
the hardened token validator rejects attempts to disable receipts or budget
accounting.

## p50 Latency Table

Values are p50 latency in milliseconds.

| Pattern | Raw | Role-only | Safe-view | RLS+SV+Audit | SB full |
|---|---:|---:|---:|---:|---:|
| Q1 SELECT | 0.236 | 0.267 | 0.431 | 1.280 | 152.923 |
| Q2 JOIN | 0.179 | 0.171 | 0.212 | 0.969 | 158.980 |
| Q3 GROUP BY | 0.225 | 0.206 | 0.260 | 1.110 | -- |
| Q4 CTE detail | 0.204 | 0.180 | 0.214 | 0.910 | 151.161 |
| Q5 window function | 0.232 | 0.265 | 0.270 | 0.987 | -- |

## Required Analysis

Where is overhead concentrated?

Raw, role-only, and safe-view-only modes are sub-millisecond on the default seed.
The strong RLS+safe-view+short-credential+audit baseline adds a measured
per-query audit/RLS cost and lands at roughly 0.91--1.28 ms p50. Full
SessionBound in the wrapper reference path is roughly 151.2--159.0 ms p50 for
supported detail-query rows. This
indicates that the accounting-complete PL/pgSQL wrapper, JSON materialization,
claim lookup, budget accounting, exposure tracking, and receipt path dominate
the current small-dataset SessionBound overhead.

Is it fixed per-query overhead?

Mostly yes on the small default dataset. The raw query complexity range is small
(0.222--0.351 ms p50 across the simple baselines), while SessionBound wrapper
modes show a roughly fixed hundreds-of-milliseconds per-query cost with modest
variation by query shape.

Does receipt writing dominate?

The archived pre-hardening ablation did not show a stable receipt-only
latency attribution. For example, Q1 full was 152.923 ms p50 and no-receipt was
153.960 ms; Q4 CTE detail full was 151.161 ms and no-receipt was 155.381 ms.
These retired-mode deltas are small relative to the wrapper jump and remain
noisy.

Does budget update dominate?

The archived pre-hardening no-budget values were close to full SessionBound:
Q1 no-budget was 152.948 ms versus 152.923 ms full; Q4 CTE detail no-budget was
157.473 ms versus 151.161 ms full. Current secure tokens no longer allow this
mode.

Which parts are prototype artifacts?

Prototype artifacts include PL/pgSQL dynamic execution through
`taskbound.run`, per-row JSON materialization, array accumulation before
returning rows, repeated claim lookup through SQL functions, and the absence of
an optimized native executor-accounting path. The current native
hook/executor-accounting path is measured diagnostically, but it has not yet
removed the multi-second 10k detail-row accounting bottleneck.

What does the hook-only microbenchmark show?

The hook-only script measures `public.sessionbound_guard_check(sql)`, which
prepares SQL through PostgreSQL parse/analyze and the native structural guard
without executing result rows. With 20 warmup and 200 measured iterations,
allowed structural checks had p50 latency of 0.101 ms for SELECT, 0.129 ms for
JOIN, and 0.118 ms for non-aggregate CTE detail SQL. Raw-schema, UNION,
GROUP BY, and window-denial checks passed. This does not
measure executor accounting or receipt insertion, but it shows that structural
guarding is not the source of the 10k detail-row wrapper bottleneck.

## Caveat

These measurements should not be generalized to production deployments or an
optimized planner/executor-hook implementation. They characterize a
security-oriented PostgreSQL reference prototype. The 10k detail-release
diagnostic exposes the accounting/materialization bottleneck. The native
end-to-end benchmark shows the current native accounting path is also
unoptimized at 10k detail rows, even
though hook-only structural checks are sub-millisecond.
