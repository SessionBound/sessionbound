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
OUT_DIR = Path(os.environ.get("TDSC_OUT_DIR", "paper/tdsc-v2/experiments/raw_results"))
DB_HOST = os.environ.get("TDSC_DB_HOST", "postgres")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
WARMUP = int(os.environ.get("TDSC_WARMUP", "10"))
MEASURED = int(os.environ.get("TDSC_MEASURED", "80"))


@dataclass(frozen=True)
class Variant:
    name: str
    runtime_options: dict[str, bool]


VARIANTS = [
    Variant("Full SessionBound", {}),
    Variant("Receipts off", {"receipts_enabled": False}),
    Variant("Budget accounting off", {"budget_accounting_enabled": False}),
    Variant("Receipts and budget off", {"receipts_enabled": False, "budget_accounting_enabled": False}),
]


PATTERNS = {
    "count": "SELECT count(*) AS n FROM expenses",
    "detail_25": "SELECT expense_id, employee_name, amount FROM expenses ORDER BY amount DESC LIMIT 25",
    "group_by": "SELECT category, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY category ORDER BY total DESC",
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


def open_session(variant: Variant, suffix: str, max_queries: int) -> tuple[dict[str, Any], dict[str, Any]]:
    safe_agent_suffix = "".join(ch if ch.isalnum() else "_" for ch in suffix.lower())[-36:]
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"tdsc-ablation-{safe_agent_suffix}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    task_body: dict[str, Any] = {
        "task_id": f"tdsc_ablation_{suffix}",
        "task_type": "monthly_travel_expense_review",
        "delegator": "user:alice",
        "actor": "agent:travel-expense-analyst",
        "credential_id": credential.get("credential_id"),
        "department_id": "dep_sales",
        "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
        "max_rows": 5000,
        "max_queries": max_queries,
    }
    if variant.runtime_options:
        task_body["runtime_options"] = variant.runtime_options
    task = post_json("/tasks", task_body)
    return credential, task


def connect_session(variant: Variant, suffix: str, max_queries: int):
    credential, task = open_session(variant, suffix, max_queries)
    dsn = f"postgresql://{credential['db_user']}:{credential['db_password']}@{DB_HOST}:5432/{DB_NAME}"
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
        cur.fetchone()
    return conn


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[idx]


def summarize(latencies: list[float], rows: int, errors: list[str], state: dict[str, Any], receipt_count: int) -> dict[str, Any]:
    return {
        "p50_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 0.95),
        "mean_ms": statistics.mean(latencies) if latencies else None,
        "stddev_ms": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        "rows": rows,
        "measurements": len(latencies),
        "errors": len(errors),
        "error_samples": errors[:3],
        "state": state,
        "receipt_count": receipt_count,
    }


def inspect_state(cur) -> dict[str, Any]:
    cur.execute("SELECT * FROM taskbound.inspect_task_state()")
    row = cur.fetchone()
    if row is None:
        return {}
    names = [d.name for d in cur.description]
    return dict(zip(names, row))


def receipt_count(cur) -> int:
    cur.execute("SELECT count(*) FROM taskbound.receipts()")
    return cur.fetchone()[0]


def run_once(cur, sql_text: str) -> int:
    cur.execute("SELECT * FROM taskbound.run(%s)", (sql_text,))
    return len(cur.fetchall()) if cur.description is not None else 0


def run_variant_pattern(variant: Variant, pattern_name: str, sql_text: str, run_id: str) -> dict[str, Any]:
    errors: list[str] = []
    rows = 0
    latencies: list[float] = []
    variant_slug = "".join(ch if ch.isalnum() else "_" for ch in variant.name.lower())

    warmup_conn = connect_session(variant, f"{run_id}_{variant_slug}_{pattern_name}_warmup", max(WARMUP, 1))
    try:
        with warmup_conn.cursor() as cur:
            for _ in range(WARMUP):
                try:
                    rows = run_once(cur, sql_text)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
    finally:
        warmup_conn.close()

    measured_conn = connect_session(variant, f"{run_id}_{variant_slug}_{pattern_name}_measured", max(MEASURED, 1))
    try:
        with measured_conn.cursor() as cur:
            for _ in range(MEASURED):
                try:
                    start = time.perf_counter()
                    rows = run_once(cur, sql_text)
                    latencies.append((time.perf_counter() - start) * 1000)
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
            state = inspect_state(cur)
            receipts = receipt_count(cur)
    finally:
        measured_conn.close()

    return summarize(latencies, rows, errors, state, receipts)


def main() -> None:
    run_id = str(int(time.time()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "run_id": run_id,
        "warmup": WARMUP,
        "measured": MEASURED,
        "variants": {},
    }

    for variant in VARIANTS:
        variant_results: dict[str, Any] = {}
        for pattern_name, sql_text in PATTERNS.items():
            variant_results[pattern_name] = run_variant_pattern(variant, pattern_name, sql_text, run_id)
        results["variants"][variant.name] = variant_results

    full = results["variants"].get("Full SessionBound", {})
    for variant_name, variant_results in results["variants"].items():
        for pattern_name, summary in variant_results.items():
            full_p50 = full.get(pattern_name, {}).get("p50_ms")
            p50 = summary.get("p50_ms")
            summary["p50_delta_vs_full_pct"] = None
            if full_p50 and p50 is not None:
                summary["p50_delta_vs_full_pct"] = ((p50 - full_p50) / full_p50) * 100

    json_path = OUT_DIR / f"ablation_{run_id}.json"
    csv_path = OUT_DIR / f"ablation_{run_id}.csv"
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "variant",
            "pattern",
            "p50_ms",
            "p95_ms",
            "mean_ms",
            "stddev_ms",
            "rows",
            "errors",
            "receipt_count",
            "query_count",
            "p50_delta_vs_full_pct",
        ])
        for variant_name, variant_results in results["variants"].items():
            for pattern_name, summary in variant_results.items():
                writer.writerow([
                    variant_name,
                    pattern_name,
                    summary.get("p50_ms"),
                    summary.get("p95_ms"),
                    summary.get("mean_ms"),
                    summary.get("stddev_ms"),
                    summary.get("rows"),
                    summary.get("errors"),
                    summary.get("receipt_count"),
                    (summary.get("state") or {}).get("query_count"),
                    summary.get("p50_delta_vs_full_pct"),
                ])
    print(json_path)


if __name__ == "__main__":
    main()
