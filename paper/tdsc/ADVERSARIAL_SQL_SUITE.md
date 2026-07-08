# Adversarial SQL Suite

## Status

- Script: `paper/tdsc/scripts/adversarial_sql_eval.py`
- Latest raw result: `paper/tdsc/raw_results/adversarial_sql_20260708_235742.json`
- API target: `http://localhost:8000`
- Cases: 140
- Passed expected classification: 140 / 140

Classification counts:

- Blocked: 126
- Allowed but accounted: 14

Bucket counts:

- Blocked direct violations: 115
- Allowed safe-view analytical cases: 14
- Blocked payload aggregation: 6
- Blocked small-group aggregate: 5

The suite is intentionally adversarial but not exhaustive. It tests direct
boundary violations, raw-schema escape, catalog and metadata escape,
`pg_temp`/search-path/GUC tampering, function abuse, payload aggregation and
compression, JSON/XML/composite leakage, prepared-statement and cursor
lifecycles, `COPY` and `EXPLAIN` variants, CTEs, recursive CTEs, set
operations, `VALUES`/`LATERAL`/`DISTINCT ON`/table sampling, DDL/DML/utility
commands, aggregate-inference probes, pagination and budget scraping,
revocation/expiry/rebind attempts, schema drift, and rollback/audit-survival
cases.

## Results

| Bucket | Cases | Result | Notes |
|---|---:|---:|---|
| Blocked direct violations | 115 | 115 / 115 blocked | denied fields, raw schema, catalogs, GUC/session tampering, function abuse, prepared/cursor misuse, `COPY`, `EXPLAIN ANALYZE`, set operations, DDL/DML, replay/drift/rollback attempts |
| Blocked payload aggregation | 6 | 6 / 6 blocked | JSON, array, string, XML, and row/composite payload compression |
| Blocked small-group aggregate | 5 | 5 / 5 blocked | direct entity grouping, HAVING probes, filtered small groups, and high configured `k` |
| Allowed safe-view analytics | 14 | 14 / 14 allowed/accounted | ordinary safe-view projection, join, aggregate, CTE/window, bounded pagination, and harmless aliasing |
| Total | 140 | 140 / 140 passed | zero expected-classification failures |

## Interpretation

The current prototype blocks the tested direct exfiltration attempts, catalog
lookups, DDL/DML, search-path abuse, recursive/set-operation stacking, payload
aggregation/compression attempts, and direct small-group aggregate releases.
Ordinary safe-view analytical SQL, including aliases, CTEs, expressions over
allowed columns, and aggregate groups satisfying the configured minimum group
size, remains allowed and emits receipts.

Minimum-group enforcement is not a formal inference-control proof. It mitigates
direct small-group aggregate release in the evaluated wrapper/API path and uses
a conservative shape-level policy in the native hook path. Arbitrary semantic
inference across multiple allowed answers remains outside the current claim.
