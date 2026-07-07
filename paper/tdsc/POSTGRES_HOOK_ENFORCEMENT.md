# PostgreSQL Hook Enforcement

## Status

- Extension: `postgres/sessionbound_guard/`
- Loaded by: `shared_preload_libraries=sessionbound_guard`
- Hook: `post_parse_analyze_hook`
- Runtime entrypoint: `public.sessionbound_guard_check(sql_text)`
- Trusted context: SUSET GUCs set by `taskbound.bind_task(...)`
- Evaluation script: `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`
- Latest raw result: `paper/tdsc/raw_results/sessionbound_guard_hook_20260707_164553.json`
- Result: 10 / 10 cases passed

The hardening prototype now includes both API-layer AST validation and an
experimental PostgreSQL hook path for structural SQL enforcement.

## Design

`sessionbound_guard` is a PostgreSQL C extension loaded at server startup. It
registers a `post_parse_analyze_hook` and defines trusted custom GUCs:

- `sessionbound_guard.enabled`
- `sessionbound_guard.task_bound`
- `sessionbound_guard.task_id`
- `sessionbound_guard.allowed_view_oids`

The GUCs are `PGC_SUSET`, so ordinary agent roles cannot set or tamper with
them. During `taskbound.bind_task(...)`, the database computes approved safe
view OIDs from `taskbound.safe_view_registry` and stores them in the trusted
GUC context for the current backend.

Before executing dynamic SQL, `taskbound.run(sql_text)` calls
`public.sessionbound_guard_check(sql_text)`. That C function temporarily enables
the guard and calls `SPI_prepare`, which invokes PostgreSQL parse/analyze and
therefore the registered hook on the exact SQL text that is about to run. If the
hook denies the query, `taskbound.run(...)` records a denial receipt and raises
an error before execution.

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
| Safe-view `SELECT` | agent credential direct DB call | 1 / 1 allowed |
| `UNION`, catalog access, recursive CTE | agent credential direct DB call | 3 / 3 blocked |
| Runtime helper abuse | agent credential direct DB call | 1 / 1 blocked |
| Non-`SELECT`, raw schema, payload aggregation, unapproved safe-view OID | superuser trusted-GUC hook check, no API preflight | 4 / 4 blocked |
| Total | mixed direct database paths | 10 / 10 passed |

The hook evaluation intentionally bypasses the `/agent-query` API preflight for
the agent cases by connecting directly as the generated runtime credential. The
hook-only cases use trusted superuser setup to exercise the extension path
without the API validator.

## Boundary

This is a real database-resident hook prototype, but it is still experimental.
It validates SQL through PostgreSQL parse/analyze before dynamic execution; it
does not yet provide a production-grade planner or executor hook, optimized
accounting, or out-of-transaction denial logging. The API-layer AST validator
remains as defense in depth and as a portable preflight path.
