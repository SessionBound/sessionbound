# Performance Baseline Results

Raw files: `../raw_results/overhead_breakdown_20260708_205527.json` and
`../raw_results/overhead_breakdown_20260708_205527.csv`.

All values are measured inside the Docker Compose environment. The
SessionBound path uses direct database execution of `taskbound.run(sql)`
after task and credential issuance; it excludes per-query HTTP latency
but includes PL/pgSQL dispatch, policy checks, budget accounting,
disclosure accounting, and receipt insertion.

| Baseline | Pattern | p50 ms | p95 ms | Rows | Errors |
|---|---|---:|---:|---:|---:|
| Raw PostgreSQL | SELECT | 0.230 | 0.426 | 10 | 0 |
| Raw PostgreSQL | JOIN | 0.201 | 0.321 | 10 | 0 |
| Raw PostgreSQL | GROUP BY | 0.229 | 0.334 | 1 | 0 |
| Raw PostgreSQL | CTE | 0.252 | 0.381 | 1 | 0 |
| Raw PostgreSQL | Window | 0.251 | 0.376 | 20 | 0 |
| Role-only | SELECT | 0.227 | 0.368 | 10 | 0 |
| Role-only | JOIN | 0.181 | 0.287 | 10 | 0 |
| Role-only | GROUP BY | 0.230 | 0.369 | 1 | 0 |
| Role-only | CTE | 0.238 | 0.397 | 1 | 0 |
| Role-only | Window | 0.238 | 0.435 | 20 | 0 |
| Safe-view-only | SELECT | 0.233 | 0.353 | 10 | 0 |
| Safe-view-only | JOIN | 0.209 | 0.415 | 10 | 0 |
| Safe-view-only | GROUP BY | 0.272 | 0.550 | 1 | 0 |
| Safe-view-only | CTE | 0.254 | 0.490 | 1 | 0 |
| Safe-view-only | Window | 0.281 | 0.468 | 20 | 0 |
| RLS+Safe View+Short Credential+Audit | SELECT | 1.067 | 1.952 | 10 | 0 |
| RLS+Safe View+Short Credential+Audit | JOIN | 0.976 | 1.977 | 10 | 0 |
| RLS+Safe View+Short Credential+Audit | GROUP BY | 1.100 | 2.017 | 1 | 0 |
| RLS+Safe View+Short Credential+Audit | CTE | 1.045 | 1.956 | 1 | 0 |
| RLS+Safe View+Short Credential+Audit | Window | 1.119 | 2.090 | 20 | 0 |
| Full SessionBound | SELECT | 17.397 | 19.919 | 10 | 0 |
| Full SessionBound | JOIN | 18.616 | 20.693 | 10 | 0 |
| Full SessionBound | GROUP BY | 18.289 | 20.830 | 1 | 0 |
| Full SessionBound | CTE | 17.698 | 20.613 | 1 | 0 |
| Full SessionBound | Window | 17.639 | 19.538 | 20 | 0 |

The relative percentages are large because the raw PostgreSQL baseline is
sub-millisecond. The absolute Full SessionBound p50 cost is about
17.4--18.6 ms for the measured small-dataset query patterns. The strong
RLS+safe-view+short-credential+audit baseline is about 0.98--1.12 ms p50.

Follow-up performance probes are reported separately: receipt/budget
ablation in the overhead breakdown and the 1k/10k/100k scoped-row scale
sweep in `SCALE_CONCURRENCY_RESULTS.md`.
