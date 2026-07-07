# Adversarial SQL Suite

## Status

- Script: `paper/tdsc/scripts/adversarial_sql_eval.py`
- Latest raw result: `paper/tdsc/raw_results/adversarial_sql_20260707_145022.json`
- API target: `http://localhost:8000`
- Cases: 28
- Passed expected classification: 28 / 28

Classification counts:

- Blocked: 22
- Allowed but accounted: 5
- Known limitation: 1
- Filtered: 0
- Not testable: 0

The suite is intentionally adversarial but not exhaustive. It tests direct
boundary violations, catalog escape, payload aggregation, aliases, CTEs,
set-operation stacking, aggregate inference, and search-path/function abuse.

## Results

| Attack | SQL pattern | Expected | Actual | Classification | Notes |
|---|---|---|---|---|---|
| A01 Direct boundary violations | `SELECT salary FROM employees;` | Blocked | Blocked | Blocked | denied column |
| A02 Direct boundary violations | `SELECT bank_account FROM employees;` | Blocked | Blocked | Blocked | denied column |
| A03 Direct boundary violations | `SELECT * FROM app_data.expenses;` | Blocked | Blocked | Blocked | raw schema |
| A04 Direct boundary violations | `DELETE FROM expenses WHERE expense_month='2026-06';` | Blocked | Blocked | Blocked | mutation |
| A05 Direct boundary violations | `DROP TABLE expenses;` | Blocked | Blocked | Blocked | DDL |
| B01 Catalog and metadata escape | `SELECT * FROM pg_catalog.pg_tables;` | Blocked | Blocked | Blocked | catalog schema |
| B02 Catalog and metadata escape | `SELECT * FROM information_schema.columns;` | Blocked | Blocked | Blocked | catalog schema |
| B03 Catalog and metadata escape | `SELECT schemaname, tablename FROM pg_tables;` | Blocked | Blocked | Blocked | bare catalog view |
| C01 Payload aggregation / compression | `SELECT json_agg(e) FROM expenses e;` | Blocked | Blocked | Blocked | payload aggregation |
| C02 Payload aggregation / compression | `SELECT jsonb_agg(e) FROM expenses e;` | Blocked | Blocked | Blocked | payload aggregation |
| C03 Payload aggregation / compression | `SELECT array_agg(e.expense_id) FROM expenses e;` | Blocked | Blocked | Blocked | payload aggregation |
| C04 Payload aggregation / compression | `SELECT array_agg(row_to_json(e)) FROM expenses e;` | Blocked | Blocked | Blocked | payload aggregation |
| C05 Payload aggregation / compression | `SELECT string_agg(employee_name, ',') FROM employees;` | Blocked | Blocked | Blocked | payload aggregation |
| C06 Payload aggregation / compression | `SELECT json_build_object('data', array_agg(e.expense_id)) FROM expenses e;` | Blocked | Blocked | Blocked | payload aggregation |
| D01 Obfuscation and aliases | `SELECT e.employee_name AS s FROM employees e;` | Allowed but accounted | Allowed but accounted | Allowed but accounted | 226 rows, receipt emitted |
| D02 Obfuscation and aliases | `SELECT amount AS salary FROM expenses;` | Blocked | Blocked | Blocked | denied-field alias |
| D03 Obfuscation and aliases | `SELECT e.amount + 0 AS amount2 FROM expenses e;` | Allowed but accounted | Allowed but accounted | Allowed but accounted | 347 rows, receipt emitted |
| D04 Obfuscation and aliases | `SELECT COALESCE(employee_name, '') FROM employees;` | Allowed but accounted | Allowed but accounted | Allowed but accounted | 226 rows, receipt emitted |
| E01 Subqueries and CTEs | `WITH x AS (SELECT * FROM expenses) SELECT * FROM x;` | Allowed but accounted | Allowed but accounted | Allowed but accounted | 347 rows, receipt emitted |
| E02 Subqueries and CTEs | `WITH x AS (SELECT employee_name FROM employees) SELECT * FROM x;` | Allowed but accounted | Allowed but accounted | Allowed but accounted | 226 rows, receipt emitted |
| E03 Subqueries and CTEs | recursive CTE with `UNION ALL` | Blocked | Blocked | Blocked | recursive/set operation |
| F01 UNION and stacking | `SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees;` | Blocked | Blocked | Blocked | set operation |
| F02 UNION and stacking | `SELECT department_id FROM departments UNION ALL SELECT employee_id FROM employees;` | Blocked | Blocked | Blocked | set operation |
| G01 Small-group / aggregate inference | group by `department_id, employee_id` with `count` and `sum` | Known limitation | Known limitation | Known limitation | allowed aggregate over small groups |
| H01 Search path / function abuse | `SHOW search_path;` | Blocked | Blocked | Blocked | utility statement |
| H02 Search path / function abuse | `SET search_path TO app_data;` | Blocked | Blocked | Blocked | search path change |
| H03 Search path / function abuse | `CREATE FUNCTION leak() RETURNS text AS $$ SELECT 'x' $$ LANGUAGE SQL;` | Blocked | Blocked | Blocked | function creation |
| H04 Search path / function abuse | `DO $$ BEGIN RAISE NOTICE 'x'; END $$;` | Blocked | Blocked | Blocked | DO block |

## Interpretation

The current prototype blocks the tested direct exfiltration attempts, catalog
lookups, DDL/DML, search-path abuse, recursive/set-operation stacking, and
payload aggregation/compression attempts. Ordinary safe-view analytical SQL,
including aliases, CTEs, and simple expressions over allowed columns, remains
allowed and emits receipts.

The small-group aggregate case remains a known limitation. It is allowed
because the current prototype does not implement minimum group-size rules,
aggregate sensitivity scoring, or formal inference control. This limitation
should be stated in the paper and kept in future work.
