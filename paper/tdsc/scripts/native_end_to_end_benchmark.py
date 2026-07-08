#!/usr/bin/env python3
"""End-to-end benchmark for wrapper and native SessionBound paths.

This benchmark intentionally excludes HTTP calls from measured query latency,
but uses the API once per SessionBound measurement group to issue a short-lived
runtime credential and signed task token. It measures:

* Raw PostgreSQL
* Safe-view-only
* RLS + Safe View + Short Credential + Audit
* SessionBound wrapper reference path (`taskbound.run`)
* SessionBound native hook/executor path (direct safe-view SELECT)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg


REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = os.environ.get("TDSC_BASE_URL", "http://localhost:8000")
DB_HOST = os.environ.get("TDSC_DB_HOST", "postgres")
DB_PORT = os.environ.get("TDSC_DB_PORT", "5432")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
ADMIN_DSN = f"postgresql://postgres:postgres@{DB_HOST}:{DB_PORT}/{DB_NAME}"
SAFE_VIEW_ONLY_DSN = f"postgresql://tdsc_safe_view_only:tdsc_safe_view_only_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}"
RLS_SAFE_AUDIT_DSN = f"postgresql://tdsc_rls_safe_audit:tdsc_rls_safe_audit_pass@{DB_HOST}:{DB_PORT}/{DB_NAME}"
SQL_DIR = REPO_ROOT / "paper/tdsc/experiments/sql"

DEFAULT_WARMUP = int(os.environ.get("TDSC_NATIVE_WARMUP", "10"))
DEFAULT_MEASURED = int(os.environ.get("TDSC_NATIVE_MEASURED", "30"))
TARGET_ROWS = [int(x) for x in os.environ.get("TDSC_NATIVE_ROWS", "1000,10000,100000").split(",")]
MAX_QUERIES_PER_TASK = int(os.environ.get("TDSC_NATIVE_MAX_QUERIES_PER_TASK", "100"))
DEFAULT_TARGET_MEASURED = {1000: 100, 10000: 100, 100000: 30}


def parse_target_counts(env_name: str) -> dict[int, int]:
    raw = os.environ.get(env_name, "").strip()
    counts: dict[int, int] = {}
    if not raw:
        return counts
    for item in raw.split(","):
        if not item.strip():
            continue
        row_text, _, count_text = item.partition(":")
        if not row_text or not count_text:
            raise ValueError(f"{env_name} item must be ROWS:COUNT, got {item!r}")
        counts[int(row_text)] = int(count_text)
    return counts


TARGET_WARMUPS = parse_target_counts("TDSC_NATIVE_WARMUP_BY_ROWS")
if "TDSC_NATIVE_MEASURED_BY_ROWS" in os.environ:
    TARGET_MEASURED = parse_target_counts("TDSC_NATIVE_MEASURED_BY_ROWS")
elif "TDSC_NATIVE_MEASURED" in os.environ:
    TARGET_MEASURED = {}
else:
    TARGET_MEASURED = DEFAULT_TARGET_MEASURED


def warmup_for(target_rows: int) -> int:
    return TARGET_WARMUPS.get(target_rows, DEFAULT_WARMUP)


def measured_for(target_rows: int) -> int:
    return TARGET_MEASURED.get(target_rows, DEFAULT_MEASURED)


@dataclass(frozen=True)
class Mode:
    name: str
    kind: str
    dsn: str | None = None
    schema: str | None = None
    audit: bool = False


MODES = [
    Mode("Raw PostgreSQL", "raw", ADMIN_DSN),
    Mode("Safe-view-only", "safe", SAFE_VIEW_ONLY_DSN, "tdsc_safe_view_only"),
    Mode(
        "RLS + Safe View + Short Credential + Audit",
        "safe",
        RLS_SAFE_AUDIT_DSN,
        "tdsc_rls_safe_view",
        audit=True,
    ),
    Mode("SessionBound wrapper", "sessionbound_wrapper"),
    Mode("SessionBound native hook/executor", "sessionbound_native"),
]


PATTERNS = {
    "SELECT": {
        "raw": """
            SELECT expense_id, employee_id, amount
            FROM app_data.expenses
            WHERE tenant_id='company_a'
              AND expense_month='2026-06'
              AND department_id='dep_sales'
            ORDER BY amount DESC
            LIMIT 50
        """,
        "safe": """
            SELECT expense_id, employee_id, amount
            FROM {schema}.expenses
            ORDER BY amount DESC
            LIMIT 50
        """,
        "sb": """
            SELECT expense_id, employee_id, amount
            FROM expenses
            ORDER BY amount DESC
            LIMIT 50
        """,
    },
    "JOIN": {
        "raw": """
            SELECT e.expense_id, e.amount, d.department_name
            FROM app_data.expenses e
            JOIN app_data.departments d
              ON d.tenant_id=e.tenant_id AND d.department_id=e.department_id
            WHERE e.tenant_id='company_a'
              AND e.expense_month='2026-06'
              AND e.department_id='dep_sales'
            ORDER BY e.amount DESC
            LIMIT 50
        """,
        "safe": """
            SELECT e.expense_id, e.amount, d.department_name
            FROM {schema}.expenses e
            JOIN {schema}.departments d ON d.department_id=e.department_id
            ORDER BY e.amount DESC
            LIMIT 50
        """,
        "sb": """
            SELECT e.expense_id, e.amount, d.department_name
            FROM expenses e
            JOIN departments d ON d.department_id=e.department_id
            ORDER BY e.amount DESC
            LIMIT 50
        """,
    },
    "GROUP BY": {
        "raw": """
            SELECT category, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS n, sum(amount) AS total
            FROM app_data.expenses
            WHERE tenant_id='company_a'
              AND expense_month='2026-06'
              AND department_id='dep_sales'
            GROUP BY category
            ORDER BY total DESC
        """,
        "safe": """
            SELECT category, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS n, sum(amount) AS total
            FROM {schema}.expenses
            GROUP BY category
            ORDER BY total DESC
        """,
        "sb": """
            SELECT category, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS n, sum(amount) AS total
            FROM expenses
            GROUP BY category
            ORDER BY total DESC
        """,
    },
    "CTE": {
        "raw": """
            WITH scoped AS (
              SELECT category, employee_id, amount
              FROM app_data.expenses
              WHERE tenant_id='company_a'
                AND expense_month='2026-06'
                AND department_id='dep_sales'
                AND amount >= 100
            )
            SELECT category, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS n, avg(amount) AS avg_amount
            FROM scoped
            GROUP BY category
            ORDER BY avg_amount DESC
        """,
        "safe": """
            WITH scoped AS (
              SELECT category, employee_id, amount
              FROM {schema}.expenses
              WHERE amount >= 100
            )
            SELECT category, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS n, avg(amount) AS avg_amount
            FROM scoped
            GROUP BY category
            ORDER BY avg_amount DESC
        """,
        "sb": """
            WITH scoped AS (
              SELECT category, employee_id, amount
              FROM expenses
              WHERE amount >= 100
            )
            SELECT category, count(DISTINCT employee_id) AS employee_count,
                   count(*) AS n, avg(amount) AS avg_amount
            FROM scoped
            GROUP BY category
            ORDER BY avg_amount DESC
        """,
    },
    "Window": {
        "raw": """
            SELECT expense_id, employee_id, category, amount,
                   row_number() OVER (PARTITION BY category ORDER BY amount DESC) AS rn
            FROM app_data.expenses
            WHERE tenant_id='company_a'
              AND expense_month='2026-06'
              AND department_id='dep_sales'
            ORDER BY amount DESC
            LIMIT 50
        """,
        "safe": """
            SELECT expense_id, employee_id, category, amount,
                   row_number() OVER (PARTITION BY category ORDER BY amount DESC) AS rn
            FROM {schema}.expenses
            ORDER BY amount DESC
            LIMIT 50
        """,
        "sb": """
            SELECT expense_id, employee_id, category, amount,
                   row_number() OVER (PARTITION BY category ORDER BY amount DESC) AS rn
            FROM expenses
            ORDER BY amount DESC
            LIMIT 50
        """,
    },
}


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return "unknown"


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
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            for path in [
                SQL_DIR / "role_only_baseline.sql",
                SQL_DIR / "safe_view_only_baseline.sql",
                SQL_DIR / "rls_safe_view_short_credential_audit_baseline.sql",
            ]:
                cur.execute(path.read_text(encoding="utf-8"))


def cleanup_scale_rows() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_data.approval_events WHERE expense_id LIKE 'native_scale_%'")
            cur.execute("DELETE FROM app_data.ledger_entries WHERE expense_id LIKE 'native_scale_%'")
            cur.execute("DELETE FROM app_data.expenses WHERE expense_id LIKE 'native_scale_%'")
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
                SELECT
                  'emp_native_scale_' || lpad(gs::text, 2, '0'),
                  'company_a',
                  'dep_sales',
                  'Native Scale ' || lpad(gs::text, 2, '0'),
                  'L4',
                  '13888' || lpad(gs::text, 6, '0'),
                  '6222-native-' || lpad(gs::text, 2, '0'),
                  30000
                FROM generate_series(1, 50) AS gs
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
            existing = int(cur.fetchone()[0])
            rows_to_add = max(0, target_rows - existing)
            cur.execute(
                """
                INSERT INTO app_data.expenses (
                  expense_id, tenant_id, employee_id, department_id,
                  expense_month, category, merchant, city, amount,
                  submitted_at, status
                )
                SELECT
                  'native_scale_' || %s || '_' || gs::text,
                  'company_a',
                  'emp_native_scale_' || lpad((((gs - 1) %% 50) + 1)::text, 2, '0'),
                  'dep_sales',
                  '2026-06',
                  (ARRAY['flight','hotel','taxi','meal','train','conference','equipment','mobile','client_event','software'])[((gs - 1) %% 10) + 1],
                  (ARRAY['Native Air','Native Hotel','Native Taxi','Native Meal','Native Rail','Native Conf','Native Store','Native Mobile','Native Event','Native SaaS'])[((gs - 1) %% 10) + 1],
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
            actual = int(cur.fetchone()[0])
        conn.commit()
    return {"target_rows": target_rows, "existing_rows_before_insert": existing, "inserted_rows": rows_to_add, "actual_rows": actual}


