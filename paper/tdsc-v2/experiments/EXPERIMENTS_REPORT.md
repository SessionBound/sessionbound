# v2 Experiment Report

The v2 experiment pass validates the current SessionBound prototype and
adds direct comparisons against raw PostgreSQL, role-only, safe-view-only,
and RLS-only configurations.

## Completed Runs

| Area | Result | Raw artifact |
|---|---:|---|
| Functional validation | 24/24 scenarios passed | `raw_results/eval_runs/sessionbound_agent_eval_1783221284.json` |
| Security baselines | 5 configurations tested | `raw_results/security_baseline_1783221673.json` |
| Performance baselines | 5 configurations x 5 query patterns | `raw_results/performance_1783221625.json` |
| Credential-token edge cases | 7 tests | `raw_results/credential_token_1783222420.json` |
| Concurrency smoke test | 1, 5, and 20 concurrent sessions | `raw_results/concurrency_1783221968.json` |

## Interpretation

The prototype enforces the main task boundary for safe-view access,
denied fields, read-only SQL, row scope, query budget, disclosure budget,
payload aggregation blocking, and receipts. The credential-token tests
also show a real prototype gap: signed token expiration and task
revocation are enforced, but strict credential-id binding, actor matching,
audience validation, token replay blocking across a second credential,
and same-session rebind blocking are not implemented in the measured
code.

Scale is represented only by the default seed data and a concurrency
smoke test. The run does not constitute a large-scale benchmark.
