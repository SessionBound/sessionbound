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
DB_PORT = os.environ.get("TDSC_DB_PORT", "5432")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
ADMIN_DSN = f"postgresql://postgres:postgres@{DB_HOST}:{DB_PORT}/{DB_NAME}"
WARMUP = int(os.environ.get("TDSC_WARMUP", "10"))
MEASURED = int(os.environ.get("TDSC_MEASURED", "100"))
SCRIPT_DIR = Path(__file__).resolve().parent
SQL_DIR = SCRIPT_DIR.parent / "sql"


@dataclass(frozen=True)
class Baseline:
    name: str
    dsn: str | None
    schema: str
    mode: str = "db"
    audit: bool = False


BASELINES = [
    Baseline("Raw PostgreSQL", f"postgresql://postgres:postgres@{DB_HOST}:{DB_PORT}/{DB_NAME}", "app_data"),
    Baseline("Role-only", f"postgresql://tdsc_role_only:tdsc_role_only_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}", "app_data"),
    Baseline("Safe-view-only", f"postgresql://tdsc_safe_view_only:tdsc_safe_view_only_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}", "tdsc_safe_view_only"),
    Baseline(
        "RLS + Safe View + Short Credential + Audit",
        f"postgresql://tdsc_rls_safe_audit:tdsc_rls_safe_audit_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}",
        "tdsc_rls_safe_view",
        audit=True,
    ),
    Baseline("Full SessionBound", None, "taskbound", mode="sessionbound"),
]


