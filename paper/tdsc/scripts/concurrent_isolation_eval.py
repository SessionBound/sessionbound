#!/usr/bin/env python3
"""Validate task isolation for concurrent SessionBound task sessions."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg


REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")


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


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"ok": False, "http_error": exc.code, **payload}
    except (urllib.error.URLError, ConnectionResetError, socket.timeout) as exc:
        return {"ok": False, "error": str(exc)}


def wait_for_api() -> None:
    for _ in range(45):
        try:
            urllib.request.urlopen(BASE_URL.rstrip("/") + "/", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {BASE_URL}")


def issue_credential(agent_id: str) -> dict[str, Any]:
    credential = post_json(
        "/credentials",
        {
            "agent_id": agent_id,
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 15,
        },
    )
    if not credential.get("db_user"):
        raise RuntimeError(f"credential issue failed: {credential}")
    return credential


def issue_task(task_id: str, credential_id: str, *, max_queries: int, max_rows: int) -> dict[str, Any]:
    task = post_json(
        "/tasks",
        {
            "task_id": task_id,
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential_id,
            "department_id": "dep_sales",
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_rows": max_rows,
            "max_queries": max_queries,
        },
    )
    if not task.get("payload_text"):
        raise RuntimeError(f"task issue failed: {task}")
    return task


def credential_dsn(credential: dict[str, Any], host: str, port: str, dbname: str) -> str:
    return f"postgresql://{credential['db_user']}:{credential['db_password']}@{host}:{port}/{dbname}"


def rows_as_dicts(cur) -> list[dict[str, Any]]:
    columns = [desc.name for desc in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def bind(cur, task: dict[str, Any]) -> dict[str, Any]:
    cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
    return cur.fetchone()[0]


def run_sql(cur, sql: str) -> dict[str, Any]:
    try:
        cur.execute("SELECT * FROM taskbound.run(%s)", (sql,))
        rows = [row[0] for row in cur.fetchall()]
        return {"ok": True, "rows": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def state(cur) -> dict[str, Any]:
    cur.execute("SELECT * FROM taskbound.inspect_task_state()")
    rows = rows_as_dicts(cur)
    return rows[0] if rows else {}


def receipts(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT task_id, decision, rows_returned, unique_rows_added,
               remaining_unique_row_budget, reason
        FROM taskbound.receipts()
        ORDER BY created_at, receipt_id
        """
    )
    return rows_as_dicts(cur)


def record(name: str, passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "evidence": evidence,
    }


def evaluate(host: str, port: str, dbname: str) -> dict[str, Any]:
    wait_for_api()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    cred_a = issue_credential(f"tdsc-isolation-a-{run_id}")
    cred_b = issue_credential(f"tdsc-isolation-b-{run_id}")
    task_a = issue_task(f"tdsc_isolation_a_{run_id}", cred_a["credential_id"], max_queries=5, max_rows=2)
    task_b = issue_task(f"tdsc_isolation_b_{run_id}", cred_b["credential_id"], max_queries=5, max_rows=5)

    records: list[dict[str, Any]] = []
    dsn_a = credential_dsn(cred_a, host, port, dbname)
    dsn_b = credential_dsn(cred_b, host, port, dbname)

    with psycopg.connect(dsn_a, autocommit=True) as conn_a, psycopg.connect(dsn_b, autocommit=True) as conn_b:
        with conn_a.cursor() as cur_a, conn_b.cursor() as cur_b:
            bound_a = bind(cur_a, task_a)
            bound_b = bind(cur_b, task_b)
            records.append(
                record(
                    "two_active_tasks_independent_credentials",
                    bound_a.get("task_id") != bound_b.get("task_id")
                    and bound_a.get("credential_id") != bound_b.get("credential_id"),
                    {"bound_a": bound_a, "bound_b": bound_b},
                )
            )

            query_a = run_sql(cur_a, "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 2")
            query_b = run_sql(cur_b, "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 1")
            state_a = state(cur_a)
            state_b_before = state(cur_b)
            records.append(
                record(
                    "independent_budget_counters",
                    query_a["ok"]
                    and query_b["ok"]
                    and state_a.get("query_count") == 1
                    and state_a.get("returned_rows") == 2
                    and state_a.get("unique_expense_rows") == 2
                    and state_b_before.get("query_count") == 1
                    and state_b_before.get("returned_rows") == 1
                    and state_b_before.get("unique_expense_rows") == 1,
                    {
                        "query_a": query_a,
                        "query_b": query_b,
                        "state_a": state_a,
                        "state_b": state_b_before,
                    },
                )
            )

            over_budget_a = run_sql(cur_a, "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 3")
            state_b_after = state(cur_b)
            records.append(
                record(
                    "task_a_over_budget_does_not_alter_task_b",
                    (not over_budget_a["ok"]) and state_b_after == state_b_before,
                    {
                        "over_budget_a": over_budget_a,
                        "state_b_before": state_b_before,
                        "state_b_after": state_b_after,
                    },
                )
            )

            receipts_a = receipts(cur_a)
            receipts_b = receipts(cur_b)
            task_a_id = bound_a.get("task_id")
            task_b_id = bound_b.get("task_id")
            records.append(
                record(
                    "receipt_chains_are_task_specific",
                    bool(receipts_a)
                    and bool(receipts_b)
                    and all(r.get("task_id") == task_a_id for r in receipts_a)
                    and all(r.get("task_id") == task_b_id for r in receipts_b),
                    {"receipts_a": receipts_a, "receipts_b": receipts_b},
                )
            )

            task_c = issue_task(f"tdsc_isolation_c_{run_id}", cred_a["credential_id"], max_queries=5, max_rows=5)
            try:
                bind(cur_a, task_c)
                same_backend = {"ok": True}
            except Exception as exc:
                same_backend = {"ok": False, "error": str(exc)}
            records.append(
                record(
                    "same_backend_cannot_bind_second_active_task",
                    (not same_backend["ok"]) and "already bound" in same_backend.get("error", ""),
                    same_backend,
                )
            )

    with psycopg.connect(dsn_a, autocommit=True) as mismatch_conn:
        with mismatch_conn.cursor() as mismatch_cur:
            try:
                bind(mismatch_cur, task_b)
                mismatch = {"ok": True}
            except Exception as exc:
                mismatch = {"ok": False, "error": str(exc)}
    records.append(
        record(
            "credential_a_with_token_b_denied",
            (not mismatch["ok"]) and "does not match" in mismatch.get("error", ""),
            mismatch,
        )
    )

    passed = sum(1 for item in records if item["passed"])
    return {
        "run": {
            "case_count": len(records),
            "passed": passed,
            "failed": len(records) - passed,
            "commit": git_commit(),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate concurrent task isolation")
    parser.add_argument("--db-host", default=os.environ.get("TDSC_DB_HOST", "localhost"))
    parser.add_argument("--db-port", default=os.environ.get("TDSC_DB_PORT", "15432"))
    parser.add_argument("--db-name", default=os.environ.get("TDSC_DB_NAME", "travel"))
    parser.add_argument("--output-dir", default="paper/tdsc/raw_results")
    args = parser.parse_args()

    result = evaluate(args.db_host, args.db_port, args.db_name)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"concurrent_isolation_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")

    print(output_path)
    print(json.dumps(result["run"], ensure_ascii=False, indent=2))
    if result["run"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
