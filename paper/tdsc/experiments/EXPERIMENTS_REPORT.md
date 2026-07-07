# Experiment Report

The experiment pass validates the current SessionBound prototype and
adds direct comparisons against raw PostgreSQL, role-only, safe-view-only,
and RLS-only configurations.

## Completed Runs

| Area | Result | Raw artifact |
|---|---:|---|
| Functional validation | 24/24 scenarios passed | `raw_results/eval_runs/sessionbound_agent_eval_1783224117.json` |
| Security baselines | 5 configurations tested | `raw_results/security_baseline_1783221673.json` |
| Performance baselines | 5 configurations x 5 query patterns | `raw_results/performance_1783221625.json` |
| Credential-token and schema drift edge cases | 10 tests | `raw_results/credential_token_1783224156.json` |
| Receipt/budget ablation | 4 SessionBound variants x 3 patterns | `raw_results/ablation_1783224624.json` |
| Scale sweep | 1k, 10k, and 100k scoped rows | `raw_results/scale_1783224892.json` |
| Concurrency smoke test | 1, 5, and 20 concurrent sessions | `raw_results/concurrency_1783221968.json` |

## Interpretation

The prototype enforces the main task boundary for safe-view access,
denied fields, read-only SQL, row scope, query budget, disclosure budget,
payload aggregation blocking, receipts, credential-token binding, and
safe-view drift invalidation. The updated credential-token tests deny
credential mismatch, cross-credential replay, wrong audience, wrong
actor, expired token, revoked task state, same-session rebind, registry
version drift, policy-version drift, and view-definition hash mismatch.

The ablation run confirms that receipt and budget-accounting switches
work as intended, but small-dataset latency deltas are noisy. The scale
sweep shows severe Full SessionBound overhead at 100k scoped rows,
supporting the manuscript's claim that this PL/pgSQL implementation is a
security reference prototype rather than a production performance path.
