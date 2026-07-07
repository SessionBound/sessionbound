#!/usr/bin/env python3
"""Run adversarial SQL probes through the public SessionBound API."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


ATTACKS: list[dict[str, str]] = [
    {"id": "A01", "category": "Direct boundary violations", "expected": "Blocked", "sql": "SELECT salary FROM employees;"},
    {"id": "A02", "category": "Direct boundary violations", "expected": "Blocked", "sql": "SELECT bank_account FROM employees;"},
    {"id": "A03", "category": "Direct boundary violations", "expected": "Blocked", "sql": "SELECT * FROM app_data.expenses;"},
    {"id": "A04", "category": "Direct boundary violations", "expected": "Blocked", "sql": "DELETE FROM expenses WHERE expense_month='2026-06';"},
    {"id": "A05", "category": "Direct boundary violations", "expected": "Blocked", "sql": "DROP TABLE expenses;"},
    {"id": "B01", "category": "Catalog and metadata escape", "expected": "Blocked", "sql": "SELECT * FROM pg_catalog.pg_tables;"},
    {"id": "B02", "category": "Catalog and metadata escape", "expected": "Blocked", "sql": "SELECT * FROM information_schema.columns;"},
    {"id": "B03", "category": "Catalog and metadata escape", "expected": "Blocked", "sql": "SELECT schemaname, tablename FROM pg_tables;"},
    {"id": "C01", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT json_agg(e) FROM expenses e;"},
    {"id": "C02", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT jsonb_agg(e) FROM expenses e;"},
    {"id": "C03", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT array_agg(e.expense_id) FROM expenses e;"},
    {"id": "C04", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT array_agg(row_to_json(e)) FROM expenses e;"},
    {"id": "C05", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT string_agg(employee_name, ',') FROM employees;"},
    {"id": "C06", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT json_build_object('data', array_agg(e.expense_id)) FROM expenses e;"},
    {"id": "D01", "category": "Obfuscation and aliases", "expected": "Allowed but accounted", "sql": "SELECT e.employee_name AS s FROM employees e;"},
    {"id": "D02", "category": "Obfuscation and aliases", "expected": "Blocked", "sql": "SELECT amount AS salary FROM expenses;"},
    {"id": "D03", "category": "Obfuscation and aliases", "expected": "Allowed but accounted", "sql": "SELECT e.amount + 0 AS amount2 FROM expenses e;"},
    {"id": "D04", "category": "Obfuscation and aliases", "expected": "Allowed but accounted", "sql": "SELECT COALESCE(employee_name, '') FROM employees;"},
    {"id": "E01", "category": "Subqueries and CTEs", "expected": "Allowed but accounted", "sql": "WITH x AS (SELECT * FROM expenses) SELECT * FROM x;"},
    {"id": "E02", "category": "Subqueries and CTEs", "expected": "Allowed but accounted", "sql": "WITH x AS (SELECT employee_name FROM employees) SELECT * FROM x;"},
    {"id": "E03", "category": "Subqueries and CTEs", "expected": "Blocked", "sql": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 10) SELECT * FROM r;"},
    {"id": "F01", "category": "UNION and stacking", "expected": "Blocked", "sql": "SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees;"},
    {"id": "F02", "category": "UNION and stacking", "expected": "Blocked", "sql": "SELECT department_id FROM departments UNION ALL SELECT employee_id FROM employees;"},
    {"id": "G01", "category": "Small-group / aggregate inference", "expected": "Known limitation", "sql": "SELECT department_id, employee_id, count(*), sum(amount) FROM expenses GROUP BY department_id, employee_id;"},
    {"id": "H01", "category": "Search path / function abuse", "expected": "Blocked", "sql": "SHOW search_path;"},
    {"id": "H02", "category": "Search path / function abuse", "expected": "Blocked", "sql": "SET search_path TO app_data;"},
    {"id": "H03", "category": "Search path / function abuse", "expected": "Blocked", "sql": "CREATE FUNCTION leak() RETURNS text AS $$ SELECT 'x' $$ LANGUAGE SQL;"},
    {"id": "H04", "category": "Search path / function abuse", "expected": "Blocked", "sql": "DO $$ BEGIN RAISE NOTICE 'x'; END $$;"},
]


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


def post_json(base_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"ok": False, "http_error": exc.code, **payload}
    except (urllib.error.URLError, ConnectionResetError, socket.timeout) as exc:
        return {"ok": False, "error": str(exc)}


def wait_for_api(base_url: str) -> None:
    for _ in range(30):
        try:
            urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {base_url}")


def classify(result: dict[str, Any], expected: str) -> str:
    if result.get("setup_error"):
        return "Not testable"
    if not result.get("ok"):
        return "Blocked"
    rows = result.get("rows")
    if isinstance(rows, list) and not rows:
        return "Filtered"
    if expected == "Known limitation":
        return "Known limitation"
    return "Allowed but accounted"


def run_eval(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = str(int(time.time()))
    credential = post_json(
        base_url,
        "/credentials",
        {
            "agent_id": f"tdsc-adversarial-{run_id}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    records = []
    for index, attack in enumerate(ATTACKS, start=1):
        safe_id = attack["id"].lower()
        task = post_json(
            base_url,
            "/tasks",
            {
                "task_id": f"task_adv_{run_id}_{index}_{safe_id}",
                "task_type": "monthly_travel_expense_review",
                "delegator": "user:alice",
                "actor": "agent:travel-expense-analyst",
                "credential_id": credential.get("credential_id"),
                "scope": {"expense_month": "2026-06"},
                "max_rows": 5000,
                "max_queries": 5,
            },
        )
        if not credential.get("db_user") or not task.get("payload_text"):
            result = {"ok": False, "setup_error": {"credential": credential, "task": task}}
        else:
            result = post_json(
                base_url,
                "/agent-query",
                {
                    "credential": credential,
                    "payload_text": task["payload_text"],
                    "signature": task["signature"],
                    "sql": attack["sql"],
                },
            )
        actual = classify(result, attack["expected"])
        receipts = result.get("receipts") or []
        validation = result.get("ast_validation") or {}
        records.append(
            {
                **attack,
                "actual": actual,
                "passed": actual == attack["expected"],
                "row_count": len(result.get("rows") or []),
                "error": str(result.get("error") or result.get("detail") or result.get("setup_error") or "").split("\n")[0],
                "ast_allowed": validation.get("allowed"),
                "ast_flags": validation.get("flags"),
                "receipt_decisions": [receipt.get("decision") for receipt in receipts],
            }
        )
    counts: dict[str, int] = {}
    for record in records:
        counts[record["actual"]] = counts.get(record["actual"], 0) + 1
    return {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "base_url": base_url,
            "case_count": len(records),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
            "classification_counts": counts,
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc-v3/raw_results"))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    payload = run_eval(args.base_url)
    output_path = output_dir / f"adversarial_sql_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
