#!/usr/bin/env python3
"""Measure SessionBoundDB overhead across query patterns and ablation modes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as psql

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "app"))

from task_registry import build_task_from_template  # noqa: E402


DB_HOST = os.environ.get("TDSC_DB_HOST", "postgres")
DB_PORT = os.environ.get("TDSC_DB_PORT", "5432")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
ADMIN_DSN = f"postgresql://postgres:postgres@{DB_HOST}:{DB_PORT}/{DB_NAME}"
APP_DSN = f"postgresql://agent_app:agentpass@{DB_HOST}:{DB_PORT}/{DB_NAME}"
ROLE_ONLY_DSN = f"postgresql://tdsc_role_only:tdsc_role_only_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}"
SAFE_VIEW_ONLY_DSN = f"postgresql://tdsc_safe_view_only:tdsc_safe_view_only_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}"
RLS_SAFE_AUDIT_DSN = f"postgresql://tdsc_rls_safe_audit:tdsc_rls_safe_audit_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}"
SQL_DIR = REPO_ROOT / "paper/tdsc/experiments/sql"

WARMUP_ITERATIONS = 10
MEASUREMENT_ITERATIONS = 100

PATTERNS: dict[str, dict[str, str]] = {
    "Q1 SELECT": {
        "safe": "SELECT expense_id, employee_name, amount FROM expenses WHERE department_id = 'dep_sales' ORDER BY amount DESC LIMIT 10",
        "raw": """
            SELECT e.expense_id, emp.employee_name, e.amount
            FROM app_data.expenses e
            JOIN app_data.employees emp ON emp.employee_id = e.employee_id
            WHERE e.tenant_id = 'company_a' AND e.expense_month = '2026-06'
              AND e.department_id = 'dep_sales'
            ORDER BY e.amount DESC
            LIMIT 10
        """,
    },
    "Q2 JOIN": {
        "safe": """
            SELECT e.expense_id, e.amount, d.department_name
            FROM expenses e
            JOIN departments d ON e.department_id = d.department_id
            WHERE e.department_id = 'dep_sales'
            ORDER BY e.amount DESC
            LIMIT 10
        """,
        "raw": """
            SELECT e.expense_id, e.amount, d.department_name
            FROM app_data.expenses e
            JOIN app_data.departments d ON d.department_id = e.department_id
            WHERE e.tenant_id = 'company_a' AND e.expense_month = '2026-06'
              AND e.department_id = 'dep_sales'
            ORDER BY e.amount DESC
            LIMIT 10
        """,
    },
    "Q3 GROUP BY": {
        "safe": """
            SELECT department_name, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS expense_count, sum(amount) AS total_amount
            FROM expenses
            WHERE department_id = 'dep_sales'
            GROUP BY department_name
            ORDER BY total_amount DESC
        """,
        "raw": """
            SELECT d.department_name, count(DISTINCT e.employee_id) AS employee_count,
                   count(*) AS expense_count, sum(e.amount) AS total_amount
            FROM app_data.expenses e
            JOIN app_data.departments d ON d.department_id = e.department_id
            WHERE e.tenant_id = 'company_a' AND e.expense_month = '2026-06'
              AND e.department_id = 'dep_sales'
            GROUP BY d.department_name
            ORDER BY total_amount DESC
        """,
    },
    "Q4 CTE": {
        "safe": """
            WITH high AS (
              SELECT expense_id, department_name, employee_id, amount
              FROM expenses
              WHERE amount > 1000 AND department_id = 'dep_sales'
            )
            SELECT department_name, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS high_count, avg(amount) AS average_amount
            FROM high
            GROUP BY department_name
            ORDER BY high_count DESC
        """,
        "raw": """
            WITH high AS (
              SELECT e.expense_id, d.department_name, e.employee_id, e.amount
              FROM app_data.expenses e
              JOIN app_data.departments d ON d.department_id = e.department_id
              WHERE e.tenant_id = 'company_a'
                AND e.expense_month = '2026-06'
                AND e.department_id = 'dep_sales'
                AND e.amount > 1000
            )
            SELECT department_name, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS high_count, avg(amount) AS average_amount
            FROM high
            GROUP BY department_name
            ORDER BY high_count DESC
        """,
    },
    "Q5 window function": {
        "safe": """
            SELECT expense_id, department_name, amount,
                   row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn
            FROM expenses
            WHERE department_id = 'dep_sales'
            ORDER BY amount DESC
            LIMIT 20
        """,
        "raw": """
            SELECT e.expense_id, d.department_name, e.amount,
                   row_number() OVER (PARTITION BY d.department_name ORDER BY e.amount DESC) AS rn
            FROM app_data.expenses e
            JOIN app_data.departments d ON d.department_id = e.department_id
            WHERE e.tenant_id = 'company_a' AND e.expense_month = '2026-06'
              AND e.department_id = 'dep_sales'
            ORDER BY e.amount DESC
            LIMIT 20
        """,
    },
}


def git_commit() -> str:
    if os.environ.get("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def apply_sql_file(cur: Any, path: Path) -> None:
    cur.execute(path.read_text(encoding="utf-8"))


def setup_baselines() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            apply_sql_file(cur, SQL_DIR / "role_only_baseline.sql")
            apply_sql_file(cur, SQL_DIR / "safe_view_only_baseline.sql")
            apply_sql_file(cur, SQL_DIR / "rls_safe_view_short_credential_audit_baseline.sql")


def task_token(task_id: str, runtime_options: dict[str, bool] | None = None) -> tuple[str, str]:
    _, payload_text, signature = build_task_from_template(
        task_id=task_id,
        task_type="monthly_travel_expense_review",
        delegator="user:alice",
        actor="agent:travel-expense-analyst",
        requested_scope={"expense_month": "2026-06"},
        requested_budgets={
            "max_queries": MEASUREMENT_ITERATIONS,
            "max_unique_expense_rows": 5000,
        },
        runtime_claims={"runtime_options": runtime_options} if runtime_options else None,
    )
    return payload_text, signature


def summarize(latencies_ms: list[float]) -> dict[str, float | None]:
    if not latencies_ms:
        return {"p50_ms": None, "p95_ms": None, "mean_ms": None, "stddev_ms": None}
    ordered = sorted(latencies_ms)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[p95_index],
        "mean_ms": statistics.fmean(ordered),
        "stddev_ms": statistics.pstdev(ordered),
    }


def execute_sql(cur: Any, sql_text: str, mode: str) -> list[Any]:
    if "SessionBound" in mode:
        cur.execute("SELECT * FROM taskbound.run(%s)", (sql_text,))
    else:
        cur.execute(sql_text)
    return cur.fetchall()


def measure_direct(
    *,
    dsn: str,
    sql_text: str,
    mode: str,
    bind_task: bool = False,
    runtime_options: dict[str, bool] | None = None,
    iterations: int,
    pattern_name: str,
    phase: str,
    search_path: str | None = None,
    audit: bool = False,
) -> dict[str, Any]:
    latencies: list[float] = []
    errors: list[str] = []
    rows_returned: int | None = None
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            if bind_task:
                payload_text, signature = task_token(
                    f"task_overhead_{int(time.time() * 1000)}_{abs(hash((mode, pattern_name, phase))) % 1000000}",
                    runtime_options,
                )
                cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
                if mode == "M2 Safe-view-only":
                    cur.execute("SET search_path TO taskbound, public")
            elif search_path:
                cur.execute(psql.SQL("SET search_path TO {}, public").format(psql.Identifier(search_path)))
            for _ in range(iterations):
                started = time.perf_counter_ns()
                try:
                    rows = execute_sql(cur, sql_text, mode)
                    if audit:
                        cur.execute(
                            "SELECT tdsc_rls_audit.log_query(%s, %s, %s, %s)",
                            (
                                "tdsc_rls_safe_audit",
                                f"{pattern_name}:{phase}",
                                sql_text,
                                len(rows),
                            ),
                        )
                    elapsed = (time.perf_counter_ns() - started) / 1_000_000
                    latencies.append(elapsed)
                    rows_returned = len(rows)
                except Exception as exc:
                    errors.append(str(exc).split("\n")[0])
    return {
        **summarize(latencies),
        "rows_returned": rows_returned,
        "errors": errors,
        "error_count": len(errors),
        "iterations": iterations,
    }


def run_mode(pattern_name: str, mode: str, query: dict[str, str]) -> dict[str, Any]:
    config = {
        "M0 Raw PostgreSQL": {
            "dsn": ADMIN_DSN,
            "sql": query["raw"],
            "bind_task": False,
            "runtime_options": None,
            "search_path": None,
            "audit": False,
        },
        "M1 Role-only read-only credential": {
            "dsn": ROLE_ONLY_DSN,
            "sql": query["raw"],
            "bind_task": False,
            "runtime_options": None,
            "search_path": None,
            "audit": False,
        },
        "M2 Safe-view-only": {
            "dsn": SAFE_VIEW_ONLY_DSN,
            "sql": query["safe"],
            "bind_task": False,
            "runtime_options": None,
            "search_path": "tdsc_safe_view_only",
            "audit": False,
        },
        "M3 RLS + Safe View + Short Credential + Audit": {
            "dsn": RLS_SAFE_AUDIT_DSN,
            "sql": query["safe"],
            "bind_task": False,
            "runtime_options": None,
            "search_path": "tdsc_rls_safe_view",
            "audit": True,
        },
        "M4 SessionBound without receipts": {
            "dsn": APP_DSN,
            "sql": query["safe"],
            "bind_task": True,
            "runtime_options": {"receipts_enabled": False},
            "search_path": None,
            "audit": False,
        },
        "M5 SessionBound without budget updates": {
            "dsn": APP_DSN,
            "sql": query["safe"],
            "bind_task": True,
            "runtime_options": {"budget_accounting_enabled": False},
            "search_path": None,
            "audit": False,
        },
        "M6 SessionBound full": {
            "dsn": APP_DSN,
            "sql": query["safe"],
            "bind_task": True,
            "runtime_options": None,
            "search_path": None,
            "audit": False,
        },
    }[mode]

    warmup = measure_direct(
        dsn=config["dsn"],
        sql_text=config["sql"],
        mode=mode,
        bind_task=config["bind_task"],
        runtime_options=config["runtime_options"],
        iterations=WARMUP_ITERATIONS,
        pattern_name=pattern_name,
        phase="warmup",
        search_path=config["search_path"],
        audit=config["audit"],
    )
    measurement = measure_direct(
        dsn=config["dsn"],
        sql_text=config["sql"],
        mode=mode,
        bind_task=config["bind_task"],
        runtime_options=config["runtime_options"],
        iterations=MEASUREMENT_ITERATIONS,
        pattern_name=pattern_name,
        phase="measurement",
        search_path=config["search_path"],
        audit=config["audit"],
    )
    return {
        "mode": mode,
        "pattern": pattern_name,
        "supported": True,
        "warmup": warmup,
        "measurement": measurement,
    }


def flatten_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for record in payload["records"]:
        measurement = record.get("measurement") or {}
        rows.append(
            {
                "pattern": record["pattern"],
                "mode": record["mode"],
                "supported": record.get("supported"),
                "p50_ms": measurement.get("p50_ms"),
                "p95_ms": measurement.get("p95_ms"),
                "mean_ms": measurement.get("mean_ms"),
                "stddev_ms": measurement.get("stddev_ms"),
                "rows_returned": measurement.get("rows_returned"),
                "error_count": measurement.get("error_count"),
                "reason": record.get("reason", ""),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_baselines()

    modes = [
        "M0 Raw PostgreSQL",
        "M1 Role-only read-only credential",
        "M2 Safe-view-only",
        "M3 RLS + Safe View + Short Credential + Audit",
        "M4 SessionBound without receipts",
        "M5 SessionBound without budget updates",
        "M6 SessionBound full",
    ]
    records = []
    for pattern_name, query in PATTERNS.items():
        for mode in modes:
            print(f"{pattern_name} | {mode}", flush=True)
            records.append(run_mode(pattern_name, mode, query))

    payload = {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "warmup_iterations": WARMUP_ITERATIONS,
            "measurement_iterations": MEASUREMENT_ITERATIONS,
            "notes": [
                "Measurements use direct PostgreSQL connections from the compose network.",
                "HTTP, model calls, and API-layer AST parsing are excluded from this overhead breakdown.",
                "M3 applies PostgreSQL RLS over raw tables, field-limited safe views, a short-lived read-only role, and a basic query audit insert.",
            ],
        },
        "records": records,
    }
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    json_path = output_dir / f"overhead_breakdown_{timestamp}.json"
    csv_path = output_dir / f"overhead_breakdown_{timestamp}.csv"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    rows = flatten_rows(payload)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
