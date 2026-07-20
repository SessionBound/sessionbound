#!/usr/bin/env python3
"""Evaluate release-time binding validity for expiry and policy drift.

This focuses on the review's lifecycle gap: validity must hold at the final
release barrier, not only when a statement is admitted.  Each case binds a real
agent session, mutates authoritative state from the admin side while the
binding remains live, and invokes the fenced release transition.  The expected
outcome is a denial receipt with zero released rows.  The suite also checks
that the approved safe-view snapshot carries database, view, dependency, and
view-option metadata rather than only view SQL text.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

from evidence_metadata import git_metadata
from path_consistency_eval import (
    IssuedTask,
    credential_dsn,
    post_json_retry,
    wait_for_api,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "paper" / "tdsc" / "raw_results"
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:15432/travel",
)
BASE_URL = os.environ.get("TDSC_BASE_URL", "http://localhost:8000")


def issue_task(base_url: str, run_id: str, suffix: str) -> IssuedTask:
    actor = "agent:travel-expense-analyst"
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"lifecycle-{run_id}-{suffix}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    )
    task_id = f"task_lifecycle_{run_id}_{suffix}"
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": task_id,
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_queries": 20,
            "max_rows": 5000,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(
            f"failed to create credential/task for {suffix}: "
            f"credential={credential} task={task}"
        )
    return IssuedTask(credential=credential, task=task)


def bind_agent(issued: IssuedTask) -> tuple[psycopg.Connection[Any], dict[str, Any]]:
    conn = psycopg.connect(credential_dsn(issued.credential), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT taskbound.bind_task(%s, %s)",
                (issued.task["payload_text"], issued.task["signature"]),
            )
            return conn, cur.fetchone()[0]
    except Exception:
        conn.close()
        raise


def admin_receipts(task_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT decision, reason, rows_returned, unique_rows_added,
                       remaining_unique_row_budget, created_at::text AS created_at
                FROM taskbound.task_query_receipts
                WHERE task_id = %s
                ORDER BY created_at DESC, receipt_id DESC
                LIMIT 5
                """,
                (task_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def admin_state(task_id: str) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT query_count, returned_rows, unique_expense_rows, revoked
                FROM taskbound.task_execution_state
                WHERE task_id = %s
                """,
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else {}


def call_release(binding: dict[str, Any], sql_text: str) -> str:
    try:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT taskbound.native_finish_query_status(
                      %s, %s, %s, %s, ARRAY[]::text[], %s, %s,
                      true, true, %s::uuid, %s
                    )
                    """,
                    (
                        binding["task_id"],
                        binding.get("budget_account") or binding["task_id"],
                        sql_text,
                        1,
                        20,
                        5000,
                        binding["binding_id"],
                        binding["fence_token"],
                    ),
                )
                status = cur.fetchone()[0]
                return str(status)
        return ""
    except Exception as exc:
        return str(exc)


