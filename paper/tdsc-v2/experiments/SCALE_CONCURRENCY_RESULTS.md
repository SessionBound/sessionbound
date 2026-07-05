# Scale and Concurrency Results

Raw file: `raw_results/concurrency_1783221968.json`.

The measured dataset is the default seed: 430 expenses, 240 employees,
and 124 departments. No larger synthetic scale sweep was completed in
this run.

| Concurrent sessions | Successful sessions | Failed sessions | Error rate | Query p50 ms | Query p95 ms | Session p50 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0.0% | 29.894 | 30.995 | 175.331 |
| 5 | 5 | 0 | 0.0% | 36.296 | 39.004 | 203.131 |
| 20 | 20 | 0 | 0.0% | 82.896 | 110.356 | 467.768 |

This is a concurrency smoke test for the demo stack, not a throughput
capacity claim. It includes HTTP calls, task/credential issuance, and
query execution through the API.
