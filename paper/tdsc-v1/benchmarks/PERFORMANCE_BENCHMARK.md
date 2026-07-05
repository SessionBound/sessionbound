# Performance Benchmark

Status: benchmark completed.

Raw results:

```text
paper/arxiv-v1/benchmarks/raw_results/benchmark_1782321795.json
```

## Environment

- Date: 2026-06-25
- Commit under test: `cb2d4ca`
- Runtime: Docker Compose
- PostgreSQL: PostgreSQL 16.14 (Debian 16.14-1.pgdg13+1), 64-bit
- API container: FastAPI/uvicorn image built from `app/`
- Warmup iterations: 10
- Measurement iterations: 100

## Baseline

Baseline option used:

1. Admin/test role executes equivalent SQL over raw `app_data` tables with equivalent tenant/month predicates.

The raw baseline queries include:

```sql
WHERE e.tenant_id = 'company_a'
  AND e.expense_month = '2026-06'
```

SessionBound queries execute through:

```sql
SELECT * FROM taskbound.run($SQL)
```

after binding a signed task token with:

```sql
SELECT taskbound.bind_task($payload_text, $signature)
```

The benchmark intentionally excludes HTTP latency and dynamic credential creation. It measures database execution overhead for the SessionBound runtime path: SQL validation, session claim enforcement, safe views, budget accounting, row-exposure tracking, and receipt writes.

## Performance Table

| Query Pattern | Raw PG p50 | SessionBound p50 | Overhead | Rows | Notes |
|---|---:|---:|---:|---:|---|
| SELECT | 0.063 ms | 1.434 ms | 2168.4% | 3 | p95 raw 0.142 ms; p95 SessionBound 1.760 ms |
| JOIN | 0.060 ms | 1.503 ms | 2408.0% | 3 | p95 raw 0.135 ms; p95 SessionBound 1.752 ms |
| GROUP BY | 0.066 ms | 1.450 ms | 2088.7% | 3 | p95 raw 0.139 ms; p95 SessionBound 1.709 ms |
| CTE | 0.052 ms | 1.457 ms | 2702.8% | 1 | p95 raw 0.102 ms; p95 SessionBound 1.694 ms |
| Window | 0.074 ms | 1.500 ms | 1937.5% | 5 | p95 raw 0.157 ms; p95 SessionBound 1.814 ms |

## Interpretation

The measured absolute SessionBound p50 latency is approximately 1.4-1.5 ms for these small synthetic queries. Relative overhead is high because the raw PostgreSQL baseline is extremely small, roughly 0.052-0.074 ms p50.

The likely overhead sources are:

- PL/pgSQL `taskbound.run(sql)` wrapper execution;
- SQL text policy checks;
- task-session lookup;
- safe-view execution through session claims;
- query budget updates;
- unique row exposure accounting;
- receipt insertion.

These results should not be generalized to larger datasets without additional experiments. For larger analytical queries, fixed runtime overhead may be less significant relative to scan/join/aggregation cost.

## Reproduction

With Docker services running:

```bash
docker compose exec -T api python - < paper/arxiv-v1/benchmarks/scripts/run_benchmark.py \
  > paper/arxiv-v1/benchmarks/raw_results/benchmark_$(date +%s).json
```
