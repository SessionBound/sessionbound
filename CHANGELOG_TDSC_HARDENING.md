# TDSC Hardening Changelog

Branch: `high-standard-tdsc-pdsc-revision`
Date: 2026-07-19

## 2026-07-19 Static-Review Rebuild Pass

- Scoped all demo safe views to the full signed task scope. `departments`,
  `employees`, `approval_events`, and `ledger_entries` now derive visibility
  through in-scope expense rows, so month/department-scoped tasks cannot
  enumerate tenant-wide dimensions, workflow events, or ledger entries.
- Made bind-time token validation stricter: task tokens must carry
  `credential_id`, registry drift claims, non-empty `allowed_views`,
  `row_scope.expense_month`, and safe-view scope coverage for every non-empty
  row-scope claim.
- Added bind-time rejection when an approved safe view still exposes a
  token-denied column, and passed the normalized denied-column set into the
  native guard for parse-tree column/alias checks.
- Switched native function policy from catalog-default-allow to
  allowlist-default-deny, while permitting only audited scalar/aggregate/cast
  functions needed by normal safe-view SQL.
- Removed direct agent grants for `fail_receipt` and `audit_append_receipt`;
  direct `fail_receipt` calls are now denied by the guard.
- Added `execution_id`, `actor`, `touched_views`, and a unique
  `(task_id, execution_id)` receipt key. The receipt hash now covers execution
  id, binding/fence identity, budget transition fields, actor, timestamp, and
  previous hash. The one-second receipt de-duplication window was removed.
- Populated `touched_views` for API preflight denials, wrapper receipts, and
  native query receipts. Safely parseable bound database SQL uses
  PostgreSQL parse/analyze safe-view OIDs mapped back to registry names; API
  preflight and unsupported denied syntax use an approved-name fallback.
  Enforcement remains AST/OID-based; this field is hashed audit context.
- Added explicit `TABLESAMPLE` rejection in the API validator, wrapper path,
  and native relation guard, and kept provenance extraction conservative for
  unsupported syntax that should be denied before execution.
- Added a path-consistency evaluator for the API endpoint, direct
  `taskbound.run(...)` wrapper, and direct native safe-view SQL. The evaluator
  uses a fresh task and credential for each `(case, path)` pair and compares
  allow/deny classification, normalized denial reason, budget state, and
  receipts.
- Hardened wrapper-side aggregate/window and `TABLESAMPLE` denials so they
  fail closed before execution with autonomous denial receipts rather than
  relying on exception paths. Native default-denied function policy now emits
  the same receipt-bearing denial path.
- Moved allowed-release query-count debit, output-tuple debit, and receipt
  append into one fenced release-barrier SQL transition on the audit
  connection. `native_reserve_query` is now a preflight budget/state check
  rather than an early mutation.
- Fixed the native full-result `SELECT` lifecycle so the guard commits the
  fenced release-barrier transition and replays buffered tuples to the client
  from the successful `ExecutorRun` path. This prevents the native path from
  accounting rows without delivering them for ordinary direct selects.
- Hardened the Postgres Docker build with `make clean` before `make install`,
  preventing a host-compiled extension artifact from being copied into a
  different Postgres major-version image.
- Replaced cardinality-alias group checks with conservative aggregate-template
  gating: all direct aggregate release, including ungrouped aggregates,
  ad-hoc `GROUP BY`, `HAVING`, filtered aggregate release, and window release,
  is denied across API, wrapper, and native paths until trusted
  provenance/cardinality templates exist.
- Targeted validation on an isolated Docker database passed:
  scoped `departments`/`employees`/`approval_events`/`ledger_entries` returned
  only `dep_sales` for a `2026-06 + dep_sales` token; direct native `SELECT`
  returned buffered detail rows after the release barrier; native ungrouped
  aggregate, forged `employee_count` group, filtered aggregate, window,
  `pg_sleep`, and direct `fail_receipt` probes were denied; two rapid allowed
  queries produced two distinct execution ids and receipt hashes; a token
  adding `amount` to `denied_columns` failed at bind.

## 2026-07-19 Result Refresh

