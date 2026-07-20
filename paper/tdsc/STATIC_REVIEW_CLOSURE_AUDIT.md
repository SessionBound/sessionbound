# Static Review Closure Audit

Date: 2026-07-19

Scope: closure evidence for the anonymous TDSC static review in
`/home/wmm/.codex/attachments/174041ec-0763-493c-af4d-c1c9a5b683e7/pasted-text-1.txt`.
This audit records the current source and artifact evidence for the review's
major issues M1-M6 and listed minor issues. It is intentionally claim-aligned:
where the implementation does not emit a task-chain receipt for PostgreSQL
parser/analyzer failures before SessionBound admission, the paper now scopes the
receipt claim accordingly.

2026-07-20 update: the follow-up static review in
`/home/wmm/.codex/attachments/a5865f96-dc53-48ba-a3c0-6a0c8d37bdfd/pasted-text-1.txt`
identified stricter counterexamples. The current source addresses them as
source-level closures and was rerun against the rebuilt Docker/PostgreSQL/API
stack in this workspace.

| Follow-up item | Source closure evidence |
|---|---|
| Prepared plan cross-binding reuse | `guard_check_cached_plan_source()` revalidates cached prepared-statement dependencies and selected-column permission metadata under the current binding; `bind_task()`/`unbind_task()` close portals and `DEALLOCATE ALL` explicit prepared plans. `sessionbound_guard_hook_20260720_080304.json` passes the same-binding prepared execution and cross-binding rebind cases. |
| Data-bearing database errors | Native executor failures and wrapper dynamic execution failures return fixed agent-facing errors and fixed receipt reasons; `native_reserve_query_status()` debits query count before execution so failed admitted attempts consume query budget. |
| Runtime entrypoints mixed into SELECT | `query_is_standalone_runtime_entrypoint()` rejects public `taskbound.*` runtime calls unless the query is a single top-level runtime call. |
| Controlled-command release-time/scope/rollback | `taskbound.command()` dispatches `command_apply()` through a dblink transaction; `command_apply()` revalidates binding identity with `clock_timestamp()`, requires `CONTROLLED_COMMAND`, writes business state and receipt together, and forces command writes to signed tenant/month/department scope. |
| Receipt uniqueness/completeness and EXPLAIN | Native validation denials write sanitized denial receipts when bound; `EXPLAIN` output and cursor/FETCH are denied; duplicate utility/error receipt paths are suppressed by `guard_error_receipt_recorded`. |
| Receipt hash canonical material | `taskbound.receipt_hash()` hashes typed JSONB material including `receipt_id`, `execution_id`, sequence, binding fence, decision, budget fields, touched-view array, timestamp, and previous hash. |
| Policy drift and task/credential claim | `safe_view_registry_snapshot()` hashes referenced function definitions and ACL-like metadata; `task_policy_registry` gives `bind_task()` an authoritative task-template policy version; `claim_active_binding_row()` atomically inserts-or-verifies task credential/token binding conflicts. |

## Major Issues

