# Static Review Closure Audit

Date: 2026-07-19

Scope: closure evidence for the anonymous TDSC static review in
`/home/wmm/.codex/attachments/174041ec-0763-493c-af4d-c1c9a5b683e7/pasted-text-1.txt`.
This audit records the current source and artifact evidence for the review's
major issues M1-M6 and listed minor issues. It is intentionally claim-aligned:
where the implementation does not emit a task-chain receipt for PostgreSQL
parser/analyzer failures before SessionBound admission, the paper now scopes the
receipt claim accordingly.

## Major Issues

| Review item | Closure status | Current evidence |
|---|---|---|
| M1 native accounting bypass via raw SQL runtime-helper substrings | Closed in source and evaluated. `should_account_query()` no longer uses raw SQL `strstr()` exemptions; runtime entrypoints are recognized from parsed/planned function OIDs, and ordinary safe-view SQL containing helper-name strings is accounted. | `postgres/sessionbound_guard/sessionbound_guard.c` `planned_stmt_is_runtime_entrypoint_only()` and `should_account_query()`; `paper/tdsc/raw_results/native_text_bypass_probe.json` (`bypass_closed=true`, second query denied by query budget). |
| M2 temporary operator/function side effects and incomplete expression mediation | Closed in source and evaluated. Runtime credentials lose TEMP privileges; unbound runtime sessions can only call public SessionBound runtime entrypoints; bound search path excludes `pg_temp`; and the C walker checks operator/coercion procedure OIDs and volatility in addition to ordinary functions. | `db/006_commands_and_grants.sql` TEMP revocation; `postgres/sessionbound_guard/sessionbound_guard.c` pre-bind runtime-role denial plus checks for `OpExpr`, `DistinctExpr`, `ScalarArrayOpExpr`, `RowCompareExpr`, and `CoerceViaIO`; `paper/tdsc/raw_results/function_side_effect_20260719_150354.json` (13/13 cases, 38/38 path decisions, path-consistent and receipted); `paper/tdsc/raw_results/prebind_runtime_20260719_150119.json` (7/7 pre-bind mediation cases). |
| M3 binding lifecycle gaps: holdable cursor, release-time TTL, release-time drift | Closed in source and evaluated for the covered lifecycle cases. Holdable cursors are denied; `validate_active_binding()` and the release barrier recheck token expiry, credential validity/revocation/db user, database OID, registry snapshot, dependency hash, option hash, definition hash, and exposed-column hash. | `postgres/sessionbound_guard/sessionbound_guard.c` `CURSOR_OPT_HOLD` denials; `db/003_runtime_core.sql` `validate_active_binding()`; `db/005_query_runtime.sql` `native_finish_query_status()` release-barrier checks; `paper/tdsc/raw_results/sessionbound_guard_hook_20260719_230317.json` (19/19, including holdable cursor); `paper/tdsc/raw_results/binding_lifecycle_20260719_142819.json` (4/4, including release-time expiry and view-option drift). |
| M4 receipt completeness, uniqueness, and linearity | Closed for evaluated allowed executions and SessionBound admission/release-barrier denials after binding; scoped in the paper for native parser/analyzer failures before task-chain admission. Receipts now have per-task sequence uniqueness, explicit chain heads, previous-hash links, execution-id uniqueness, controlled-command allow/deny receipts, and duplicate-denial suppression. | `db/001_schema.sql` receipt sequence/head schema; `db/005_query_runtime.sql` serialized `audit_append_receipt()` and release-barrier receipt insertion; `db/006_commands_and_grants.sql` controlled-command receipts; `postgres/sessionbound_guard/sessionbound_guard.c` `denied_recorded` and `guard_suppress_receipts`; `paper/tdsc/raw_results/receipt_fault_20260719_135735.json` (7/7); claim scoping in `paper/tdsc/sessionbound-tdsc.tex`. |
| M5 operation policy and path predicate mismatch | Closed for current claims and evaluated path corpus. `bind_task()` requires `operations` to permit `SELECT`; `taskbound.command()` requires `CONTROLLED_COMMAND`; schema-qualified safe-view SQL is accepted/accounted consistently across API, wrapper, and native paths. | `db/003_runtime_core.sql` operations claim check; `db/006_commands_and_grants.sql` controlled-command operation check; `app/sql_ast_validator.py` blocks internal relations without blocking the `taskbound` safe-view schema; `paper/tdsc/raw_results/path_consistency_20260719_215758.json` (13/13, 39 path decisions, all path-consistent and receipted, PC13 covers `taskbound.expenses`). |
| M6 unauthenticated control plane and caller-controlled security options | Closed in artifact and evaluated. Control-plane endpoints require `X-TaskBound-Control-Plane-Key`; requests cannot disable receipts or budget accounting. | `app/api.py` control-plane auth and runtime option checks; `docker-compose.yml` demo control-plane key; `paper/tdsc/raw_results/control_plane_auth_20260719_140226.json` (8/8). |

