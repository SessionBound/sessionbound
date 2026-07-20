#!/usr/bin/env python3
"""Validate controlled-command object-level serialization.

The race covered here is two separately approved payment tasks attempting to
pay the same business object at the same time.  The expected invariant is one
business effect: one ledger row, one paid event, and one final paid status.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from evidence_metadata import git_metadata


REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")
CONTROL_PLANE_KEY = os.environ.get("TASKBOUND_CONTROL_PLANE_KEY", "tdsc-demo-control-plane-key")
DB_HOST = os.environ.get("TDSC_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("TDSC_DB_PORT", "15432")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    f"postgresql://postgres:postgres@{DB_HOST}:{DB_PORT}/{DB_NAME}",
)

def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-TaskBound-Control-Plane-Key": CONTROL_PLANE_KEY,
        },
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


def issue_credential(run_id: str, index: int) -> dict[str, Any]:
    credential = post_json(
        "/credentials",
        {
            "agent_id": f"tdsc-command-race-{index}-{run_id}",
            "actor": "agent:payment-control-analyst",
            "ttl_minutes": 15,
        },
    )
    if not credential.get("credential_id"):
        raise RuntimeError(f"credential issue failed: {credential}")
    return credential


def issue_payment_task(run_id: str, index: int, credential_id: str) -> dict[str, Any]:
    task = post_json(
        "/tasks",
        {
            "task_id": f"tdsc_command_race_{index}_{run_id}",
            "task_type": "payment_readiness_audit",
            "delegator": "user:fiona",
            "actor": "agent:payment-control-analyst",
            "credential_id": credential_id,
            "department_id": "dep_fin",
            "scope": {"expense_month": "2026-06", "department_id": "dep_fin"},
            "max_rows": 200,
            "max_queries": 10,
        },
    )
    if not task.get("payload_text"):
        raise RuntimeError(f"task issue failed: {task}")
    return task


def cleanup_expense(expense_id: str) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_data.ledger_entries WHERE expense_id = %s", (expense_id,))
            cur.execute("DELETE FROM app_data.approval_events WHERE expense_id = %s", (expense_id,))
            cur.execute("DELETE FROM app_data.expenses WHERE expense_id = %s", (expense_id,))


def seed_payable_expense(expense_id: str) -> None:
    cleanup_expense(expense_id)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_data.expenses (
                  expense_id, tenant_id, employee_id, department_id, expense_month,
                  category, merchant, city, amount, submitted_at, status
                )
                VALUES (
                  %s, 'company_a', 'emp_001', 'dep_fin', '2026-06',
                  'taxi', 'Command Race Fixture', 'Shanghai', 42.00,
                  clock_timestamp(), 'payable'
                )
                """,
                (expense_id,),
            )


def command_attempt(credential: dict[str, Any], task: dict[str, Any], expense_id: str, index: int) -> dict[str, Any]:
    started = time.perf_counter()
    result = post_json(
        "/agent-command",
        {
            "credential": credential,
            "payload_text": task["payload_text"],
            "signature": task["signature"],
            "command_name": "pay_expense",
            "args": {
                "expense_id": expense_id,
                "memo": f"command race attempt {index}",
            },
        },
    )
    return {
        "index": index,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "ok": bool(result.get("ok")),
        "result": result,
    }


def inspect_business_effect(expense_id: str) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT status FROM app_data.expenses WHERE expense_id = %s", (expense_id,))
            expense = cur.fetchone()
            cur.execute("SELECT count(*)::int AS n FROM app_data.ledger_entries WHERE expense_id = %s", (expense_id,))
            ledger_count = cur.fetchone()["n"]
            cur.execute(
                """
                SELECT count(*)::int AS n
                FROM app_data.approval_events
                WHERE expense_id = %s
                  AND event_type = 'paid'
                """,
                (expense_id,),
            )
            paid_event_count = cur.fetchone()["n"]
    return {
        "expense_status": expense["status"] if expense else None,
        "ledger_count": ledger_count,
        "paid_event_count": paid_event_count,
    }


def run_eval(*, keep_artifacts: bool) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    expense_id = f"tdsc_cmd_race_{run_id}"
    metadata = git_metadata(REPO_ROOT)
    seed_payable_expense(expense_id)
    credentials = [issue_credential(run_id, idx) for idx in range(2)]
    tasks = [
        issue_payment_task(run_id, idx, credentials[idx]["credential_id"])
        for idx in range(2)
    ]

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            attempts = list(
                executor.map(
                    lambda idx: command_attempt(credentials[idx], tasks[idx], expense_id, idx),
                    range(2),
                )
            )
        effect = inspect_business_effect(expense_id)
        successes = sum(1 for attempt in attempts if attempt.get("ok"))
        passed = (
            successes == 1
            and effect.get("expense_status") == "paid"
            and effect.get("ledger_count") == 1
            and effect.get("paid_event_count") == 1
        )
        return {
            "run": {
                "run_id": run_id,
                "commit": metadata["commit"],
                "git": metadata,
                "expense_id": expense_id,
                "passed": passed,
                "expected": "exactly one concurrent pay_expense attempt has a business effect",
            },
            "attempts": attempts,
            "business_effect": effect,
        }
    finally:
        if not keep_artifacts:
            cleanup_expense(expense_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = run_eval(keep_artifacts=args.keep_artifacts)
    path = output_dir / f"command_concurrency_{payload['run']['run_id']}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(path)
    return 0 if payload["run"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
