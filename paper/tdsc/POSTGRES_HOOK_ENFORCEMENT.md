# PostgreSQL Hook Enforcement

## Status

- Extension: `postgres/sessionbound_guard/`
- Loaded by: `shared_preload_libraries=sessionbound_guard`
- Hooks: `post_parse_analyze_hook`, `ProcessUtility_hook`, executor hooks
- Runtime entrypoints: native safe-view `SELECT`; compatibility
  `public.sessionbound_guard_check(sql_text)`
- Trusted context: SUSET GUCs set by `taskbound.bind_task(...)`
- Evaluation script: `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`
- Hook-only microbenchmark script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Rollback audit script: `paper/tdsc/scripts/rollback_audit_eval.py`
- Latest raw result: `paper/tdsc/raw_results/sessionbound_guard_hook_20260720_080304.json`
- Latest hook-only microbenchmark result:
  `paper/tdsc/raw_results/hook_microbenchmark_20260719_150550.json`
- Latest rollback audit result:
  `paper/tdsc/raw_results/rollback_audit_20260720_080542.json`
- Latest raw-result status: 20 / 20 cases passed, including same-binding
  prepared execution and cross-binding prepared-plan rebind denial.
- Hook-only microbenchmark result: 7 / 7 checks passed; allowed structural
  checks p50 = 0.101--0.129 ms
- Rollback audit result: 5 / 5 cases passed

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
- `sessionbound_guard.min_group_size`

The GUCs are `PGC_SUSET`, so ordinary agent roles cannot set or tamper with
them. During `taskbound.bind_task(...)`, the database computes approved safe
view OIDs from `taskbound.safe_view_registry` and stores them in the trusted
GUC context for the current backend.

After `taskbound.bind_task(...)`, agents may issue native safe-view `SELECT`
statements directly. The parse/analyze hook validates relation OIDs, SQL shape,
and function use. The utility hook covers prepared statement revalidation and
`COPY (SELECT) TO STDOUT`; cursor/FETCH and `EXPLAIN` output are denied until
they have a reviewed release barrier. Executor hooks preflight query budget,
wrap the destination receiver, stage the complete result in a private
tuplestore, and commit query-count, detail-tuple charges, and receipts together
before releasing any tuple. Direct aggregate/window release is denied
pending approved templates.
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
- `TABLESAMPLE`;
- unsafe or non-catalog functions outside trusted runtime helpers;
- payload aggregation functions such as `json_agg`, `jsonb_agg`, `array_agg`,
  `string_agg`, `xmlagg`, `row_to_json`, `json_build_object`, and
  `jsonb_build_object`;
- relation OIDs not present in the approved safe-view registry for the bound
  task;
- non-view relations even if their OIDs were mistakenly listed;
- sensitive output aliases such as `salary`, `bank_account`, and `phone`.
- direct aggregate and window release, including ungrouped aggregates, ad-hoc
  `GROUP BY`, filtered aggregate release, and `HAVING`.

The native hook path enforces aggregate/window release conservatively at the SQL
shape level. The wrapper/API reference path applies the same template-required
policy before release.

## Evaluation Summary

| Case group | Path | Result |
|---|---|---:|
| Trusted GUC tamper attempt | non-superuser direct DB session | 1 / 1 blocked |
| Native safe-view SQL | agent credential direct DB connection | 1 / 1 allowed |
| Bound bare safe-view `SELECT` | agent credential direct DB session without `taskbound.run` | 1 / 1 allowed/accounted |
| Prepared, COPY, and cross-binding rebind | agent credential native SQL surface | 3 / 3 current-binding operations allowed and cross-binding reuse blocked |
| Cursor/FETCH, holdable cursor, and EXPLAIN | agent credential native SQL surface | 4 / 4 denied pending release-barrier support |
| `UNION`, catalog access, recursive CTE | agent credential native SQL surface | 3 / 3 blocked |
| Runtime helper abuse | agent credential direct DB connection | 1 / 1 blocked |
| Non-`SELECT`, raw schema, payload aggregation, unapproved safe-view OID | superuser trusted-GUC hook check, no API preflight | 4 / 4 blocked |
| Aggregate/window shape denials | direct aggregate/window release and HAVING probes | 2 / 2 blocked |
| Hook enforcement subtotal | mixed direct database paths | 20 / 20 passed |
| Rollback-surviving audit | allowed, API preflight, hook/parser, wrapper, and executor denials across autocommit and ROLLBACK | 5 / 5 persisted |

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

The script creates five fresh credential/task pairs. It executes an allowed
native safe-view `SELECT` inside `BEGIN ... ROLLBACK`, a direct hook/parser
raw-schema denial, a wrapper/parser denial in autocommit mode, a native
executor budget denial inside `BEGIN ... ROLLBACK`, and an API AST preflight
denial. It verifies that all receipts are produced by the runtime (the script
never calls `taskbound.fail_receipt`), that touched safe-view provenance is
populated where the submitted task SQL references approved views, and that the
executor denial releases zero rows.

For safely parseable bound database SQL, touched-view receipt provenance is
derived from PostgreSQL parse/analyze safe-view OIDs and then mapped back to
approved registry names. API preflight denials and unsupported syntax rejected
before parse/analyze use an approved-name fallback as audit context.

## Hook-Only Microbenchmark

Run:

```bash
python paper/tdsc/scripts/hook_microbenchmark.py --output-dir paper/tdsc/raw_results
```

The script measures `public.sessionbound_guard_check(sql)`, which prepares SQL
through PostgreSQL parse/analyze and the `sessionbound_guard` structural checks.
It does not execute result rows and does not measure executor row accounting,
receipt insertion, or PL/pgSQL result materialization. In the latest run, 20
warmup and 200 measured iterations per allowed case produced p50 values of
0.101 ms for SELECT, 0.129 ms for JOIN, and 0.118 ms for non-aggregate CTE
detail SQL. Raw-schema, UNION, GROUP BY, and window-denial checks also passed.
This supports the performance interpretation that the 10k detail-row
multi-second wrapper result is not caused by the structural parse/analyze guard
alone.

## Boundary

This is now a native hook/executor prototype rather than a wrapper-only path.
It still remains a research artifact: the disclosure unit is a conservative
output tuple (not a hidden-entity or inference guarantee), the autonomous audit
channel uses same-database `dblink`, and
larger deployments would want planner-level cost controls, richer inference
budgets, and fresh scale measurements of the native executor-accounting path.
The API-layer AST validator remains as defense in depth and as a portable
preflight path.