| Review item | Closure status | Current evidence |
|---|---|---|
| M1 native accounting bypass via raw SQL runtime-helper substrings | Closed in source and evaluated. `should_account_query()` no longer uses raw SQL `strstr()` exemptions; runtime entrypoints are recognized from parsed/planned function OIDs, and ordinary safe-view SQL containing helper-name strings is accounted. | `postgres/sessionbound_guard/sessionbound_guard.c` `planned_stmt_is_runtime_entrypoint_only()` and `should_account_query()`; `paper/tdsc/raw_results/native_text_bypass_probe.json` (`bypass_closed=true`, second query denied by query budget). |
| M2 temporary operator/function side effects and incomplete expression mediation | Closed in source and evaluated. Runtime credentials lose TEMP privileges; unbound runtime sessions can only call public SessionBound runtime entrypoints; bound search path excludes `pg_temp`; and the C walker checks operator/coercion procedure OIDs and volatility in addition to ordinary functions. | `db/006_commands_and_grants.sql` TEMP revocation; `postgres/sessionbound_guard/sessionbound_guard.c` pre-bind runtime-role denial plus checks for `OpExpr`, `DistinctExpr`, `ScalarArrayOpExpr`, `RowCompareExpr`, `ArrayCoerceExpr`, `CoerceToDomain`, `SubscriptingRef`, and `CoerceViaIO`; `paper/tdsc/raw_results/function_side_effect_20260720_001453.json` (13/13 cases, 38/38 path decisions, path-consistent and receipted); `paper/tdsc/raw_results/prebind_runtime_20260720_001459.json` (7/7 pre-bind mediation cases). |
| M3 binding lifecycle gaps: holdable cursor, release-time TTL, release-time drift | Closed in source and evaluated for the covered lifecycle cases. Holdable cursors are denied; `validate_active_binding()` and the release barrier recheck token expiry, credential validity/revocation/db user, database OID, registry snapshot, dependency hash, option hash, definition hash, and exposed-column hash. | `postgres/sessionbound_guard/sessionbound_guard.c` cursor/FETCH denials; `db/003_runtime_core.sql` `validate_active_binding()`; `db/005_query_runtime.sql` `native_finish_query_status()` release-barrier checks; `paper/tdsc/raw_results/sessionbound_guard_hook_20260720_080304.json` (20/20, including holdable cursor, prepared EXECUTE, and prepared-plan rebind); `paper/tdsc/raw_results/single_active_binding_20260720_001142.json` (single-active races and recovery passed). |
| M4 receipt completeness, uniqueness, and linearity | Closed for evaluated allowed executions and SessionBound admission/release-barrier denials after binding; scoped in the paper for native parser/analyzer failures before task-chain admission. Receipts now have per-task sequence uniqueness, explicit chain heads, previous-hash links, execution-id uniqueness, controlled-command allow/deny receipts, allowed-command rollback survival, and duplicate-denial suppression. | `db/001_schema.sql` receipt sequence/head schema; `db/005_query_runtime.sql` serialized `audit_append_receipt()` and release-barrier receipt insertion; `db/006_commands_and_grants.sql` autonomous controlled-command writes/receipts; `postgres/sessionbound_guard/sessionbound_guard.c` `guard_error_receipt_recorded` and `guard_suppress_receipts`; `paper/tdsc/raw_results/receipt_fault_20260720_000538.json` (8/8); `paper/tdsc/raw_results/rollback_audit_20260720_080542.json` (5/5); claim scoping in `paper/tdsc/sessionbound-tdsc.tex`. |
| M5 operation policy and path predicate mismatch | Closed for current claims and evaluated path corpus. `bind_task()` requires `operations` to permit `SELECT`; `taskbound.command()` requires `CONTROLLED_COMMAND`; schema-qualified safe-view SQL is accepted/accounted consistently across API, wrapper, and native paths, and mixed runtime entrypoints are blocked. | `db/003_runtime_core.sql` operations claim check; `db/006_commands_and_grants.sql` controlled-command operation check; `app/sql_ast_validator.py` blocks internal relations without blocking the `taskbound` safe-view schema; `paper/tdsc/raw_results/path_consistency_20260720_080527.json` (14/14, 42 path decisions, all path-consistent and receipted, PC13 covers `taskbound.expenses`, PC14 covers mixed `taskbound.unbind_task()`). |
| M6 unauthenticated control plane and caller-controlled security options | Closed in artifact and evaluated. Control-plane endpoints require `X-TaskBound-Control-Plane-Key`; requests cannot disable receipts or budget accounting. | `app/api.py` control-plane auth and runtime option checks; `docker-compose.yml` demo control-plane key; `paper/tdsc/raw_results/control_plane_auth_20260719_140226.json` (8/8). |

## Minor Issues

