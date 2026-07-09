#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import statistics
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg


BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")
OUT_DIR = Path(os.environ.get("TDSC_OUT_DIR", "paper/tdsc/experiments/raw_results"))
DB_HOST = os.environ.get("TDSC_DB_HOST", "postgres")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
ADMIN_DSN = f"postgresql://postgres:postgres@{DB_HOST}:5432/{DB_NAME}"
WARMUP = int(os.environ.get("TDSC_SCALE_WARMUP", "3"))
MEASURED = int(os.environ.get("TDSC_SCALE_MEASURED", "5"))
TARGET_ROWS = [int(x) for x in os.environ.get("TDSC_SCALE_ROWS", "1000,10000,100000").split(",")]
SCRIPT_DIR = Path(__file__).resolve().parent
SQL_DIR = SCRIPT_DIR.parent / "sql"


@dataclass(frozen=True)
class Baseline:
    name: str
    dsn: str | None
    mode: str


BASELINES = [
    Baseline("Raw PostgreSQL", ADMIN_DSN, "raw"),
    Baseline("Role-only", f"postgresql://tdsc_role_only:tdsc_role_only_pass@{DB_HOST}:5432/{DB_NAME}", "raw"),
    Baseline("Safe-view-only", f"postgresql://tdsc_safe_view_only:tdsc_safe_view_only_pass@{DB_HOST}:5432/{DB_NAME}", "safe"),
    Baseline("RLS-only", f"postgresql://tdsc_rls_only:tdsc_rls_only_pass@{DB_HOST}:5432/{DB_NAME}", "raw"),
    Baseline("Full SessionBound", None, "sessionbound"),
]


PATTERNS = {
    "aggregate_by_category": {
        "raw": """
            SELECT category, count(*) AS n, sum(amount) AS total
            FROM app_data.expenses
            WHERE tenant_id='company_a'
              AND expense_month='2026-06'
              AND department_id='dep_sales'
            GROUP BY category
            ORDER BY total DESC
        """,
        "safe": """
            SELECT category, count(*) AS n, sum(amount) AS total
            FROM tdsc_safe_view_only.expenses
            GROUP BY category
            ORDER BY total DESC
        """,
        "sb": """
            SELECT category, count(*) AS n, sum(amount) AS total
            FROM expenses
            GROUP BY category
            ORDER BY total DESC
        """,
    },
    "topk_order": {
        "raw": """
            SELECT expense_id, employee_id, amount
            FROM app_data.expenses
            WHERE tenant_id='company_a'
              AND expense_month='2026-06'
              AND department_id='dep_sales'
            ORDER BY amount DESC
            LIMIT 10
        """,
        "safe": """
            SELECT expense_id, employee_id, amount
            FROM tdsc_safe_view_only.expenses
            ORDER BY amount DESC
            LIMIT 10
        """,
        "sb": """
            SELECT expense_id, employee_id, amount
            FROM expenses
            ORDER BY amount DESC
            LIMIT 10
        """,
    },
}


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def apply_baseline_sql() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for path in [
                SQL_DIR / "role_only_baseline.sql",
                SQL_DIR / "safe_view_only_baseline.sql",
                SQL_DIR / "rls_baseline.sql",
            ]:
                cur.execute(path.read_text(encoding="utf-8"))


def cleanup_scale_rows() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_data.approval_events WHERE expense_id LIKE 'scale_bench_%'")
            cur.execute("DELETE FROM app_data.ledger_entries WHERE expense_id LIKE 'scale_bench_%'")
            cur.execute("DELETE FROM app_data.expenses WHERE expense_id LIKE 'scale_bench_%'")
        conn.commit()