def open_session(max_queries: int) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = str(int(time.time() * 1000))
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"native-bench-{suffix}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    task = post_json(
        "/tasks",
        {
            "task_id": f"native_bench_{suffix}",
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


def connect_for_mode(mode: Mode, max_queries: int):
    if mode.kind in {"raw", "safe"}:
        assert mode.dsn is not None
        conn = psycopg.connect(mode.dsn)
        conn.autocommit = True
        return conn
    credential, task = open_session(max_queries=max_queries)
    dsn = f"postgresql://{credential['db_user']}:{credential['db_password']}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
        cur.fetchone()
    return conn


def sql_for(mode: Mode, pattern: dict[str, str]) -> tuple[str, tuple[Any, ...]]:
    if mode.kind == "raw":
        return pattern["raw"], ()
    if mode.kind == "safe":
        assert mode.schema is not None
        return pattern["safe"].format(schema=mode.schema), ()
    if mode.kind == "sessionbound_wrapper":
        return "SELECT * FROM taskbound.run(%s)", (pattern["sb"],)
    return pattern["sb"], ()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))
    return ordered[idx]


def run_one(cur, mode: Mode, sql_text: str, params: tuple[Any, ...], pattern_name: str, target_rows: int) -> int:
    cur.execute(sql_text, params)
    rows = cur.fetchall() if cur.description is not None else []
    if mode.audit:
        cur.execute(
            "SELECT tdsc_rls_audit.log_query(%s, %s, %s, %s)",
            ("tdsc_rls_safe_audit", f"{target_rows}:{pattern_name}", sql_text, len(rows)),
        )
    return len(rows)