| Review item | Closure status | Current evidence |
|---|---|---|
| Formal state tuple inconsistent across paper/docs | Closed. The manuscript and invariant doc use `S=<A,T,C,G,V,L,B,R>`. | `paper/tdsc/sessionbound-tdsc.tex`; `paper/tdsc/SECURITY_INVARIANTS.md`; stale scan found no prior shorter or differently ordered tuple variants. |
| Aggregate/minimum-group language mismatched implementation | Closed by narrowing the implementation and claim. Direct aggregate/window release is denied pending reviewed templates; no minimum-group implementation is claimed. | `paper/tdsc/sessionbound-tdsc.tex`; `paper/tdsc/FINAL_HARDENING_REPORT.md`; `paper/tdsc/TDSC_READINESS.md`; `paper/tdsc/raw_results/aggregate_template_gating_20260720_001503.json` (12/12, 36/36 path decisions). |
| Safe-view snapshot missing database OID, dependencies, and view options | Closed in source and evaluated. | `db/003_runtime_core.sql` `safe_view_registry_snapshot()` includes `database_oid`, per-view OIDs, dependency hash, option hash, `view_options`, and `dependencies`; `paper/tdsc/raw_results/binding_lifecycle_20260719_142819.json`. |
| `rows_returned` records staged/authorized rows, not guaranteed client delivery | Closed by making the semantics explicit without a disruptive schema rename. | `db/001_schema.sql` and `db/005_query_runtime.sql` column comments; `postgres/sessionbound_guard/sessionbound_guard.c` native counter name/comment; `paper/tdsc/sessionbound-tdsc.tex` budget table; live DB comments verified after reapplying `005_query_runtime.sql`. |
| Safe-view yearly total vs controlled-command yearly total mismatch | Closed by aligning command totals with the safe-view row-scope semantics and documenting that the year-bucket total does not expose out-of-scope months. | `db/004_safe_views.sql` `yearly_employee_total` comment; `db/006_commands_and_grants.sql` command totals use tenant, employee, expense month, optional department, and same year bucket. |
| Progent title and Sieve bibliographic metadata | Closed. Progent title is `Programmable Privilege Control for LLM Agents`; Sieve is PVLDB 13(12):2424-2437. | `paper/tdsc/references.bib`. |
| Novelty/related-work overclaim and missing nearest neighbors | Closed by narrowing contribution wording and splitting the comparison matrix. | `paper/tdsc/sessionbound-tdsc.tex`; `paper/tdsc/references.bib`; `paper/tdsc/RELATED_WORK_EXPANSION.md` now includes ShillDB, Estrela, PICACHV, and separates Qapla, Blockaid, Sieve, agent policies, and PAuth. |

## Validation Snapshot

- `make -C paper/tdsc` succeeds; current PDF is 17 pages.
- `make -C postgres/sessionbound_guard CC=gcc` compiles the extension source.
- `docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d travel -f /docker-entrypoint-initdb.d/003_runtime_core.sql -f /docker-entrypoint-initdb.d/004_safe_views.sql -f /docker-entrypoint-initdb.d/005_query_runtime.sql -f /docker-entrypoint-initdb.d/006_commands_and_grants.sql` applies cleanly.
- Rebuilt Docker stack: `taskbounddb-postgres` healthy on port 15432 and API
  responding on port 8000.
- Passing 2026-07-20 runtime results: hook 20/20, path consistency 14/14,
  receipt fault 8/8, rollback audit 5/5, native partial budget 1/1,
  single-active binding 100 races plus lifecycle checks, scope completeness
  4/4 scenarios, dynamic denied fields 7/7, function side effects 13/13,
  pre-bind runtime 7/7, aggregate/window gating 12/12, AST validation 21/21.
- `git diff --check` passes.
- LaTeX/BibTeX log scan finds no errors, undefined references, undefined
  citations, or overfull boxes.
- Postgres logs after the successful SQL apply show no backend crash; the
  historical fail-receipt triage raw result reports `count=0` and no triggers.
