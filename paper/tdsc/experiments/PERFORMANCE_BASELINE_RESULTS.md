# Performance Baseline Results

Raw files: `raw_results/performance_1783221625.json` and
`raw_results/performance_1783221625.csv`.

All values are measured inside the Docker Compose environment. The
SessionBound path uses direct database execution of `taskbound.run(sql)`
after task and credential issuance; it excludes per-query HTTP latency
but includes PL/pgSQL dispatch, policy checks, budget accounting,
disclosure accounting, and receipt insertion.

| Baseline | Pattern | p50 ms | p95 ms | Rows | Errors | Overhead vs raw |
|---|---|---:|---:|---:|---:|---:|
| Raw PostgreSQL | SELECT | 0.103 | 0.228 | 3 | 0 | 0.0% |
| Raw PostgreSQL | JOIN | 0.103 | 0.223 | 3 | 0 | 0.0% |
| Raw PostgreSQL | GROUP BY | 0.108 | 0.249 | 1 | 0 | 0.0% |
| Raw PostgreSQL | CTE | 0.102 | 0.215 | 1 | 0 | 0.0% |
| Raw PostgreSQL | Window | 0.148 | 0.269 | 5 | 0 | 0.0% |
| Role-only | SELECT | 0.105 | 0.185 | 3 | 0 | 2.4% |
| Role-only | JOIN | 0.096 | 0.195 | 3 | 0 | -7.4% |
| Role-only | GROUP BY | 0.118 | 0.249 | 1 | 0 | 9.3% |
| Role-only | CTE | 0.095 | 0.193 | 1 | 0 | -6.7% |
| Role-only | Window | 0.194 | 0.272 | 5 | 0 | 31.4% |
| Safe-view-only | SELECT | 0.203 | 0.274 | 3 | 0 | 97.8% |
| Safe-view-only | JOIN | 0.190 | 0.258 | 3 | 0 | 83.3% |
| Safe-view-only | GROUP BY | 0.201 | 0.291 | 1 | 0 | 85.9% |
| Safe-view-only | CTE | 0.181 | 0.242 | 1 | 0 | 77.7% |
| Safe-view-only | Window | 0.253 | 0.319 | 5 | 0 | 70.9% |
| RLS-only | SELECT | 0.155 | 0.204 | 3 | 0 | 51.4% |
| RLS-only | JOIN | 0.164 | 0.226 | 3 | 0 | 58.8% |
| RLS-only | GROUP BY | 0.187 | 0.251 | 1 | 0 | 73.2% |
| RLS-only | CTE | 0.146 | 0.220 | 1 | 0 | 43.9% |
| RLS-only | Window | 0.217 | 0.294 | 5 | 0 | 47.1% |
| Full SessionBound | SELECT | 21.356 | 24.535 | 3 | 0 | 20700.5% |
| Full SessionBound | JOIN | 22.176 | 25.084 | 3 | 0 | 21344.7% |
| Full SessionBound | GROUP BY | 21.152 | 28.539 | 1 | 0 | 19450.3% |
| Full SessionBound | CTE | 20.857 | 23.247 | 1 | 0 | 20423.8% |
| Full SessionBound | Window | 21.271 | 26.652 | 5 | 0 | 14296.0% |

The relative percentages are large because the raw PostgreSQL baseline is
sub-millisecond. The absolute Full SessionBound p50 cost is about
20.9--22.2 ms for the measured small-dataset query patterns.

Follow-up performance probes are reported separately: receipt/budget
ablation in `RECEIPT_OVERHEAD_RESULTS.md` and the 1k/10k/100k scoped-row
scale sweep in `SCALE_CONCURRENCY_RESULTS.md`.