def summarize(latencies: list[float], rows: int | None, errors: list[str]) -> dict[str, Any]:
    attempts = len(latencies) + len(errors)
    return {
        "p50_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
        "mean_ms": statistics.fmean(latencies) if latencies else None,
        "stddev_ms": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        "rows_returned": rows,
        "measurements": len(latencies),
        "errors": len(errors),
        "error_rate": (len(errors) / attempts) if attempts else None,
        "error_samples": errors[:3],
    }


def run_mode_pattern(
    mode: Mode,
    pattern_name: str,
    pattern: dict[str, str],
    target_rows: int,
    warmup: int,
    measured: int,
) -> dict[str, Any]:
    errors: list[str] = []
    latencies: list[float] = []
    rows: int | None = None
    sql_text, params = sql_for(mode, pattern)

    if mode.kind in {"sessionbound_wrapper", "sessionbound_native"}:
        remaining = measured
        while remaining > 0:
            task_budget = min(MAX_QUERIES_PER_TASK, max(1, warmup + remaining + 5))
            measured_capacity = max(1, task_budget - warmup)
            measured_this_session = min(remaining, measured_capacity)
            conn = connect_for_mode(mode, max_queries=task_budget)
            try:
                with conn.cursor() as cur:
                    for _ in range(warmup):
                        try:
                            rows = run_one(cur, mode, sql_text, params, pattern_name, target_rows)
                        except Exception as exc:
                            errors.append(str(exc).splitlines()[0])
                    for _ in range(measured_this_session):
                        try:
                            started = time.perf_counter_ns()
                            rows = run_one(cur, mode, sql_text, params, pattern_name, target_rows)
                            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
                        except Exception as exc:
                            errors.append(str(exc).splitlines()[0])
            finally:
                conn.close()
            remaining -= measured_this_session
        return summarize(latencies, rows, errors)

    conn = connect_for_mode(mode, max_queries=warmup + measured + 5)
    try:
        with conn.cursor() as cur:
            for _ in range(warmup):
                try:
                    rows = run_one(cur, mode, sql_text, params, pattern_name, target_rows)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
            for _ in range(measured):
                try:
                    started = time.perf_counter_ns()
                    rows = run_one(cur, mode, sql_text, params, pattern_name, target_rows)
                    latencies.append((time.perf_counter_ns() - started) / 1_000_000)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
    finally:
        conn.close()
    return summarize(latencies, rows, errors)


