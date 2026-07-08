# PostgreSQL Hook Enforcement

## Status

- Extension: `postgres/sessionbound_guard/`
- Loaded by: `shared_preload_libraries=sessionbound_guard`
- Hooks: `post_parse_analyze_hook`, `ProcessUtility_hook`, executor hooks
- Runtime entrypoints: native safe-view `SELECT`; compatibility
  `public.sessionbound_guard_check(sql_text)`
- Trusted context: SUSET GUCs set by `taskbound.bind_task(...)`
- Evaluation script: `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`
- Rollback audit script: `paper/tdsc/scripts/rollback_audit_eval.py`
- Latest raw result: `paper/tdsc/raw_results/sessionbound_guard_hook_20260708_122824.json`
- Latest rollback audit result: `paper/tdsc/raw_results/rollback_audit_20260708_141120.json`
- Result: 16 / 16 cases passed
- Rollback audit result: 2 / 2 cases passed

The hardening prototype now includes both API-layer AST validation and a native
PostgreSQL hook/executor path for structural SQL enforcement, result
accounting, and rollback-surviving allowed and denial receipts in the evaluated
bound runtime path.

## Design

`sessionbound_guard` is a PostgreSQL C extension loaded at server startup. It
registers a `post_parse_analyze_hook` and defines trusted custom GUCs:

- `sessionbound_guard.enabled`
- `sessionbound_guard.task_bound`
- `sessionbound_guard.task_id`
- `sessionbound_guard.budget_account`
- `sessionbound_guard.allowed_view_oids`
- `sessionbound_guard.max_queries`
- `sessionbound_guard.max_unique_expense_rows`
- `sessionbound_guard.receipts_enabled`
- `sessionbound_guard.budget_accounting_enabled`

The GUCs are `PGC_SUSET`, so ordinary agent roles cannot set or tamper with
them. During `taskbound.bind_task(...)`, the database computes approved safe
view OIDs from `taskbound.safe_view_registry` and stores them in the trusted
GUC context for the current backend.

After `taskbound.bind_task(...)`, agents may issue native safe-view `SELECT`
statements directly. The parse/analyze hook validates relation OIDs, SQL shape,
and function use. The utility hook covers prepared statements, cursors/FETCH,
`COPY (SELECT) TO STDOUT`, and non-`ANALYZE` `EXPLAIN`. Executor hooks reserve
query budget, wrap the destination receiver, count returned rows, observe
disclosed `expense_id` values before forwarding tuples, and emit receipts.
Receipt and budget updates use an autonomous same-database audit channel, so
receipts for evaluated allowed executions and hook/API denials survive rollback
of the agent transaction.

## Enforced Query-Shape Policy

The hook rejects:

- SQL that is not `SELECT`;
- raw application schema relations such as `app_data`;
- `pg_catalog` and `information_schema` access;
- recursive CTEs;
- `UNION`, `INTERSECT`, and `EXCEPT`;
- unsafe or non-catalog functions outside trusted runtime helpers;
- payload aggregation functions such as `json_agg`, `jsonb_agg`, `array_agg`,
  `string_agg`, `xmlagg`, `row_to_json`, `json_build_object`, and
  `jsonb_build_object`;
- relation OIDs not present in the approved safe-view registry for the bound
  task;
- non-view relations even if their OIDs were mistakenly listed;
- sensitive output aliases such as `salary`, `bank_account`, and `phone`.

## Evaluation Summary

| Case group | Path | Result |
|---|---|---:|
| Trusted GUC tamper attempt | non-superuser direct DB session | 1 / 1 blocked |
| Native safe-view SQL | agent credential direct DB connection | 1 / 1 allowed |
| Bound bare safe-view `SELECT` | agent credential direct DB session without `taskbound.run` | 1 / 1 allowed/accounted |
| Prepared, cursor/FETCH, COPY, EXPLAIN | agent credential native SQL surface | 4 / 4 allowed |
| `UNION`, catalog access, recursive CTE | agent credential native SQL surface | 3 / 3 blocked |
| Runtime helper abuse and `EXPLAIN ANALYZE` | agent credential direct DB connection | 2 / 2 blocked |
| Non-`SELECT`, raw schema, payload aggregation, unapproved safe-view OID | superuser trusted-GUC hook check, no API preflight | 4 / 4 blocked |
| Hook enforcement subtotal | mixed direct database paths | 16 / 16 passed |
| Rollback-surviving audit | bound native allowed SELECT and raw-schema denial inside ROLLBACK | 2 / 2 persisted |

The hook evaluation intentionally bypasses the `/agent-query` API preflight for
the agent cases by connecting directly as the generated runtime credential, then
calling `taskbound.bind_task(...)` and issuing native SQL from that PostgreSQL
session. The generated runtime credential has `SELECT` on taskbound safe views;
unbound or out-of-registry access fails closed, and raw application tables remain
ungranted for `SELECT`.
The hook-only cases use trusted superuser setup to exercise the extension path
without the API validator.

## Rollback Audit Reproduction

Run:

```bash
python paper/tdsc/scripts/rollback_audit_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
```

The script creates two fresh credential/task pairs. It executes an allowed
native safe-view `SELECT` inside `BEGIN ... ROLLBACK`, then verifies that
`taskbound.inspect_task_state()` and `taskbound.receipts()` still show the
allowed accounting update and receipt. It separately executes a denied raw
`app_data` query inside `BEGIN ... ROLLBACK`, then verifies that a `denied`
receipt with reason `raw application schema access is not allowed` remains
visible after rollback.

## Boundary

This is now a native hook/executor prototype rather than a wrapper-only path.
It still remains a research artifact: the disclosure unit is demo-specific
(`expense_id`), the autonomous audit channel uses same-database `dblink`, and
larger deployments would want planner-level cost controls and richer inference
budgets. The API-layer AST validator remains as defense in depth and as a
portable preflight path.