- AST validation: `ast_validation_20260719_162614.json`, 21 / 21.
- Adversarial SQL: `adversarial_sql_20260719_162614.json`, 140 / 140
  with 130 blocked and 10 allowed/accounted.
- Native hook/executor: `sessionbound_guard_hook_20260719_162553.json`,
  18 / 18.
- Rollback audit: `rollback_audit_20260719_162553.json`, 5 / 5, including
  API preflight touched-view provenance.
- Credential-token and safe-view drift:
  `paper/tdsc/experiments/raw_results/credential_token_1784444204.json`,
  11 / 11 observed denials.
- Safe-view scope completeness:
  `scope_completeness_20260719_112307.json`, 4 / 4 scenarios and 14 / 14
  non-vacuous view checks. The evaluator compares each task-allowed safe
  view's returned rows with raw-table tenant/month/department provenance for
  monthly, finance workflow, and payment ledger task shapes.
- Dynamic token-denied field coverage:
  `dynamic_denied_field_20260719_112651.json`, 7 / 7 cases and 21 / 21
  API, direct-wrapper, and direct-native path decisions. A control token can
  read `expenses.amount`; a token that dynamically adds `expenses.amount` to
  `denied_columns` is rejected at bind for projection, alias, `WHERE`,
  `ORDER BY`, aggregate-input, and window-expression shapes.
- Aggregate-template gating:
  `aggregate_template_gating_20260719_113133.json`, 12 / 12 cases and
  36 / 36 API, direct-wrapper, and direct-native path decisions. The blocked
  cases cover forged `employee_count`/`entity_count`/distinct-employee aliases,
  filtered ungrouped aggregate over a raw-provenance one-employee merchant,
  ordinary category/merchant/city grouping, CTE/subquery aggregate release, and
  window partition release. Each blocked decision emitted exactly one denial
  receipt.
- Function side-effect/default-deny policy:
  `function_side_effect_20260719_113937.json`, 12 / 12 cases and 36 / 36
  API, direct-wrapper, and direct-native path decisions. The blocked cases
  cover `pg_sleep`, session advisory locks, `pg_notify`, `set_config`,
  `current_setting`, privilege/file introspection, SRFs, payload serialization,
  and an unlisted aggregate. The side-effect oracles confirmed no one-second
  sleep delay, no held tested advisory lock, and no delivered notification.
- Receipt fault/forgeability:
  `receipt_fault_20260719_114420.json`, 5 / 5. The evaluator recomputes the
  hardened receipt hashes from database rows, verifies previous-hash chaining,
  required hardened fields, unique execution ids, and tamper sensitivity,
  confirms a wrong-fence trusted append inserts no receipt, and blocks direct
  agent attempts to call `fail_receipt`, `audit_append_receipt`,
  `native_finish_query`, or insert into the receipt table.
- Concurrent isolation: `concurrent_isolation_20260719_065546.json`, 6 / 6.
- Single-active binding: `single_active_binding_20260719_065728.json`,
  passed with 20 repeated races, 10 contenders, zero dual-success rounds,
  zero zero-winner rounds, and zero unexpected errors.
- Native partial budget: `native_partial_budget_20260719_162615.json`, 1 / 1.
- SDK direct-query smoke: `sdk_query_20260719_082553.json`, 3 / 3.
- Functionally equivalent external-PEP cumulative baseline:
  `functional_equivalent_baseline_20260719_164723.json`, 8 / 8; covers the
  same signed task contract, credential binding, cumulative query/tuple
  budgets, persistent PEP state, execution-id idempotence, chained receipts,
  and direct-role bypass TCB distinction.
- Path consistency:
  `path_consistency_20260719_184242.json`, 12 / 12; compares 36 API,
  direct-wrapper, and direct-native path observations. Two direct-native
  observations are explicitly scoped out of receipt equivalence because
  PostgreSQL rejects them before the native policy hook can issue a task
  receipt: hidden-column lookup on the hardened safe view and `TABLESAMPLE` on
  a view.
- Overhead breakdown: `overhead_breakdown_20260719_150951.json`; supported
  full SessionBound detail-query p50 is 151.2--159.0 ms, while direct
  aggregate/window release rows are marked unsupported by policy.
