# AST Validation

## Status

- Implementation: `app/sql_ast_validator.py`
- Parser: `sqlglot==30.12.0`
- Runtime integration: API-layer preflight after token binding and before
  native SDK SQL execution; `taskbound.run(...)` remains as a compatibility
  wrapper
- Database hook companion: `postgres/sessionbound_guard/`
- Evaluation script: `paper/tdsc/scripts/ast_validation_eval.py`
- Latest raw result: `paper/tdsc/raw_results/ast_validation_20260707_182706.json`
- Result: 17 / 17 cases passed

This is an AST-level prototype validator for query-shape analysis before
SessionBoundDB execution. The hardening prototype now also includes an
experimental PostgreSQL `post_parse_analyze_hook` path for database-resident
structural enforcement; this API validator remains defense in depth and a
portable preflight path.

## Extracted Structure

The validator attempts to extract:

- referenced relations;
- referenced columns;
- function calls;
- CTE names;
- recursive CTE state;
- subquery count;
- join count;
- catalog access;
- operation type;
- set operations (`UNION`, `INTERSECT`, `EXCEPT`);
- blocked utility or procedural forms such as `COPY`, `SET`, `SHOW`,
  `CREATE FUNCTION`, and `DO` blocks.

## Blocked or Flagged Query Shapes

The prototype denies:

- raw schema references such as `app_data`;
- catalog access through `pg_catalog`, `information_schema`, or known catalog
  views such as `pg_tables`;
- mutation statements and DDL;
- `COPY`, `SET`, `SHOW`, `CREATE FUNCTION`, and `DO`;
- denied columns and denied-field aliases;
- recursive CTEs;
- `UNION`, `INTERSECT`, and `EXCEPT` stacking;
- unknown functions;
- payload aggregation functions including `json_agg`, `jsonb_agg`,
  `array_agg`, `string_agg`, `xmlagg`, `row_to_json`, `json_build_object`,
  and `jsonb_build_object`.

## Evaluation Summary

| Case type | Cases | Result |
|---|---:|---|
| Allowed SELECT/JOIN/GROUP BY/CTE/window | 5 | Passed |
| Raw schema/catalog escape | 3 | Passed |
| Mutation/utility/procedural SQL | 4 | Passed |
| Payload aggregation | 1 | Passed |
| Recursive CTE/UNION/unknown function/denied alias | 4 | Passed |
| Total | 17 | 17 passed |

## Integration Notes

The API binds the task token first. If AST preflight denies a query in this
bound API path, the API records a rollback-surviving denial receipt through
`taskbound.fail_receipt(...)` and does not invoke native SQL execution.

Acceptable wording for the paper:

```text
The hardening prototype includes both API-layer AST validation and a PostgreSQL
hook/executor path for structural SQL enforcement. Runtime credentials can issue
native `SELECT` over registered safe views only after a valid task binding; raw
table access, private runtime helpers, unsafe utility statements, and unbound
safe-view access fail closed. `TaskboundSession.query(sql)` is accounted through
executor hooks, and evaluated allowed receipts plus hook/API denial receipts are
emitted through a rollback-surviving audit path.
```
