#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")
OUT_DIR = Path(os.environ.get("TDSC_OUT_DIR", "paper/tdsc/experiments/raw_results"))
LEVELS = [1, 5, 20]
QUERIES = [
    "SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 3",
    "SELECT department_name, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY department_name",
    "WITH high AS (SELECT expense_id, amount FROM expenses WHERE amount > 1000) SELECT count(*) AS n FROM high",
    "SELECT expense_id, row_number() OVER (ORDER BY amount DESC) AS rn FROM expenses LIMIT 5",
    "SELECT count(*) AS n FROM expenses",
]


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"ok": False, "http_error": exc.code, **payload}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run_session(level: int, index: int, run_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"tdsc-concurrency-{level}-{index}-{run_id}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 15,
        },
    )
    task = post_json(
        "/tasks",
        {
            "task_id": f"tdsc_conc_{level}_{index}_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "department_id": "dep_sales",
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_rows": 5000,
            "max_queries": 20,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        return {"ok": False, "setup": {"credential": credential, "task": task}}
    query_latencies: list[float] = []
    errors: list[str] = []
    for sql in QUERIES:
        q_start = time.perf_counter()
        result = post_json(
            "/agent-query",
            {
                "credential": credential,
                "payload_text": task["payload_text"],
                "signature": task["signature"],
                "sql": sql,
            },
        )
        query_latencies.append((time.perf_counter() - q_start) * 1000)
        if not result.get("ok"):
            errors.append(str(result.get("error") or result.get("detail") or "unknown"))
    return {
        "ok": not errors,
        "session_ms": (time.perf_counter() - started) * 1000,
        "query_latencies_ms": query_latencies,
        "errors": errors[:3],
    }


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((len(ordered) - 1) * p))
    return ordered[idx]


def main() -> None:
    run_id = str(int(time.time()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"run_id": run_id, "levels": {}}
    for level in LEVELS:
        with concurrent.futures.ThreadPoolExecutor(max_workers=level) as executor:
            sessions = list(executor.map(lambda i: run_session(level, i, run_id), range(level)))
        query_latencies = [lat for s in sessions for lat in s.get("query_latencies_ms", [])]
        successes = sum(1 for s in sessions if s.get("ok"))
        failures = level - successes
        results["levels"][str(level)] = {
            "sessions": sessions,
            "successful_sessions": successes,
            "failed_sessions": failures,
            "error_rate": failures / level,
            "query_p50_ms": statistics.median(query_latencies) if query_latencies else None,
            "query_p95_ms": percentile(query_latencies, 0.95),
            "session_p50_ms": statistics.median([s["session_ms"] for s in sessions]) if sessions else None,
        }
    path = OUT_DIR / f"concurrency_{run_id}.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
