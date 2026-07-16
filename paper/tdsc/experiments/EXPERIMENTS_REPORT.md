# Experiment Report

Status note, 2026-07-10: this report is superseded by the blocking audit in
`paper/revision_notes/tdsc_blocking_audit_20260710.md` and the detailed summary
review in `paper/revision_notes/tdsc_summary_experiment_review_20260710.md`.
Treat the runs below as diagnostic evidence, not as submission-ready proof of
fair external-PEP equivalence, 10k-row detail disclosure/materialization, or
complete native reference-monitor enforcement.

The experiment pass validates the current SessionBound prototype and
adds direct comparisons against raw PostgreSQL, role-only, safe-view-only,
RLS+Safe View+Short Credential+Audit, and SessionBound configurations.

## Completed Runs

| Area | Result | Raw artifact |
|---|---:|---|
| Functional validation | 24/24 scenarios passed | `../raw_results/sessionbound_agent_eval_1783515659.json` |
| Security baselines | 5 configurations tested | `../raw_results/security_baseline_1783515117.json` |
| Performance baselines | 7 modes x 5 query patterns | `../raw_results/overhead_breakdown_20260710_103837.json` |
| Credential-token and schema drift edge cases | 11 tests | `../raw_results/credential_token_1783515640.json` |
| Adversarial SQL | 140/140 expected classifications passed | `../raw_results/adversarial_sql_20260708_235742.json` |
| Hook/direct DB enforcement | 18/18 cases passed | `../raw_results/sessionbound_guard_hook_20260709_015110.json` |
| Hook-only structural microbenchmark | 6/6 checks passed | `../raw_results/hook_microbenchmark_20260708_205831.json` |
| Native end-to-end scale | 1k/10k with 100 measured iterations; 100k with 30 measured iterations | `../raw_results/native_end_to_end_20260709_013634.json` |
| Native projection/alias/aggregate budget accounting | 3/3 atomic zero-release cases passed | `../scripts/native_partial_budget_eval.py` |
| Historical wrapper scale sweep | 1k, 10k, and 100k scoped rows | `../raw_results/scale_1783515366.json` |
| Concurrency smoke test | 1, 5, and 20 concurrent sessions | `raw_results/concurrency_1783221968.json` |

## Interpretation

The prototype exercises the main task-boundary mechanisms for safe-view access,
denied fields, read-only SQL, row scope, query budget, disclosure budget,
payload aggregation blocking, receipts, credential-token binding, and
safe-view drift invalidation in the tested paths. The latest audit found native
reference-monitor bypass classes and incomplete JSON/window coverage, so these
mechanisms must not be described as complete native enforcement until rebuilt
and rerun. The RLS+safe-view+short-credential+audit baseline
blocks writes, raw table access, denied fields, and row-scope escapes while
emitting basic audit logs, but intentionally lacks SessionBound's task-token
binding, disclosure budget, receipt hash chain, and safe-view drift token
invalidation. The updated credential-token tests deny
credential mismatch, cross-credential replay, wrong audience, wrong
actor, expired token, revoked task state, same-session rebind, registry
version drift, policy-version drift, view-definition hash mismatch, and
exposed-column hash mismatch.

The overhead run confirms that receipt and budget-accounting switches work in
the tested wrapper/ablation paths, but small-dataset latency deltas are noisy.
The native end-to-end
scale benchmark shows severe overhead in both current SessionBound accounting
paths at 100k scoped rows: wrapper p50 is 6.11--6.24 s and native
hook/executor p50 is 6.18--6.30 s across SELECT/JOIN/GROUP BY/CTE/window
query shapes. The 1k and 10k scale targets use 100 measured iterations per
mode/pattern; the 100k target uses 30 measured iterations. The hook-only
microbenchmark separates this accounting bottleneck from native structural
guard cost. The native projection-budget test validates that an over-budget
direct safe-view SELECT, an aliased projection, and a grouped aggregate all
produce the same atomic zero-release denial; no `expense_id` projection is
required.

Important correction: the native end-to-end scale scripts prepare large scoped
tables, but the measured query shapes may return only small result sets. The
10k target in `native_end_to_end_20260710_110541.json` returns 10 rows for
GROUP BY/CTE and 50 rows for SELECT/JOIN/window. Do not use those runs as
evidence for 10k-row detail disclosure or 10k-row materialization.
