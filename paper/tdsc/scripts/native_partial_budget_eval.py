#!/usr/bin/env python3
"""Validate atomic native over-budget accounting across projections."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "app"))

from taskbound_demo import default_task  # noqa: E402


def default_dsn() -> str:
    if os.environ.get("TDSC_AGENT_DSN"):
        return os.environ["TDSC_AGENT_DSN"]
    host = os.environ.get("TDSC_DB_HOST", "localhost")
    port = os.environ.get("TDSC_DB_PORT", "15432")
    dbname = os.environ.get("TDSC_DB_NAME", "travel")
    return f"postgresql://agent_app:agentpass@{host}:{port}/{dbname}"


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def fetch_state(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT task_id, budget_account, query_count, returned_rows,
               unique_expense_rows, revoked
        FROM taskbound.inspect_task_state()
        """
    )
    columns = [desc.name for desc in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def fetch_receipts(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT decision, reason, rows_returned, unique_rows_added,
               remaining_unique_row_budget
        FROM taskbound.receipts()
        ORDER BY created_at DESC, receipt_id DESC
        LIMIT 5
        """
    )
    columns = [desc.name for desc in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def evaluate(dsn: str) -> dict[str, Any]:
    task_id = f"task_native_partial_budget_{int(time.time())}"
    payload_text, signature = default_task(task_id=task_id, max_queries=5, max_rows=2)
    queries = [
        "SELECT amount FROM expenses ORDER BY expense_id LIMIT 3",
        "SELECT amount AS renamed_amount FROM expenses ORDER BY expense_id LIMIT 3",
        "SELECT category, count(*) FROM expenses GROUP BY category",
    ]
    observations: list[dict[str, Any]] = []

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
            cur.fetchone()
            for sql in queries:
                error = ""
                try:
                    cur.execute(sql)
                    cur.fetchall()
                except Exception as exc:
                    error = str(exc)
                state = fetch_state(cur)
                receipts = fetch_receipts(cur)
                observations.append({"sql": sql, "error": error, "state": state, "receipts": receipts})
            cur.execute("SELECT taskbound.unbind_task()")

    passed = True
    for index, observation in enumerate(observations, start=1):
        state = observation["state"][0] if observation["state"] else {}
        denial = next(
            (
                receipt
                for receipt in observation["receipts"]
                if receipt.get("decision") == "denied"
                and "result tuple budget exceeded" in (receipt.get("reason") or "")
            ),
            {},
        )
        passed = passed and (
            "result tuple budget exceeded" in observation["error"]
            and state.get("query_count") == index
            and state.get("returned_rows") == 0
            and state.get("unique_expense_rows") == 0
            and denial.get("rows_returned") == 0
            and denial.get("unique_rows_added") == 0
        )
    record = {
        "id": "NPB01",
        "name": "native_over_budget_projection_independent_atomic_denial",
        "expected": "Projection, alias, and aggregate shapes cannot release a prefix or bypass the conservative tuple budget",
        "actual": "Passed" if passed else "Failed",
        "passed": passed,
        "evidence": {
            "observations": observations,
        },
    }
    return {
        "records": [record],
        "run": {
            "case_count": 1,
            "passed": 1 if passed else 0,
            "failed": 0 if passed else 1,
            "database_url": dsn.replace(":agentpass@", ":***@"),
            "commit": git_commit(),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=default_dsn())
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()

    payload = evaluate(args.dsn)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    output_path = out_dir / f"native_partial_budget_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    print(output_path)
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
