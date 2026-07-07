# Overhead Breakdown

## Status

- Script: `paper/tdsc/scripts/overhead_breakdown.py`
- Latest raw JSON: `paper/tdsc/raw_results/overhead_breakdown_20260707_064236.json`
- Latest raw CSV: `paper/tdsc/raw_results/overhead_breakdown_20260707_064236.csv`
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
| Q1 SELECT | 0.222 | 0.242 | 14.316 | N/A | 15.085 | 14.554 | 14.672 | safe-view predicates plus wrapper |
| Q2 JOIN | 0.167 | 0.171 | 17.474 | N/A | 18.445 | 18.482 | 18.859 | safe-view predicates plus wrapper |
| Q3 GROUP BY | 0.174 | 0.208 | 13.942 | N/A | 17.335 | 15.448 | 15.307 | safe-view predicates plus wrapper |
| Q4 CTE | 0.173 | 0.212 | 13.529 | N/A | 15.264 | 14.871 | 15.905 | safe-view predicates plus wrapper |
| Q5 window function | 0.416 | 0.405 | 15.698 | N/A | 18.348 | 18.188 | 17.380 | safe-view predicates plus wrapper |

## Required Analysis

Where is overhead concentrated?

The dominant overhead is already present in M2 safe-view-only. Raw and
role-only queries are sub-millisecond, while safe-view-only p50 is roughly
13.5--17.5 ms. Full SessionBound p50 is roughly 14.7--18.9 ms. This indicates
that safe-view evaluation, session claim lookup, and the PL/pgSQL execution
shape dominate on the small seed dataset.

Is it fixed per-query overhead?

Mostly yes on this dataset. The raw query complexity range is small
(0.167--0.416 ms p50), while safe-view and SessionBound modes show a roughly
fixed tens-of-milliseconds per-query cost with modest variation by query shape.

Does receipt writing dominate?

No. Disabling receipts does not consistently reduce latency. For example,
Q1 full is 14.672 ms p50 and no-receipt is 15.085 ms; Q5 full is 17.380 ms and
no-receipt is 18.348 ms. The deltas are smaller than the safe-view jump and
are noisy.

Does budget update dominate?

No. Disabling budget accounting produces p50 values close to full
SessionBound. Q1 no-budget is 14.554 ms versus 14.672 ms full; Q3 no-budget is
15.448 ms versus 15.307 ms full.

Does query complexity dominate?

Not in the small seed benchmark. JOIN and window patterns are somewhat higher
than simple SELECT, but the main jump happens between raw/role-only and
safe-view/SessionBound modes.

Which parts are prototype artifacts?

Likely prototype artifacts include PL/pgSQL dynamic execution through
`taskbound.run`, per-row JSON materialization, array accumulation before
returning rows, repeated claim lookup through SQL functions, and the lack of a
parser/planner hook path.

Which parts are security costs?

Security costs include safe-view scope predicates, task-session lookup,
budget-accounting updates, unique-row exposure tracking, and receipt insertion.
In this run, safe-view/session-claim enforcement is the largest measured cost;
receipt and budget accounting are comparatively smaller.

## Caveat

These measurements should not be generalized to production deployments or a
future parser-hook implementation. They characterize the current PostgreSQL
reference prototype.