PATTERNS = {
    "SELECT": {
        "safe": "SELECT expense_id, employee_name, amount FROM {schema}.expenses ORDER BY amount DESC LIMIT 3",
        "raw": "SELECT expense_id, employee_id, amount FROM app_data.expenses WHERE tenant_id='company_a' AND expense_month='2026-06' AND department_id='dep_sales' ORDER BY amount DESC LIMIT 3",
        "sb": "SELECT expense_id, employee_name, amount FROM expenses ORDER BY amount DESC LIMIT 3",
    },
    "JOIN": {
        "safe": "SELECT e.expense_id, e.amount, d.department_name FROM {schema}.expenses e JOIN {schema}.departments d ON e.department_id=d.department_id ORDER BY e.amount DESC LIMIT 3",
        "raw": "SELECT e.expense_id, e.amount, d.department_name FROM app_data.expenses e JOIN app_data.departments d ON e.department_id=d.department_id WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' AND e.department_id='dep_sales' ORDER BY e.amount DESC LIMIT 3",
        "sb": "SELECT e.expense_id, e.amount, d.department_name FROM expenses e JOIN departments d ON e.department_id=d.department_id ORDER BY e.amount DESC LIMIT 3",
    },
    "GROUP BY": {
        "safe": "SELECT department_name, count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM {schema}.expenses GROUP BY department_name ORDER BY total DESC",
        "raw": "SELECT d.department_name, count(DISTINCT e.employee_id) AS employee_count, count(*) AS n, sum(e.amount) AS total FROM app_data.expenses e JOIN app_data.departments d ON e.department_id=d.department_id WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' AND e.department_id='dep_sales' GROUP BY d.department_name ORDER BY total DESC",
        "sb": "SELECT department_name, count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY department_name ORDER BY total DESC",
    },
    "CTE": {
        "safe": "WITH high AS (SELECT expense_id, employee_id, amount FROM {schema}.expenses WHERE amount > 1000) SELECT count(DISTINCT employee_id) AS employee_count, count(*) AS high_count FROM high",
        "raw": "WITH high AS (SELECT expense_id, employee_id, amount FROM app_data.expenses WHERE tenant_id='company_a' AND expense_month='2026-06' AND department_id='dep_sales' AND amount > 1000) SELECT count(DISTINCT employee_id) AS employee_count, count(*) AS high_count FROM high",
        "sb": "WITH high AS (SELECT expense_id, employee_id, amount FROM expenses WHERE amount > 1000) SELECT count(DISTINCT employee_id) AS employee_count, count(*) AS high_count FROM high",
    },
    "Window": {
        "safe": "SELECT expense_id, department_name, amount, row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn FROM {schema}.expenses ORDER BY amount DESC LIMIT 5",
        "raw": "SELECT e.expense_id, d.department_name, e.amount, row_number() OVER (PARTITION BY d.department_name ORDER BY e.amount DESC) AS rn FROM app_data.expenses e JOIN app_data.departments d ON e.department_id=d.department_id WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' AND e.department_id='dep_sales' ORDER BY e.amount DESC LIMIT 5",
        "sb": "SELECT expense_id, department_name, amount, row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn FROM expenses ORDER BY amount DESC LIMIT 5",
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


def open_session(max_queries: int) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = str(int(time.time() * 1000))
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"tdsc-perf-{suffix}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    task = post_json(
        "/tasks",
        {
            "task_id": f"tdsc_perf_{suffix}",
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


def connect_for_baseline(baseline: Baseline, *, max_queries: int = 100):
    if baseline.mode != "sessionbound":
        assert baseline.dsn
        return psycopg.connect(baseline.dsn)
    credential, task = open_session(max_queries=max_queries)
    dsn = f"postgresql://{credential['db_user']}:{credential['db_password']}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    conn = psycopg.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
        cur.fetchone()
    conn.commit()
    return conn


def apply_baseline_sql() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for path in [
                SQL_DIR / "role_only_baseline.sql",
                SQL_DIR / "safe_view_only_baseline.sql",
                SQL_DIR / "rls_safe_view_short_credential_audit_baseline.sql",
            ]:
                cur.execute(path.read_text(encoding="utf-8"))


def sql_for(baseline: Baseline, pattern: dict[str, str]) -> str:
    if baseline.mode == "sessionbound":
        return "SELECT * FROM taskbound.run(%s)", pattern["sb"]
    if baseline.schema == "app_data":
        return pattern["raw"]
    return pattern["safe"].format(schema=baseline.schema)


def run_once(cur, baseline: Baseline, pattern: dict[str, str]) -> int:
    if baseline.mode == "sessionbound":
        cur.execute("SELECT * FROM taskbound.run(%s)", (pattern["sb"],))
    else:
        sql_text = sql_for(baseline, pattern)
        cur.execute(sql_text)
    rows = cur.fetchall() if cur.description is not None else []
    if baseline.audit:
        cur.execute(
            "SELECT tdsc_rls_audit.log_query(%s, %s, %s, %s)",
            ("tdsc_rls_safe_audit", "performance", sql_for(baseline, pattern), len(rows)),
        )
    return len(rows)


def run_pattern_for_baseline(baseline: Baseline, pattern: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    rows = 0
    latencies: list[float] = []

    if baseline.mode == "sessionbound":
        warmup_conn = connect_for_baseline(baseline, max_queries=max(WARMUP, 1))
        warmup_conn.autocommit = True
        try:
            with warmup_conn.cursor() as cur:
                for _ in range(WARMUP):
                    try:
                        rows = run_once(cur, baseline, pattern)
                    except Exception as exc:
                        errors.append(str(exc).splitlines()[0])
        finally:
            warmup_conn.close()

        measured_conn = connect_for_baseline(baseline, max_queries=max(MEASURED, 1))
        measured_conn.autocommit = True
        try:
            with measured_conn.cursor() as cur:
                for _ in range(MEASURED):
                    try:
                        start = time.perf_counter()
                        rows = run_once(cur, baseline, pattern)
                        latencies.append((time.perf_counter() - start) * 1000)
                    except Exception as exc:
                        errors.append(str(exc).splitlines()[0])
        finally:
            measured_conn.close()
        return summarize(latencies, rows, errors)

    conn = connect_for_baseline(baseline)
    conn.autocommit = True
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


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[idx]


def summarize(latencies: list[float], rows: int, errors: list[str]) -> dict[str, Any]:
    return {
        "p50_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 0.95) if latencies else None,
        "mean_ms": statistics.mean(latencies) if latencies else None,
        "stddev_ms": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        "rows": rows,
        "errors": len(errors),
        "error_samples": errors[:3],
        "measurements": len(latencies),
    }


def main() -> None:
    run_id = str(int(time.time()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    apply_baseline_sql()
    results: dict[str, Any] = {
        "run_id": run_id,
        "warmup": WARMUP,
        "measured": MEASURED,
        "baselines": {},
    }

    for baseline in BASELINES:
        baseline_results: dict[str, Any] = {}
        for pattern_name, pattern in PATTERNS.items():
            baseline_results[pattern_name] = run_pattern_for_baseline(baseline, pattern)
        results["baselines"][baseline.name] = baseline_results

    raw = results["baselines"].get("Raw PostgreSQL", {})
    for baseline_name, baseline_results in results["baselines"].items():
        for pattern_name, summary in baseline_results.items():
            raw_p50 = raw.get(pattern_name, {}).get("p50_ms")
            p50 = summary.get("p50_ms")
            summary["overhead_vs_raw_pct"] = None
            if raw_p50 and p50 is not None:
                summary["overhead_vs_raw_pct"] = ((p50 - raw_p50) / raw_p50) * 100

    json_path = OUT_DIR / f"performance_{run_id}.json"
    csv_path = OUT_DIR / f"performance_{run_id}.csv"
    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["baseline", "pattern", "p50_ms", "p95_ms", "mean_ms", "stddev_ms", "rows", "errors", "overhead_vs_raw_pct"])
        for baseline_name, baseline_results in results["baselines"].items():
            for pattern_name, summary in baseline_results.items():
                writer.writerow([
                    baseline_name,
                    pattern_name,
                    summary.get("p50_ms"),
                    summary.get("p95_ms"),
                    summary.get("mean_ms"),
                    summary.get("stddev_ms"),
                    summary.get("rows"),
                    summary.get("errors"),
                    summary.get("overhead_vs_raw_pct"),
                ])
    print(json_path)
    print(csv_path)


if __name__ == "__main__":
    main()
