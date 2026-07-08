# TDSC Hardening Changelog

Branch: `high-standard-tdsc-pdsc-revision`
Date: 2026-07-08

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