def cleanup_binding(agent_conn: psycopg.Connection[Any] | None, binding: dict[str, Any]) -> None:
    if binding.get("binding_id") and binding.get("fence_token"):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT taskbound.release_active_binding_row(
                      %s::uuid, %s, 'BINDING_TEST_CLEANUP'
                    )
                    """,
                    (binding["binding_id"], binding["fence_token"]),
                )
    if agent_conn is not None:
        agent_conn.close()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT taskbound.reap_stale_bindings()")


def snapshot_contract_metadata_present() -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT taskbound.safe_view_registry_snapshot(ARRAY['expenses'])")
            snapshot = cur.fetchone()[0]

    views = snapshot.get("views", {}) if isinstance(snapshot, dict) else {}
    expenses = views.get("expenses", {}) if isinstance(views, dict) else {}
    checks = {
        "top_level_database_oid": bool(snapshot.get("database_oid")),
        "top_level_dependency_hash": bool(snapshot.get("view_dependency_hash")),
        "top_level_option_hash": bool(snapshot.get("view_option_hash")),
        "view_oid": bool(expenses.get("view_oid")),
        "view_database_oid": bool(expenses.get("database_oid")),
        "view_dependency_hash": bool(expenses.get("view_dependency_hash")),
        "view_options": isinstance(expenses.get("view_options"), list),
        "dependencies": bool(expenses.get("dependencies")),
    }
    return {
        "name": "snapshot_contract_metadata_present",
        "passed": all(checks.values()),
        "checks": checks,
        "snapshot_summary": {
            "database_oid": snapshot.get("database_oid"),
            "view_definition_hash": snapshot.get("view_definition_hash"),
            "view_dependency_hash": snapshot.get("view_dependency_hash"),
            "view_option_hash": snapshot.get("view_option_hash"),
            "expenses_view_oid": expenses.get("view_oid"),
            "expenses_dependency_count": len(expenses.get("dependencies") or []),
            "expenses_view_options": expenses.get("view_options"),
        },
    }


def helper_function_dependency_hash_changes() -> dict[str, Any]:
    restore_sql = ""
    before_hash = ""
    drift_hash = ""
    restored_hash = ""
    error = ""
    try:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_catalog.pg_get_functiondef('taskbound.claim(text[])'::regprocedure)")
                restore_sql = cur.fetchone()[0]
                cur.execute("SELECT taskbound.safe_view_registry_snapshot(ARRAY['expenses'])->>'view_dependency_hash'")
                before_hash = cur.fetchone()[0]
                cur.execute(
                    """
                    CREATE OR REPLACE FUNCTION taskbound.claim(path text[])
                    RETURNS text
                    LANGUAGE sql
                    SECURITY DEFINER
                    SET search_path = taskbound, pg_temp
                    AS $$
                      SELECT (taskbound.require_payload() #>> path) || ''
                    $$;
                    """
                )
                cur.execute("SELECT taskbound.safe_view_registry_snapshot(ARRAY['expenses'])->>'view_dependency_hash'")
                drift_hash = cur.fetchone()[0]
    except Exception as exc:
        error = str(exc).splitlines()[0]
    finally:
        if restore_sql:
            with psycopg.connect(ADMIN_DSN, autocommit=True) as restore_conn:
                with restore_conn.cursor() as restore_cur:
                    restore_cur.execute(restore_sql)
                    restore_cur.execute("SELECT taskbound.safe_view_registry_snapshot(ARRAY['expenses'])->>'view_dependency_hash'")
                    restored_hash = restore_cur.fetchone()[0]

    passed = bool(before_hash and drift_hash and restored_hash) and before_hash != drift_hash and before_hash == restored_hash
    return {
        "name": "helper_function_dependency_hash_changes",
        "passed": passed,
        "error": error,
        "before_hash": before_hash,
        "drift_hash": drift_hash,
        "restored_hash": restored_hash,
    }


def release_case(
    base_url: str,
    run_id: str,
    suffix: str,
    setup: Callable[[dict[str, Any]], None],
    expected_reason: str,
    cleanup: Callable[[], None] | None = None,
) -> dict[str, Any]:
    issued = issue_task(base_url, run_id, suffix)
    agent_conn: psycopg.Connection[Any] | None = None
    binding: dict[str, Any] = {}
    error = ""
    try:
        agent_conn, binding = bind_agent(issued)
        setup(binding)
        error = call_release(
            binding,
            "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 1",
        )
    finally:
        if cleanup is not None:
            cleanup()
        cleanup_binding(agent_conn, binding)

    receipts = admin_receipts(binding.get("task_id", issued.task["payload"]["task_id"]))
    state = admin_state(binding.get("task_id", issued.task["payload"]["task_id"]))
    latest = receipts[0] if receipts else {}
    passed = (
        expected_reason in error
        and latest.get("decision") == "denied"
        and expected_reason in str(latest.get("reason") or "")
        and latest.get("rows_returned") == 0
        and latest.get("unique_rows_added") == 0
        and state.get("query_count") == 0
        and state.get("returned_rows") == 0
        and state.get("unique_expense_rows") == 0
    )
    return {
        "name": suffix,
        "passed": passed,
        "expected_reason": expected_reason,
        "error": error.splitlines()[0] if error else "",
        "binding": binding,
        "state": state,
        "receipts": receipts,
    }


def expire_active_binding(binding: dict[str, Any]) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE taskbound.active_sessions
                SET token_expires_at = now() - interval '1 second'
                WHERE binding_id = %s::uuid
                  AND fence_token = %s
                """,
                (binding["binding_id"], binding["fence_token"]),
            )


