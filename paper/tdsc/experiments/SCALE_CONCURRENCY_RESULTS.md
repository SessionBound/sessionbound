# Scale and Concurrency Results

Native end-to-end scale raw file:
`../raw_results/native_end_to_end_20260708_235456.json`.
Historical wrapper scale raw file: `../raw_results/scale_1783515366.json`.
Concurrency raw file: `raw_results/concurrency_1783221968.json` (historical
smoke test retained for context only).

The native end-to-end scale benchmark temporarily inserted synthetic
`native_scale_*` expense rows for the June 2026 Sales scope and cleaned them
after each target. The run compared Raw PostgreSQL, Safe-view-only,
RLS+SafeView+ShortCredential+Audit, SessionBound wrapper, and SessionBound
native hook/executor modes across SELECT/JOIN/GROUP BY/CTE/window query
shapes. API task-token and credential issuance were outside measured query
latency.

| Target scoped rows | Mode | p50 range ms | max p95 ms | max p99 ms | Errors |
|---:|---|---:|---:|---:|---:|
| 1,000 | Raw PostgreSQL | 0.52--1.06 | 1.17 | 1.17 | 0 |
| 1,000 | Safe-view-only | 0.65--1.41 | 1.59 | 1.59 | 0 |
| 1,000 | RLS+SafeView+Audit | 1.55--1.96 | 2.55 | 2.55 | 0 |
| 1,000 | SessionBound wrapper | 78.48--94.38 | 100.13 | 100.13 | 0 |
| 1,000 | SessionBound native hook/executor | 80.01--98.43 | 107.87 | 107.87 | 0 |
| 10,000 | Raw PostgreSQL | 1.90--8.14 | 8.57 | 8.57 | 0 |
| 10,000 | Safe-view-only | 2.73--9.01 | 9.59 | 9.59 | 0 |
| 10,000 | RLS+SafeView+Audit | 3.62--10.39 | 11.10 | 11.10 | 0 |
| 10,000 | SessionBound wrapper | 673.83--701.69 | 768.59 | 768.59 | 0 |
| 10,000 | SessionBound native hook/executor | 660.17--683.23 | 761.76 | 761.76 | 0 |
| 100,000 | Raw PostgreSQL | 12.30--94.56 | 99.80 | 99.80 | 0 |
| 100,000 | Safe-view-only | 24.91--101.26 | 110.32 | 110.32 | 0 |
| 100,000 | RLS+SafeView+Audit | 17.64--100.57 | 103.86 | 103.86 | 0 |
| 100,000 | SessionBound wrapper | 6136.37--6313.42 | 6544.00 | 6544.00 | 0 |
| 100,000 | SessionBound native hook/executor | 6242.66--6444.11 | 6717.46 | 6717.46 | 0 |

The 100k result exposes substantial scale-sensitive overhead in both current
SessionBound accounting paths. It should be read as a diagnostic prototype
limitation, not as a production throughput claim. The separate hook-only
microbenchmark at `../raw_results/hook_microbenchmark_20260708_205831.json`
shows that native structural guard checks are not the multi-second bottleneck.

| Concurrent sessions | Successful sessions | Failed sessions | Error rate | Query p50 ms | Query p95 ms | Session p50 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0.0% | 29.894 | 30.995 | 175.331 |
| 5 | 5 | 0 | 0.0% | 36.296 | 39.004 | 203.131 |
| 20 | 20 | 0 | 0.0% | 82.896 | 110.356 | 467.768 |

This is a concurrency smoke test for the demo stack, not a throughput
capacity claim. It includes HTTP calls, task/credential issuance, and
query execution through the API.
