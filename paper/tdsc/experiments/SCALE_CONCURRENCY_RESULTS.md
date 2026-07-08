# Scale and Concurrency Results

Native end-to-end scale raw file:
`../raw_results/native_end_to_end_20260709_013634.json`.
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
The 1k and 10k targets use 10 warmup and 100 measured iterations per
mode/pattern; the 100k target uses 10 warmup and 30 measured iterations.
SessionBound modes are split across fresh task sessions when needed to respect
the prototype 100-query task limit.

| Target scoped rows | Mode | p50 range ms | max p95 ms | max p99 ms | Errors |
|---:|---|---:|---:|---:|---:|
| 1,000 | Raw PostgreSQL | 0.43--1.16 | 1.28 | 1.34 | 0 |
| 1,000 | Safe-view-only | 0.63--1.22 | 1.36 | 1.49 | 0 |
| 1,000 | RLS+SafeView+Audit | 1.32--2.06 | 2.82 | 3.29 | 0 |
| 1,000 | SessionBound wrapper | 75.63--92.57 | 104.39 | 111.13 | 0 |
| 1,000 | SessionBound native hook/executor | 79.80--95.53 | 106.71 | 113.85 | 0 |
| 10,000 | Raw PostgreSQL | 2.03--8.76 | 9.68 | 10.63 | 0 |
| 10,000 | Safe-view-only | 2.82--9.48 | 10.27 | 11.08 | 0 |
| 10,000 | RLS+SafeView+Audit | 3.70--10.24 | 11.69 | 12.38 | 0 |
| 10,000 | SessionBound wrapper | 632.57--646.97 | 686.76 | 730.08 | 0 |
| 10,000 | SessionBound native hook/executor | 632.39--644.28 | 691.20 | 726.82 | 0 |
| 100,000 | Raw PostgreSQL | 12.41--93.42 | 102.18 | 103.88 | 0 |
| 100,000 | Safe-view-only | 24.14--97.80 | 112.63 | 117.71 | 0 |
| 100,000 | RLS+SafeView+Audit | 17.14--99.71 | 109.32 | 109.52 | 0 |
| 100,000 | SessionBound wrapper | 6108.33--6236.90 | 6531.86 | 6626.32 | 0 |
| 100,000 | SessionBound native hook/executor | 6176.94--6298.30 | 6537.96 | 6690.70 | 0 |

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
