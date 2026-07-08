# Experiment Report

The experiment pass validates the current SessionBound prototype and
adds direct comparisons against raw PostgreSQL, role-only, safe-view-only,
RLS+Safe View+Short Credential+Audit, and SessionBound configurations.

## Completed Runs

| Area | Result | Raw artifact |
|---|---:|---|
| Functional validation | 24/24 scenarios passed | `../raw_results/sessionbound_agent_eval_1783515659.json` |
| Security baselines | 5 configurations tested | `../raw_results/security_baseline_1783515117.json` |
| Performance baselines | 7 modes x 5 query patterns | `../raw_results/overhead_breakdown_20260708_205527.json` |
| Credential-token and schema drift edge cases | 11 tests | `../raw_results/credential_token_1783515640.json` |
| Adversarial SQL | 34/34 expected classifications passed | `../raw_results/adversarial_sql_20260708_210059.json` |
| Hook/direct DB enforcement | 18/18 cases passed | `../raw_results/sessionbound_guard_hook_20260708_205325.json` |
| Hook-only structural microbenchmark | 6/6 checks passed | `../raw_results/hook_microbenchmark_20260708_205831.json` |
| Scale sweep | 1k, 10k, and 100k scoped rows | `../raw_results/scale_1783515366.json` |
| Concurrency smoke test | 1, 5, and 20 concurrent sessions | `raw_results/concurrency_1783221968.json` |

## Interpretation

The prototype enforces the main task boundary for safe-view access,
denied fields, read-only SQL, row scope, query budget, disclosure budget,
payload aggregation blocking, receipts, credential-token binding, and
safe-view drift invalidation. The RLS+safe-view+short-credential+audit baseline
blocks writes, raw table access, denied fields, and row-scope escapes while
emitting basic audit logs, but intentionally lacks SessionBound's task-token
binding, disclosure budget, receipt hash chain, and safe-view drift token
invalidation. The updated credential-token tests deny
credential mismatch, cross-credential replay, wrong audience, wrong
actor, expired token, revoked task state, same-session rebind, registry
version drift, policy-version drift, view-definition hash mismatch, and
exposed-column hash mismatch.

The overhead run confirms that receipt and budget-accounting switches work as
intended, but small-dataset latency deltas are noisy. The scale sweep shows
severe Full SessionBound overhead at 100k scoped rows, supporting the
manuscript's claim that the accounting-complete wrapper implementation is a
security reference path rather than a production performance path. The hook-only
microbenchmark separates this wrapper bottleneck from native structural guard
cost.
