# Scale and Concurrency Results

Scale raw file: `raw_results/scale_1783224892.json`.
Concurrency raw file: `raw_results/concurrency_1783221968.json`.

The scale sweep temporarily inserted synthetic `scale_bench_*` expense
rows for the June 2026 Sales scope and cleaned them after each target.
The final database was restored to zero `scale_bench_*` rows.

| Target scoped rows | Pattern | Raw p50 ms | Safe-view-only p50 ms | RLS-only p50 ms | Full SessionBound p50 ms |
|---:|---|---:|---:|---:|---:|
| 1,000 | aggregate_by_category | 0.41 | 0.60 | 0.39 | 84.41 |
| 1,000 | topk_order | 0.40 | 0.53 | 0.43 | 82.44 |
| 10,000 | aggregate_by_category | 2.70 | 3.76 | 2.38 | 688.66 |
| 10,000 | topk_order | 2.54 | 3.32 | 2.39 | 661.62 |
| 100,000 | aggregate_by_category | 14.77 | 29.96 | 15.08 | 6742.55 |
| 100,000 | topk_order | 15.62 | 32.39 | 14.61 | 6965.09 |

The 100k result exposes substantial scale-sensitive overhead in the
current PL/pgSQL prototype and safe-view shape. It should be read as a
reference-runtime limitation, not as a production throughput claim.

| Concurrent sessions | Successful sessions | Failed sessions | Error rate | Query p50 ms | Query p95 ms | Session p50 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0.0% | 29.894 | 30.995 | 175.331 |
| 5 | 5 | 0 | 0.0% | 36.296 | 39.004 | 203.131 |
| 20 | 20 | 0 | 0.0% | 82.896 | 110.356 | 467.768 |

This is a concurrency smoke test for the demo stack, not a throughput
capacity claim. It includes HTTP calls, task/credential issuance, and
query execution through the API.