def prepare_scale(target_rows: int) -> dict[str, int]:
    cleanup_scale_rows()
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_data.employees (
                  employee_id, tenant_id, department_id, employee_name,
                  employee_level, phone, bank_account, salary
                )
                VALUES (
                  'emp_scale_bench', 'company_a', 'dep_sales',
                  'Scale Benchmark', 'L4', '13899999999', '6222-scale', 30000
                )
                ON CONFLICT (employee_id) DO NOTHING
                """
            )
            cur.execute(
                """
                SELECT count(*)
                FROM app_data.expenses
                WHERE tenant_id='company_a'
                  AND expense_month='2026-06'
                  AND department_id='dep_sales'
                """
            )
            existing = cur.fetchone()[0]
            rows_to_add = max(0, target_rows - existing)
            cur.execute(
                """
                INSERT INTO app_data.expenses (
                  expense_id, tenant_id, employee_id, department_id,
                  expense_month, category, merchant, city, amount,
                  submitted_at, status
                )
                SELECT
                  'scale_bench_' || %s || '_' || gs::text,
                  'company_a',
                  'emp_scale_bench',
                  'dep_sales',
                  '2026-06',
                  (ARRAY['flight','hotel','taxi','meal','train','conference','equipment','mobile','client_event','software'])[((gs - 1) %% 10) + 1],
                  (ARRAY['Scale Air','Scale Hotel','Scale Taxi','Scale Meal','Scale Rail','Scale Conf','Scale Store','Scale Mobile','Scale Event','Scale SaaS'])[((gs - 1) %% 10) + 1],
                  (ARRAY['Beijing','Shanghai','Shenzhen','Guangzhou','Hangzhou'])[((gs - 1) %% 5) + 1],
                  100 + ((gs %% 1000) * 3.17),
                  '2026-06-01T09:00:00Z'::timestamptz + ((gs %% 27) || ' days')::interval,
                  (ARRAY['submitted','finance_review_requested','finance_compliant','department_approval_requested','payable'])[((gs - 1) %% 5) + 1]
                FROM generate_series(1, %s) AS gs
                """,
                (target_rows, rows_to_add),
            )
            cur.execute("ANALYZE app_data.expenses")
            cur.execute(
                """
                SELECT count(*)
                FROM app_data.expenses
                WHERE tenant_id='company_a'
                  AND expense_month='2026-06'
                  AND department_id='dep_sales'
                """
            )
            actual = cur.fetchone()[0]
        conn.commit()
    return {"target_rows": target_rows, "existing_rows_before_insert": existing, "inserted_rows": rows_to_add, "actual_rows": actual}


def open_session(max_queries: int) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = str(int(time.time() * 1000))
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"tdsc-scale-{suffix}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    task = post_json(
        "/tasks",
        {
            "task_id": f"tdsc_scale_{suffix}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "department_id": "dep_sales",
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_rows": 5000,
            "max_queries": max_queries,
        },
    )
    return credential, task


def connect_for_baseline(baseline: Baseline, max_queries: int):
    if baseline.mode != "sessionbound":
        assert baseline.dsn
        conn = psycopg.connect(baseline.dsn)
        conn.autocommit = True
        return conn
    credential, task = open_session(max_queries=max_queries)
    dsn = f"postgresql://{credential['db_user']}:{credential['db_password']}@{DB_HOST}:5432/{DB_NAME}"
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
        cur.fetchone()
    return conn


def sql_for(baseline: Baseline, pattern: dict[str, str]) -> tuple[str, tuple[Any, ...]]:
    if baseline.mode == "sessionbound":
        return "SELECT * FROM taskbound.run(%s)", (pattern["sb"],)
    if baseline.mode == "safe":
        return pattern["safe"], ()
    return pattern["raw"], ()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[idx]


def run_once(cur, baseline: Baseline, pattern: dict[str, str]) -> int:
    sql_text, params = sql_for(baseline, pattern)
    cur.execute(sql_text, params)
    return len(cur.fetchall()) if cur.description is not None else 0


def summarize(latencies: list[float], rows: int, errors: list[str]) -> dict[str, Any]:
    return {
        "p50_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 0.95),
        "mean_ms": statistics.mean(latencies) if latencies else None,
        "stddev_ms": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        "rows_returned": rows,
        "measurements": len(latencies),
        "errors": len(errors),
        "error_samples": errors[:3],
    }


def run_baseline_pattern(baseline: Baseline, pattern: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    rows = 0
    latencies: list[float] = []
    conn = connect_for_baseline(baseline, max_queries=WARMUP + MEASURED + 5)
    try:
        with conn.cursor() as cur:
            for _ in range(WARMUP):
                try:
                    rows = run_once(cur, baseline, pattern)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
            for _ in range(MEASURED):
                try:
                    start = time.perf_counter()
                    rows = run_once(cur, baseline, pattern)
                    latencies.append((time.perf_counter() - start) * 1000)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
    finally:
        conn.close()
    return summarize(latencies, rows, errors)


def main() -> None:
    run_id = str(int(time.time()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    apply_baseline_sql()
    results: dict[str, Any] = {
        "run_id": run_id,
        "warmup": WARMUP,
        "measured": MEASURED,
        "targets": {},
    }

    for target in TARGET_ROWS:
        try:
            setup = prepare_scale(target)
            target_results: dict[str, Any] = {"setup": setup, "baselines": {}}
            for baseline in BASELINES:
                baseline_results: dict[str, Any] = {}
                for pattern_name, pattern in PATTERNS.items():
                    baseline_results[pattern_name] = run_baseline_pattern(baseline, pattern)
                target_results["baselines"][baseline.name] = baseline_results

            raw = target_results["baselines"].get("Raw PostgreSQL", {})
            for baseline_name, baseline_results in target_results["baselines"].items():
                for pattern_name, summary in baseline_results.items():
                    raw_p50 = raw.get(pattern_name, {}).get("p50_ms")
                    p50 = summary.get("p50_ms")
                    summary["overhead_vs_raw_pct"] = None
                    if raw_p50 and p50 is not None:
                        summary["overhead_vs_raw_pct"] = ((p50 - raw_p50) / raw_p50) * 100
            results["targets"][str(target)] = target_results
        finally:
            cleanup_scale_rows()

    json_path = OUT_DIR / f"scale_{run_id}.json"
    csv_path = OUT_DIR / f"scale_{run_id}.csv"
    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "target_rows",
            "actual_rows",
            "baseline",
            "pattern",
            "p50_ms",
            "p95_ms",
            "mean_ms",
            "stddev_ms",
            "rows_returned",
            "errors",
            "overhead_vs_raw_pct",
        ])
        for target, target_results in results["targets"].items():
            actual_rows = target_results["setup"]["actual_rows"]
            for baseline_name, baseline_results in target_results["baselines"].items():
                for pattern_name, summary in baseline_results.items():
                    writer.writerow([
                        target,
                        actual_rows,
                        baseline_name,
                        pattern_name,
                        summary.get("p50_ms"),
                        summary.get("p95_ms"),
                        summary.get("mean_ms"),
                        summary.get("stddev_ms"),
                        summary.get("rows_returned"),
                        summary.get("errors"),
                        summary.get("overhead_vs_raw_pct"),
                    ])
    print(json_path)


if __name__ == "__main__":
    main()
