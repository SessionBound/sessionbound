#!/usr/bin/env python3
"""Focused regression for query preflight/release crash consistency.

The historical bug class was a durable query-count reservation committed before
the release receipt. If the bound backend died in that window, the task could
show a budget transition with no receipt explaining it. The current contract is
that preflight is read-only; the durable transition happens with the release or
post-execution denial receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from evidence_metadata import git_metadata


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "app"))

from taskbound_demo import default_task  # noqa: E402


AGENT_DSN = os.environ.get(
    "TDSC_AGENT_DSN",
    "postgresql://agent_app:agentpass@localhost:15432/travel",
)
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:15432/travel",
)


def one_row(cur) -> dict[str, Any]:
    row = cur.fetchone()
    return dict(row) if row else {}


def admin_state(task_id: str) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, budget_account, query_count, returned_rows,
                       unique_expense_rows, revoked
                FROM taskbound.task_execution_state
                WHERE task_id = %s
                """,
                (task_id,),
            )
            state = one_row(cur)
            cur.execute(
                """
                SELECT count(*)::int AS receipt_count
                FROM taskbound.task_query_receipts
                WHERE task_id = %s
                """,
                (task_id,),
            )
            state["receipt_count"] = int(cur.fetchone()["receipt_count"])
            return state


def active_binding(task_id: str) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, binding_id::text AS binding_id, fence_token,
                       advisory_lock_key, owner_backend_pid
                FROM taskbound.active_sessions
                WHERE task_id = %s
                """,
                (task_id,),
            )
            return one_row(cur)


def terminate_backend(pid: int) -> bool:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
            return bool(cur.fetchone()[0])


def preflight_status(task: dict[str, Any], binding: dict[str, Any], sql: str) -> str:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT taskbound.native_reserve_query_status(
                  %s, %s, %s, %s, true, true, %s::uuid, %s
                )
                """,
                (
                    task["task_id"],
                    task.get("budget_account", task["task_id"]),
                    sql,
                    int(task["payload"]["budgets"]["max_queries"]),
                    binding["binding_id"],
                    int(binding["fence_token"]),
                ),
            )
            return str(cur.fetchone()[0])


def run_eval() -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    task_id = f"task_receipt_crash_{run_id}"
    metadata = git_metadata(REPO_ROOT)
    payload_text, signature = default_task(task_id=task_id, max_queries=3, max_rows=10)
    payload = json.loads(payload_text)
    task = {
        "task_id": task_id,
        "payload": payload,
        "budget_account": payload.get("budget_account", task_id),
    }
    sql = "SELECT expense_id FROM expenses ORDER BY expense_id LIMIT 1"

    owner_conn = psycopg.connect(AGENT_DSN, autocommit=True)
    try:
        with owner_conn.cursor() as owner_cur:
            owner_cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
            first_bind = owner_cur.fetchone()[0]
            binding = active_binding(task_id)
            before = admin_state(task_id)
            status = preflight_status(task, binding, sql)
            after_preflight = admin_state(task_id)
            terminated = terminate_backend(int(binding["owner_backend_pid"]))
            time.sleep(0.5)
    finally:
        try:
            owner_conn.close()
        except Exception:
            pass

    with psycopg.connect(AGENT_DSN, autocommit=True) as recovery_conn:
        with recovery_conn.cursor() as cur:
            cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
            recovered_bind = cur.fetchone()[0]
            after_recovery = admin_state(task_id)
            cur.execute(sql)
            rows = cur.fetchall()
            after_release = admin_state(task_id)
            cur.execute("SELECT taskbound.unbind_task()")

    preflight_read_only = (
        status == "ok"
        and after_preflight.get("query_count") == before.get("query_count")
        and after_preflight.get("receipt_count") == before.get("receipt_count")
    )
    recovery_preserved_state = (
        terminated
        and recovered_bind.get("stale_recovered") is True
        and after_recovery.get("query_count") == before.get("query_count")
        and after_recovery.get("receipt_count") == before.get("receipt_count")
    )
    release_linearized = (
        len(rows) == 1
        and after_release.get("query_count") == before.get("query_count", 0) + 1
        and after_release.get("returned_rows") == before.get("returned_rows", 0) + 1
        and after_release.get("receipt_count") == before.get("receipt_count", 0) + 1
    )

    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "git_commit": metadata["commit"],
            "git": metadata,
            "task_id": task_id,
            "passed": preflight_read_only and recovery_preserved_state and release_linearized,
        },
        "checks": {
            "preflight_read_only": preflight_read_only,
            "recovery_preserved_state": recovery_preserved_state,
            "release_linearized": release_linearized,
        },
        "evidence": {
            "first_bind": first_bind,
            "preflight_status": status,
            "terminated_owner_backend": terminated,
            "recovered_bind": recovered_bind,
            "state_before": before,
            "state_after_preflight": after_preflight,
            "state_after_recovery": after_recovery,
            "state_after_release": after_release,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()

    result = run_eval()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = out_dir / f"receipt_crash_consistency_{stamp}.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result["run"], indent=2, sort_keys=True))
    print(output_path)
    return 0 if result["run"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