def flatten(results: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target_rows, target in results["targets"].items():
        actual_rows = target["setup"]["actual_rows"]
        warmup = target.get("warmup_iterations")
        measured = target.get("measured_iterations")
        for mode_name, mode_results in target["modes"].items():
            for pattern_name, summary in mode_results.items():
                rows.append(
                    {
                        "target_rows": target_rows,
                        "actual_rows": actual_rows,
                        "warmup_iterations": warmup,
                        "measured_iterations": measured,
                        "mode": mode_name,
                        "pattern": pattern_name,
                        "p50_ms": summary.get("p50_ms"),
                        "p95_ms": summary.get("p95_ms"),
                        "p99_ms": summary.get("p99_ms"),
                        "mean_ms": summary.get("mean_ms"),
                        "stddev_ms": summary.get("stddev_ms"),
                        "rows_returned": summary.get("rows_returned"),
                        "measurements": summary.get("measurements"),
                        "errors": summary.get("errors"),
                        "error_rate": summary.get("error_rate"),
                    }
                )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    parser.add_argument("--keep-scale-rows", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    apply_baseline_sql()
    results: dict[str, Any] = {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "default_warmup": DEFAULT_WARMUP,
            "default_measured": DEFAULT_MEASURED,
            "target_warmups": {str(k): v for k, v in sorted(TARGET_WARMUPS.items())},
            "target_measured": {str(k): v for k, v in sorted(TARGET_MEASURED.items())},
            "max_queries_per_task": MAX_QUERIES_PER_TASK,
            "db_host": DB_HOST,
            "db_port": DB_PORT,
            "patterns": list(PATTERNS),
            "modes": [mode.name for mode in MODES],
            "notes": [
                "SessionBound modes use API-issued runtime credentials and signed task tokens, but API calls are outside measured query latency.",
                "SessionBound wrapper executes SELECT * FROM taskbound.run(sql).",
                "SessionBound native hook/executor executes direct safe-view SELECT after taskbound.bind_task.",
                "SessionBound modes are split across fresh task sessions when needed to respect the prototype max_queries limit.",
                "Hook-only structural microbenchmarks are separate and do not execute rows, account budgets, or emit allow receipts.",
            ],
        },
        "targets": {},
    }
    try:
        for target_rows in TARGET_ROWS:
            warmup = warmup_for(target_rows)
            measured = measured_for(target_rows)
            setup = prepare_scale(target_rows)
            target: dict[str, Any] = {
                "setup": setup,
                "warmup_iterations": warmup,
                "measured_iterations": measured,
                "modes": {},
            }
            for mode in MODES:
                mode_results: dict[str, Any] = {}
                for pattern_name, pattern in PATTERNS.items():
                    print(
                        f"{target_rows} | warmup={warmup} measured={measured} | {mode.name} | {pattern_name}",
                        flush=True,
                    )
                    mode_results[pattern_name] = run_mode_pattern(
                        mode,
                        pattern_name,
                        pattern,
                        target_rows,
                        warmup,
                        measured,
                    )
                target["modes"][mode.name] = mode_results
            results["targets"][str(target_rows)] = target
            if not args.keep_scale_rows:
                cleanup_scale_rows()
    finally:
        if not args.keep_scale_rows:
            cleanup_scale_rows()

    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    json_path = out_dir / f"native_end_to_end_{timestamp}.json"
    csv_path = out_dir / f"native_end_to_end_{timestamp}.csv"
    json_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    csv_rows = flatten(results)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(csv_rows)
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
