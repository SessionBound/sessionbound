# Scale and Concurrency Results

Scale raw file: `../raw_results/scale_1783515366.json`.
Concurrency raw file: `raw_results/concurrency_1783221968.json` (historical
smoke test retained for context only).

The scale sweep temporarily inserted synthetic `scale_bench_*` expense
rows for the June 2026 Sales scope and cleaned them after each target.
The final database was restored to zero `scale_bench_*` rows.

| Target scoped rows | Pattern | Raw p50 ms | Safe-view-only p50 ms | RLS+Safe View+Audit p50 ms | Full SessionBound p50 ms |
|---:|---|---:|---:|---:|---:|
| 1,000 | aggregate_by_category | 0.86 | 1.02 | 2.00 | 79.03 |
| 1,000 | topk_order | 0.43 | 0.64 | 1.46 | 72.63 |
| 10,000 | aggregate_by_category | 5.39 | 6.60 | 7.49 | 618.95 |
| 10,000 | topk_order | 2.02 | 2.86 | 3.65 | 660.25 |
| 100,000 | aggregate_by_category | 68.81 | 82.31 | 74.87 | 6365.20 |
| 100,000 | topk_order | 13.82 | 30.70 | 19.93 | 6169.55 |

The 100k result exposes substantial scale-sensitive overhead in the
accounting-complete wrapper reference path. It should be read as a
reference-runtime limitation, not as a production throughput claim. The current
TDSC hardening workspace adds a separate hook-only microbenchmark at
`../raw_results/hook_microbenchmark_20260708_205831.json`, which shows that
native structural guard checks are not the multi-second bottleneck. Native
executor-accounting scale behavior remains to be measured.

| Concurrent sessions | Successful sessions | Failed sessions | Error rate | Query p50 ms | Query p95 ms | Session p50 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0.0% | 29.894 | 30.995 | 175.331 |
| 5 | 5 | 0 | 0.0% | 36.296 | 39.004 | 203.131 |
| 20 | 20 | 0 | 0.0% | 82.896 | 110.356 | 467.768 |

This is a concurrency smoke test for the demo stack, not a throughput
capacity claim. It includes HTTP calls, task/credential issuance, and
query execution through the API.