- Hook-only microbenchmark: `hook_microbenchmark_20260719_150550.json`;
  structural-check p50 is 0.101--0.129 ms for supported detail shapes, and
  raw-schema, UNION, `GROUP BY`, and window denials passed.
- Native end-to-end diagnostic:
  `native_end_to_end_20260719_152051.json`, run with
  `TDSC_NATIVE_ROWS=1000,10000`, `TDSC_NATIVE_WARMUP=1`, and
  `TDSC_NATIVE_MEASURED=3`; supported 10k detail-release p50 is
  5.03--5.15 s for the wrapper path and 4.85--4.91 s for the native
  hook/executor path, with zero query errors.
- Updated `paper/tdsc/sessionbound-tdsc.tex`,
  `paper/tdsc/SECURITY_INVARIANTS.md`, and
  `paper/tdsc/SUBMISSION_STATUS.md` to use the rerun evidence and to avoid
  claiming ad-hoc aggregate/window release support before approved aggregate
  templates exist.
- Added a nearest-neighbor related-work matrix covering TBAC/UCON,
  Macaroons, Qapla/Blockaid/Sieve, Progent/AgentSpec/Task Shield/PAuth, and
  CaMeL/IFC agents. The manuscript now states the narrowed novelty claim as a
  PostgreSQL-centered task-session composition, not a new standalone
  authorization model.
- Refreshed official IEEE Computer Society/TDSC submission-format checks on
  2026-07-19 and shortened the manuscript abstract from 264 to 199 words. The
  rebuilt PDF remains 16 pages with embedded Type 1 fonts and a clean strict
  LaTeX log scan.

## 2026-07-10 Global Single-Active Binding

- Implemented exact-key global binding ownership for
  `task_id + token_digest + credential_id`.
- Added PostgreSQL session-level advisory locks as the authoritative
  single-active mutex, with non-blocking `pg_try_advisory_lock` denial mapped
  to `ACTIVE_BINDING_EXISTS` / SQLSTATE `55P03` / HTTP 409.
- Added protected active binding state with token digest, signed nonce,
  credential id, `binding_id`, monotonic `fence_token`, advisory-lock key,
  database OID, lock backend, owner PID/backend-start/postmaster-start/session
  user, acquisition times, and token expiry.
- Fenced all budget, receipt, cleanup, and unbind mutations with
  `binding_id + fence_token`; fence mismatches emit `BINDING_FENCED`.
- Added crash and disconnect recovery that first reacquires the advisory lock
  and then verifies owner death using PID, backend start, postmaster start, and
  database OID. `last_seen_at` is diagnostic only.
- Hardened the native guard against advisory unlock, `DISCARD ALL`, direct
  guard-clear calls, untrusted session-state utility commands, and rollback
  processing from aborted transactions.
- Added `docs/GLOBAL_SINGLE_ACTIVE_BINDING.md`.
- Added `paper/tdsc/scripts/single_active_binding_eval.py`.
- Final full result:
  `paper/tdsc/raw_results/single_active_binding_20260710_030846.json`.
  The 1000-round same-key race had 0 dual-success rounds, 0 zero-owner rounds,
  and 0 unexpected errors; the 20-contender race produced exactly 1 owner and
  19 deterministic denials; explicit unbind, socket close, backend termination,
  live-owner non-eviction, PID reuse protection, rollback semantics, old-fence
  rejection, and different-key concurrency passed.

## 2026-07-10 Result Refresh

- Canonical validation: `sessionbound_agent_eval_1783653370.json`, 24 / 24.
- AST validation: `ast_validation_20260710_103428.json`, 20 / 20.
- Adversarial SQL: `adversarial_sql_20260710_110551.json`, 140 / 140.
- Native hook/executor: `sessionbound_guard_hook_20260710_110558.json`,
  18 / 18.
- Rollback audit: `rollback_audit_20260710_110603.json`, 2 / 2.
- Native partial budget: `native_partial_budget_20260710_110604.json`, 1 / 1.
- Concurrent isolation: `concurrent_isolation_20260710_030604.json`, 6 / 6.
- Overhead breakdown: `overhead_breakdown_20260710_103837.json`; full
  SessionBound p50 is now 128.4--136.1 ms after statement-start owner/fence
  validation.