def drift_registry(binding: dict[str, Any]) -> Callable[[], None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT registry_version
                FROM taskbound.safe_view_registry
                WHERE view_name = 'expenses'
                """
            )
            old_version = int(cur.fetchone()["registry_version"])
            cur.execute(
                """
                UPDATE taskbound.safe_view_registry
                SET registry_version = registry_version + 1
                WHERE view_name = 'expenses'
                """
            )

    def restore() -> None:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as restore_conn:
            with restore_conn.cursor() as restore_cur:
                restore_cur.execute(
                    """
                    UPDATE taskbound.safe_view_registry
                    SET registry_version = %s
                    WHERE view_name = 'expenses'
                    """,
                    (old_version,),
                )

    return restore


def drift_view_option(binding: dict[str, Any]) -> Callable[[], None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(reloptions, ARRAY[]::text[]) AS reloptions
                FROM pg_catalog.pg_class
                WHERE oid = 'taskbound.expenses'::regclass
                """
            )
            old_options = list(cur.fetchone()["reloptions"] or [])
            old_security_barrier = next(
                (opt.split("=", 1)[1].lower() for opt in old_options if opt.startswith("security_barrier=")),
                None,
            )
            new_security_barrier = "false" if old_security_barrier == "true" else "true"
            cur.execute(
                f"ALTER VIEW taskbound.expenses SET (security_barrier = {new_security_barrier})"
            )

    def restore() -> None:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as restore_conn:
            with restore_conn.cursor() as restore_cur:
                if old_security_barrier is None:
                    restore_cur.execute("ALTER VIEW taskbound.expenses RESET (security_barrier)")
                else:
                    restore_cur.execute(
                        f"ALTER VIEW taskbound.expenses SET (security_barrier = {old_security_barrier})"
                    )

    return restore


def evaluate(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    metadata = git_metadata(REPO_ROOT)
    records: list[dict[str, Any]] = []

    records.append(snapshot_contract_metadata_present())
    records.append(helper_function_dependency_hash_changes())

    records.append(
        release_case(
            base_url,
            run_id,
            "release_time_token_expiry_denied",
            expire_active_binding,
            "task token is expired",
        )
    )

    cleanup_holder: dict[str, Callable[[], None]] = {}

    def setup_drift(binding: dict[str, Any]) -> None:
        cleanup_holder["restore"] = drift_registry(binding)

    records.append(
        release_case(
            base_url,
            run_id,
            "release_time_safe_view_drift_denied",
            setup_drift,
            "task token safe-view registry snapshot is stale; re-approval is required",
            lambda: cleanup_holder.pop("restore")(),
        )
    )

    def setup_option_drift(binding: dict[str, Any]) -> None:
        cleanup_holder["restore_option"] = drift_view_option(binding)

    records.append(
        release_case(
            base_url,
            run_id,
            "release_time_view_option_drift_denied",
            setup_option_drift,
            "task token safe-view registry snapshot is stale; re-approval is required",
            lambda: cleanup_holder.pop("restore_option")(),
        )
    )

    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "commit": metadata["commit"],
            "git": metadata,
            "base_url": base_url,
            "case_count": len(records),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    payload = evaluate(args.base_url.rstrip("/"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"binding_lifecycle_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
