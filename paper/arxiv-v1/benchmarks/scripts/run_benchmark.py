#!/usr/bin/env python3
"""Benchmark raw PostgreSQL SQL vs taskbound.run(sql).

Run from the API container so the script can import task_registry and connect to
the Compose postgres service:

    docker compose exec -T api python - < paper/arxiv-v1/benchmarks/scripts/run_benchmark.py \
      > paper/arxiv-v1/benchmarks/raw_results/benchmark.json

The benchmark uses an admin/test connection for the raw baseline and signed task
tokens for the SessionBound path. It does not include HTTP request latency or
dynamic credential creation in the measured SessionBound timings.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime, timezone

import psycopg
from task_registry import build_task_from_template


WARMUP_ITERATIONS = 10
MEASUREMENT_ITERATIONS = 100

QUERIES = [
    {
        "pattern": "SELECT",
        "raw": (
            "SELECT e.expense_id, emp.employee_name, e.amount "
            "FROM app_data.expenses e "
            "JOIN app_data.employees emp ON emp.employee_id=e.employee_id "
            "WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' "
            "ORDER BY e.amount DESC LIMIT 3"
        ),
        "taskbound": (
            "SELECT expense_id, employee_name, amount "
            "FROM expenses ORDER BY amount DESC LIMIT 3"
        ),
    },
    {
        "pattern": "JOIN",
        "raw": (
            "SELECT e.expense_id, e.amount, d.department_name "
            "FROM app_data.expenses e "
            "JOIN app_data.departments d ON e.department_id=d.department_id "
            "WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' "
            "ORDER BY e.amount DESC LIMIT 3"
        ),
        "taskbound": (
            "SELECT e.expense_id, e.amount, d.department_name "
            "FROM expenses e JOIN departments d ON e.department_id = d.department_id "
            "ORDER BY e.amount DESC LIMIT 3"
        ),
    },
    {
        "pattern": "GROUP BY",
        "raw": (
            "SELECT d.department_name, COUNT(*) AS n, SUM(e.amount) AS total "
            "FROM app_data.expenses e "
            "JOIN app_data.departments d ON e.department_id=d.department_id "
            "WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' "
            "GROUP BY d.department_name ORDER BY total DESC"
        ),
        "taskbound": (
            "SELECT department_name, COUNT(*) AS n, SUM(amount) AS total "
            "FROM expenses GROUP BY department_name ORDER BY total DESC"
        ),
    },
    {
        "pattern": "CTE",
        "raw": (
            "WITH high AS ("
            "SELECT e.expense_id, e.amount FROM app_data.expenses e "
            "WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' AND e.amount > 1000"
            ") SELECT count(*) AS high_count FROM high"
        ),
        "taskbound": (
            "WITH high AS (SELECT expense_id, amount FROM expenses WHERE amount > 1000) "
            "SELECT count(*) AS high_count FROM high"
        ),
    },
    {
        "pattern": "Window",
        "raw": (
            "SELECT e.expense_id, d.department_name, e.amount, "
            "row_number() OVER (PARTITION BY d.department_name ORDER BY e.amount DESC) AS rn "
            "FROM app_data.expenses e "
            "JOIN app_data.departments d ON e.department_id=d.department_id "
            "WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' "
            "ORDER BY e.amount DESC LIMIT 5"
        ),
        "taskbound": (
            "SELECT expense_id, department_name, amount, "
            "row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn "
            "FROM expenses ORDER BY amount DESC LIMIT 5"
        ),
    },
]


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(latencies_ms: list[float], rows: int) -> dict[str, float | int]:
    return {
        "p50_ms": percentile(latencies_ms, 0.50),
        "p95_ms": percentile(latencies_ms, 0.95),
        "mean_ms": statistics.fmean(latencies_ms),
        "stddev_ms": statistics.pstdev(latencies_ms),
        "rows": rows,
        "iterations": len(latencies_ms),
    }


def run_fetch_count(cur: psycopg.Cursor, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    return len(cur.fetchall())


def measure_raw(cur: psycopg.Cursor, sql: str) -> dict[str, float | int]:
    for _ in range(WARMUP_ITERATIONS):
        run_fetch_count(cur, sql)

    latencies: list[float] = []
    rows = 0
    for _ in range(MEASUREMENT_ITERATIONS):
        start = time.perf_counter()
        rows = run_fetch_count(cur, sql)
        latencies.append((time.perf_counter() - start) * 1000)
    return summarize(latencies, rows)


def make_task(run_id: str, pattern: str, phase: str) -> tuple[str, str]:
    _payload, payload_text, signature = build_task_from_template(
        task_id=f"bench_{run_id}_{pattern}_{phase}".replace(" ", "_"),
        task_type="monthly_travel_expense_review",
        delegator="user:alice",
        actor="agent:benchmark",
        requested_scope={"expense_month": "2026-06"},
        requested_budgets={"max_queries": 100, "max_unique_expense_rows": 1000},
    )
    return payload_text, signature


def bind_task(cur: psycopg.Cursor, payload_text: str, signature: str) -> None:
    cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
    cur.fetchall()


def measure_taskbound(cur: psycopg.Cursor, sql: str, run_id: str, pattern: str) -> dict[str, float | int]:
    payload_text, signature = make_task(run_id, pattern, "warmup")
    bind_task(cur, payload_text, signature)
    for _ in range(WARMUP_ITERATIONS):
        run_fetch_count(cur, "SELECT * FROM taskbound.run(%s)", (sql,))

    payload_text, signature = make_task(run_id, pattern, "measure")
    bind_task(cur, payload_text, signature)
    latencies: list[float] = []
    rows = 0
    for _ in range(MEASUREMENT_ITERATIONS):
        start = time.perf_counter()
        rows = run_fetch_count(cur, "SELECT * FROM taskbound.run(%s)", (sql,))
        latencies.append((time.perf_counter() - start) * 1000)
    return summarize(latencies, rows)


def main() -> None:
    admin_dsn = os.environ["ADMIN_DATABASE_URL"]
    run_id = str(int(time.time()))

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            postgresql_version = cur.fetchone()[0]
            results = []
            for query in QUERIES:
                raw = measure_raw(cur, query["raw"])
                taskbound = measure_taskbound(cur, query["taskbound"], run_id, query["pattern"])
                overhead = ((taskbound["p50_ms"] - raw["p50_ms"]) / raw["p50_ms"]) * 100
                results.append(
                    {
                        "query_pattern": query["pattern"],
                        "raw_postgresql": raw,
                        "taskbound_run": taskbound,
                        "overhead_pct_p50": overhead,
                        "raw_sql": query["raw"],
                        "taskbound_sql": query["taskbound"],
                    }
                )

    print(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "warmup_iterations": WARMUP_ITERATIONS,
                "measurement_iterations": MEASUREMENT_ITERATIONS,
                "baseline": (
                    "admin/test role executes equivalent SQL over raw app_data tables "
                    "with tenant/month predicates matching the task scope"
                ),
                "postgresql_version": postgresql_version,
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