- Hook-only microbenchmark: `hook_microbenchmark_20260710_103846.json`;
  structural-check p50 is 0.127--0.176 ms.
- Bounded native end-to-end benchmark:
  `native_end_to_end_20260710_110541.json`, run with
  `TDSC_NATIVE_ROWS=1000,10000`, `TDSC_NATIVE_WARMUP=1`, and
  `TDSC_NATIVE_MEASURED=3`; 10k wrapper p50 is 5.08--5.25 s and 10k native
  p50 is 5.16--5.38 s.

## Claim and Manuscript Hardening

- Reframed SessionBound as a task-bound database-session state machine:
  approval, credential, signed token, safe-view registry, SQL surface, budget,
  and receipts are one execution-time authorization object.
- Added three manuscript figures:
  - Figure 1: SessionBound architecture.
  - Figure 2: bind/query decision pipeline.
  - Figure 3: performance diagnosis separating hook-only structural checks
    from accounting-complete execution.
- Rewrote the abstract and evaluation text to distinguish:
  - wrapper/API reference path;
  - native PostgreSQL hook/executor path;
  - hook-only structural microbenchmark.
- Rewrote Proposition 3 to match implemented budget semantics:
  - wrapper path materializes and checks candidate results before release;
  - native path checks each outgoing tuple before forwarding it and records the
    accepted prefix on denial;
  - native streaming is not claimed to be all-or-nothing for earlier tuples in
    a query that later exceeds budget.
- Added `paper/tdsc/scripts/native_partial_budget_eval.py`; latest raw result
  is `paper/tdsc/raw_results/native_partial_budget_20260709_014913.json`.

## Security Suite Hardening

- Expanded `paper/tdsc/scripts/adversarial_sql_eval.py` from the earlier
  34-case suite to 140 cases.
- Latest adversarial raw result:
  `paper/tdsc/raw_results/adversarial_sql_20260708_235742.json`.
- Result: 140 / 140 expected classifications passed.
- Classification summary:
  - 126 blocked;
  - 14 allowed and budget-accounted;
  - all tested direct small-group aggregate-release attempts blocked.

## Native End-to-End Benchmark

- Added `paper/tdsc/scripts/native_end_to_end_benchmark.py`.
- Earlier raw results, now superseded by the 2026-07-19 result refresh:
  - `paper/tdsc/raw_results/native_end_to_end_20260709_013634.json`
  - `paper/tdsc/raw_results/native_end_to_end_20260709_013634.csv`
- Modes:
  - Raw PostgreSQL;
  - Safe-view-only;
  - RLS + Safe View + Short Credential + Audit;
  - SessionBound wrapper;
  - SessionBound native hook/executor.
- Query shapes: SELECT, JOIN, GROUP BY, CTE, Window.
- Scale targets: 1k, 10k, 100k scoped rows.
- Key result: current native hook/executor accounting is measured but not
  optimized. At 100k rows, wrapper p50 is 6108.33--6236.90 ms and native
  hook/executor p50 is 6176.94--6298.30 ms across the five query shapes.

## Artifact and Report Updates

- Updated adversarial, scale, overhead, readiness, manifest, and experiment
  reports to use the 140-case adversarial result and the native end-to-end
  benchmark.
- Added manuscript artifact reproducibility commands and cited raw result files.
- Removed stale claims that native executor-accounting scale was unmeasured.
- Kept the hook-only microbenchmark explicitly scoped to parse/analyze
  structural checks with no row execution, no receipts, and no accounting.

## Remaining Limits

- The prototype is not a formal SQL safety proof.
- Disclosure budgets are operational accounting, not differential privacy.
- Direct aggregate/window release is denied pending approved aggregate
  templates, but arbitrary semantic inference across multiple allowed detail
  answers remains out of scope.
- Current wrapper and native accounting paths remain too slow at 10k detail
  rows for production-scale performance claims.
- Production hardening still requires optimized executor integration,
  hardened key management, credential lifecycle cleanup, and external audit
  retention.
