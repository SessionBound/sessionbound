# Adversarial SQL Suite

## Status

- Script: `paper/tdsc/scripts/adversarial_sql_eval.py`
- Latest raw result: `paper/tdsc/raw_results/adversarial_sql_20260719_162614.json`
- API target: `http://localhost:8000`
- Cases: 140
- Passed expected classification: 140 / 140

Classification counts:

- Blocked: 130
- Allowed but accounted: 10

Bucket counts:

- Blocked direct violations: 117
- Allowed safe-view detail cases: 10
- Blocked payload aggregation: 6
- Blocked aggregate/window release: 7

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
| Blocked direct violations | 117 | 117 / 117 blocked | denied fields, raw schema, catalogs, GUC/session tampering, function abuse, prepared/cursor misuse, `COPY`, `EXPLAIN ANALYZE`, set operations, DDL/DML, replay/drift/rollback attempts |
| Blocked payload aggregation | 6 | 6 / 6 blocked | JSON, array, string, XML, and row/composite payload compression |
| Blocked aggregate/window release | 7 | 7 / 7 blocked | direct entity grouping, HAVING probes, filtered aggregate release, high configured `k`, ungrouped aggregate, and window release |
| Allowed safe-view detail queries | 10 | 10 / 10 allowed/accounted | ordinary safe-view projection, join, non-aggregate CTE/detail access, bounded pagination, and harmless aliasing |
| Total | 140 | 140 / 140 passed | zero expected-classification failures |

## Interpretation

The current prototype blocks the tested direct exfiltration attempts, catalog
lookups, DDL/DML, search-path abuse, recursive/set-operation stacking, payload
aggregation/compression attempts, and direct aggregate/window release.
Ordinary safe-view detail SQL, including aliases, non-aggregate CTEs,
expressions over allowed columns, bounded pagination, and joins, remains
allowed and emits receipts.

The aggregate policy is conservative rather than a formal inference-control
proof: direct aggregate/window release is denied until approved templates can
provide trusted provenance/cardinality logic. Arbitrary semantic inference
across multiple allowed detail answers remains outside the current claim.
