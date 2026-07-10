# TDSC Hardening Changelog

Branch: `high-standard-tdsc-pdsc-revision`
Date: 2026-07-08

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
- Latest raw results:
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
- Minimum-group policy mitigates direct small-group aggregate release but not
  arbitrary semantic inference across multiple allowed answers.
- Current wrapper and native accounting paths remain too slow at 100k rows for
  production-scale performance claims.
- Production hardening still requires optimized executor integration,
  hardened key management, credential lifecycle cleanup, and external audit
  retention.