## Minor Issues

| Review item | Closure status | Current evidence |
|---|---|---|
| Formal state tuple inconsistent across paper/docs | Closed. The manuscript and invariant doc use `S=<A,T,C,G,V,L,B,R>`. | `paper/tdsc/sessionbound-tdsc.tex`; `paper/tdsc/SECURITY_INVARIANTS.md`; stale scan found no prior shorter or differently ordered tuple variants. |
| Aggregate/minimum-group language mismatched implementation | Closed by narrowing the implementation and claim. Direct aggregate/window release is denied pending reviewed templates; no minimum-group implementation is claimed. | `paper/tdsc/sessionbound-tdsc.tex`; `paper/tdsc/FINAL_HARDENING_REPORT.md`; `paper/tdsc/TDSC_READINESS.md`; `paper/tdsc/raw_results/aggregate_template_gating_20260719_113133.json` (12/12, 36/36 path decisions). |
| Safe-view snapshot missing database OID, dependencies, and view options | Closed in source and evaluated. | `db/003_runtime_core.sql` `safe_view_registry_snapshot()` includes `database_oid`, per-view OIDs, dependency hash, option hash, `view_options`, and `dependencies`; `paper/tdsc/raw_results/binding_lifecycle_20260719_142819.json`. |
| `rows_returned` records staged/authorized rows, not guaranteed client delivery | Closed by making the semantics explicit without a disruptive schema rename. | `db/001_schema.sql` and `db/005_query_runtime.sql` column comments; `postgres/sessionbound_guard/sessionbound_guard.c` native counter name/comment; `paper/tdsc/sessionbound-tdsc.tex` budget table; live DB comments verified after reapplying `005_query_runtime.sql`. |
| Safe-view yearly total vs controlled-command yearly total mismatch | Closed by aligning command totals with the safe-view row-scope semantics and documenting that the year-bucket total does not expose out-of-scope months. | `db/004_safe_views.sql` `yearly_employee_total` comment; `db/006_commands_and_grants.sql` command totals use tenant, employee, expense month, optional department, and same year bucket. |
| Progent title and Sieve bibliographic metadata | Closed. Progent title is `Programmable Privilege Control for LLM Agents`; Sieve is PVLDB 13(12):2424-2437. | `paper/tdsc/references.bib`. |
| Novelty/related-work overclaim and missing nearest neighbors | Closed by narrowing contribution wording and splitting the comparison matrix. | `paper/tdsc/sessionbound-tdsc.tex`; `paper/tdsc/references.bib`; `paper/tdsc/RELATED_WORK_EXPANSION.md` now includes ShillDB, Estrela, PICACHV, and separates Qapla, Blockaid, Sieve, agent policies, and PAuth. |

## Validation Snapshot

- `make -C paper/tdsc` succeeds; current PDF is 17 pages.
- `make -C postgres/sessionbound_guard CC=gcc` compiles the extension source.
- `docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d travel -f /docker-entrypoint-initdb.d/005_query_runtime.sql` applies cleanly.
- `python paper/tdsc/scripts/prebind_runtime_eval.py --base-url http://localhost:8000`
  passes 7 / 7 against the rebuilt PostgreSQL hook.
- `git diff --check` passes.
- LaTeX/BibTeX log scan finds no errors, undefined references, undefined
  citations, or overfull boxes.
- Postgres logs after the successful SQL apply show no backend crash; the
  historical fail-receipt triage raw result reports `count=0` and no triggers.
