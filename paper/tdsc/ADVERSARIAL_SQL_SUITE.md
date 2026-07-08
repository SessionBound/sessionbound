# Adversarial SQL Suite

## Status

- Script: `paper/tdsc/scripts/adversarial_sql_eval.py`
- Latest raw result: `paper/tdsc/raw_results/adversarial_sql_20260708_210059.json`
- API target: `http://localhost:8000`
- Cases: 34
- Passed expected classification: 34 / 34

Classification counts:

- Blocked: 27
- Allowed but accounted: 7
- Known limitation: 0
- Filtered: 0
- Not testable: 0

Bucket counts:

- Blocked direct violations: 16
- Allowed safe-view analytical cases: 7
- Blocked payload aggregation: 6
- Blocked small-group aggregate: 5

The suite is intentionally adversarial but not exhaustive. It tests direct
boundary violations, catalog escape, payload aggregation, aliases, CTEs,
set-operation stacking, minimum-group aggregate enforcement, and
search-path/function abuse.

## Results

| Attack family | Cases | Result | Notes |
|---|---:|---:|---|
| Direct boundary violations | 5 | 5 / 5 blocked | denied fields, raw schema, DML, DDL |
| Catalog and metadata escape | 3 | 3 / 3 blocked | `pg_catalog`, `information_schema`, and bare catalog views |
| Payload aggregation / compression | 6 | 6 / 6 blocked | JSON, array, string, and row-to-JSON aggregation |
| Obfuscation and aliases | 4 | 3 allowed/accounted, 1 blocked | benign expressions allowed; denied-field alias blocked |
| Subqueries and CTEs | 3 | 2 allowed/accounted, 1 blocked | ordinary safe-view CTEs allowed; recursive/set shape blocked |
| UNION and stacking | 2 | 2 / 2 blocked | `UNION` and `UNION ALL` denied |
| Small-group aggregate policy | 7 | 2 allowed/accounted, 5 blocked | groups at or above `k=5` allowed; direct entity grouping, small groups, HAVING probes, and too-small scopes blocked |
| Search path / function abuse | 4 | 4 / 4 blocked | utility, search-path, function creation, and DO block attempts |

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
