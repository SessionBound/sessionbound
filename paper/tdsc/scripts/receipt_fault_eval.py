#!/usr/bin/env python3
"""Evaluate receipt-chain completeness and forgery/fault resistance.

This complements the rollback audit by checking the receipt material itself:

* recompute receipt hashes from the fields included in the hardened hash
  formula;
* verify previous-hash chaining and unique execution identifiers;
* simulate field tampering and confirm the hash changes;
* inject a wrong binding fence into the trusted append function and confirm no
  receipt is inserted; and
* verify controlled-command allow/deny receipts, including survival across a
  caller transaction rollback, and confirm an agent cannot forge receipts or
  call private native receipt/budget helpers directly.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from path_consistency_eval import (
    IssuedTask,
    credential_dsn,
    fetch_receipts,
    fetch_state,
    git_commit,
    post_json_retry,
    rows_as_dicts,
    wait_for_api,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "paper" / "tdsc" / "raw_results"
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:15432/travel",
)
SCOPE = {"expense_month": "2026-06", "department_id": "dep_sales"}
SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me").encode("utf-8")
COMMAND_EXPENSE_ID = os.environ.get("TDSC_COMMAND_EXPENSE_ID", "exp_141")


FORGERY_CASES: list[dict[str, Any]] = [
    {
        "id": "RF02",
        "name": "agent_fail_receipt_forgery_blocked",
        "sql": "SELECT taskbound.fail_receipt('SELECT 1', 'forged agent receipt')",
        "forged_marker": "forged agent receipt",
    },
    {
        "id": "RF03",
        "name": "agent_audit_append_receipt_forgery_blocked",
        "sql": (
            "SELECT taskbound.audit_append_receipt("
            "'forged_task', 'forged_budget', gen_random_uuid(), 1, "
            "'forged_digest', 'allowed', 999, 999, 0, "
            "'forged audit append', true, gen_random_uuid(), "
            "'agent:attacker', ARRAY['expenses'])"
        ),
        "forged_marker": "forged audit append",
    },
    {
        "id": "RF04",
        "name": "agent_native_finish_direct_mutation_blocked",
        "sql": (
            "SELECT taskbound.native_finish_query("
            "'forged_task', 'forged_budget', 'SELECT 1', 1, ARRAY[]::text[], "
            "10, 10, true, true, gen_random_uuid(), 1)"
        ),
        "forged_marker": "forged_budget",
    },
    {
        "id": "RF05",
        "name": "agent_direct_receipt_table_insert_blocked",
        "sql": (
            "INSERT INTO taskbound.task_query_receipts ("
            "task_id, budget_account, query_digest, decision, rows_returned, "
            "unique_rows_added, reason"
            ") VALUES ("
            "'forged_task', 'forged_budget', 'forged_digest', 'allowed', "
            "999, 999, 'forged direct insert')"
        ),
        "forged_marker": "forged direct insert",
    },
]


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign(payload_text: str) -> str:
    return hmac.new(SECRET, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_task(
    base_url: str,
    run_id: str,
    suffix: str,
    *,
    max_rows: int = 5000,
    max_queries: int = 20,
    task_type: str = "monthly_travel_expense_review",
    delegator: str = "user:alice",
    actor: str = "agent:travel-expense-analyst",
) -> IssuedTask:
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"receipt-fault-{run_id}-{suffix}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    )
    task_id = f"task_receipt_fault_{run_id}_{suffix}"
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": task_id,
            "task_type": task_type,
            "delegator": delegator,
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": SCOPE,
            "max_queries": max_queries,
            "max_rows": max_rows,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(
            f"failed to create credential/task for {suffix}: "
            f"credential={credential} task={task}"
        )
    task.setdefault("task_id", task_id)
    task.setdefault("budget_account", task_id)
    return IssuedTask(credential=credential, task=task)


def admin_receipts(task_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT receipt_id::text,
                       execution_id::text,
                       receipt_sequence,
                       task_id,
                       budget_account,
                       binding_id::text,
                       fence_token,
                       query_digest,
                       decision,
                       rows_returned,
                       unique_rows_added,
                       remaining_unique_row_budget,
                       COALESCE(reason, '') AS reason,
                       COALESCE(actor, '') AS actor,
                       touched_views,
                       COALESCE(previous_receipt_hash, '') AS previous_receipt_hash,
                       COALESCE(receipt_hash, '') AS receipt_hash,
                       created_at::text AS created_at_text,
                       taskbound.receipt_hash(
                         receipt_id,
                         execution_id,
                         receipt_sequence,
                         task_id,
                         budget_account,
                         binding_id,
                         fence_token,
                         query_digest,
                         decision,
                         rows_returned,
                         unique_rows_added,
                         remaining_unique_row_budget,
                         reason,
                         actor,
                         touched_views,
                         created_at,
                         previous_receipt_hash
                       ) AS recomputed_receipt_hash
                FROM taskbound.task_query_receipts
                WHERE task_id = %s
                ORDER BY receipt_sequence
                """,
                (task_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def receipt_count(task_id: str) -> int:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*)::int AS n FROM taskbound.task_query_receipts WHERE task_id = %s",
                (task_id,),
            )
            return int(cur.fetchone()["n"])


def recompute_receipt_hash(row: dict[str, Any]) -> str:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT taskbound.receipt_hash(
                  %s::uuid, %s::uuid, %s, %s, %s, %s::uuid, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s::text[], %s::timestamptz, %s
                )
                """,
                (
                    row.get("receipt_id"),
                    row.get("execution_id"),
                    int(row.get("receipt_sequence") or 0),
                    row.get("task_id"),
                    row.get("budget_account"),
                    row.get("binding_id"),
                    row.get("fence_token"),
                    row.get("query_digest"),
                    row.get("decision"),
                    int(row.get("rows_returned") or 0),
                    int(row.get("unique_rows_added") or 0),
                    row.get("remaining_unique_row_budget"),
                    row.get("reason") or "",
                    row.get("actor") or "",
                    row.get("touched_views") or [],
                    row.get("created_at_text"),
                    row.get("previous_receipt_hash") or "",
                ),
            )
            return str(cur.fetchone()[0])


def verify_receipt_chain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hash_checks = []
    for row in rows:
        recomputed = recompute_receipt_hash(row)
        hash_checks.append(
            {
                "receipt_id": row["receipt_id"],
                "stored": row["receipt_hash"],
                "recomputed": recomputed,
                "passed": row["receipt_hash"] == recomputed,
            }
        )
    chain_checks = []
    previous = ""
    sequence_checks = []
    for index, row in enumerate(rows, start=1):
        chain_checks.append(
            {
                "receipt_id": row["receipt_id"],
                "expected_previous": previous,
                "actual_previous": row["previous_receipt_hash"],
                "passed": row["previous_receipt_hash"] == previous,
            }
        )
        sequence_checks.append(
            {
                "receipt_id": row["receipt_id"],
                "expected_sequence": index,
                "actual_sequence": int(row.get("receipt_sequence") or 0),
                "passed": int(row.get("receipt_sequence") or 0) == index,
            }
        )
        previous = row["receipt_hash"]

    execution_ids = [row["execution_id"] for row in rows]
    required_fields = [
        "receipt_id",
        "execution_id",
        "receipt_sequence",
        "task_id",
        "budget_account",
        "binding_id",
        "fence_token",
        "query_digest",
        "decision",
        "actor",
        "created_at_text",
        "receipt_hash",
    ]
    field_checks = [
        {
            "receipt_id": row["receipt_id"],
            "missing": [field for field in required_fields if row.get(field) in {None, ""}],
            "passed": all(row.get(field) not in {None, ""} for field in required_fields),
        }
        for row in rows
    ]
    tamper_checks = []
    for row in rows:
        for field, replacement in (
            ("receipt_id", "00000000-0000-0000-0000-000000000000"),
            ("execution_id", "00000000-0000-0000-0000-000000000000"),
            ("receipt_sequence", int(row.get("receipt_sequence") or 0) + 1000),
            ("actor", f"{row.get('actor')}-tampered"),
            ("touched_views", list(row.get("touched_views") or []) + ["tampered_view"]),
            ("rows_returned", int(row.get("rows_returned") or 0) + 1),
        ):
            mutated = dict(row)
            mutated[field] = replacement
            tamper_checks.append(
                {
                    "receipt_id": row["receipt_id"],
                    "field": field,
                    "passed": recompute_receipt_hash(mutated) != row["receipt_hash"],
                }
            )

    return {
        "receipt_count": len(rows),
        "hash_checks": hash_checks,
        "chain_checks": chain_checks,
        "sequence_checks": sequence_checks,
        "field_checks": field_checks,
        "tamper_checks": tamper_checks,
        "unique_execution_ids": len(execution_ids) == len(set(execution_ids)),
        "all_hashes_match": all(item["passed"] for item in hash_checks),
        "chain_ok": all(item["passed"] for item in chain_checks),
        "sequence_ok": all(item["passed"] for item in sequence_checks),
        "required_fields_present": all(item["passed"] for item in field_checks),
        "tamper_changes_hash": all(item["passed"] for item in tamper_checks),
    }


def inject_wrong_fence_receipt(
    *,
    task_id: str,
    budget_account: str,
    binding_id: str,
    fence_token: int,
    actor: str,
) -> dict[str, Any]:
    before = receipt_count(task_id)
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT taskbound.audit_append_receipt(
                  %s, %s, %s::uuid, %s, %s, 'allowed',
                  999, 999, 0, %s, true, gen_random_uuid(), %s, ARRAY['expenses']
                )
                """,
                (
                    task_id,
                    budget_account,
                    binding_id,
                    fence_token + 1,
                    "wrong-fence-forged-query-digest",
                    "wrong fence forged receipt",
                    actor,
                ),
            )
    after = receipt_count(task_id)
    return {
        "before_count": before,
        "after_count": after,
        "passed": before == after,
        "attempted_fence_token": fence_token + 1,
    }


def run_chain_and_fault_case(base_url: str, run_id: str) -> dict[str, Any]:
    issued = issue_task(base_url, run_id, "chain_fault", max_rows=5000, max_queries=20)
    task_id = issued.task["task_id"]
    rows1: list[dict[str, Any]] = []
    rows2: list[Any] = []
    denied_error = ""
    binding: dict[str, Any] = {}
    state: list[dict[str, Any]] = []
    visible_receipts: list[dict[str, Any]] = []

    with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT taskbound.bind_task(%s, %s)",
                (issued.task["payload_text"], issued.task["signature"]),
            )
            binding = dict(cur.fetchone()[0])

            cur.execute("SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 2")
            rows1 = rows_as_dicts(cur)

            cur.execute(
                "SELECT * FROM taskbound.run(%s)",
                ("SELECT expense_id FROM expenses ORDER BY expense_id LIMIT 1",),
            )
            rows2 = [row[0] for row in cur.fetchall()]

            try:
                cur.execute("SELECT pg_sleep(0) FROM expenses LIMIT 1")
            except Exception as exc:
                denied_error = str(exc).splitlines()[0]

            state = fetch_state(cur)
            visible_receipts = fetch_receipts(cur)
            wrong_fence = inject_wrong_fence_receipt(
                task_id=task_id,
                budget_account=issued.task.get("budget_account", task_id),
                binding_id=str(binding["binding_id"]),
                fence_token=int(binding["fence_token"]),
                actor="agent:travel-expense-analyst",
            )
            cur.execute("SELECT taskbound.unbind_task()")

    raw_receipts = admin_receipts(task_id)
    chain = verify_receipt_chain(raw_receipts)
    expected_decisions = [row["decision"] for row in raw_receipts]
    expected_rows = [int(row["rows_returned"]) for row in raw_receipts]
    state_row = state[0] if state else {}
    passed = (
        len(rows1) == 2
        and len(rows2) == 1
        and "function" in denied_error.lower()
        and len(raw_receipts) == 3
        and expected_decisions == ["allowed", "allowed", "denied"]
        and expected_rows == [2, 1, 0]
        and state_row.get("query_count") == 2
        and state_row.get("returned_rows") == 3
        and chain["all_hashes_match"]
        and chain["chain_ok"]
        and chain["sequence_ok"]
        and chain["required_fields_present"]
        and chain["unique_execution_ids"]
        and chain["tamper_changes_hash"]
        and wrong_fence["passed"]
    )
    return {
        "id": "RF01",
        "name": "receipt_hash_chain_fields_and_wrong_fence_rejection",
        "passed": passed,
        "expected": (
            "two allowed receipts plus one denied receipt form a recomputable "
            "hash chain, required hardened fields are present, tampering changes "
            "the hash, and a wrong-fence append inserts nothing"
        ),
        "actual": "passed" if passed else "failed",
        "task_id": task_id,
        "binding": {
            "binding_id": binding.get("binding_id"),
            "fence_token": binding.get("fence_token"),
            "credential_id": binding.get("credential_id"),
            "advisory_lock_key": binding.get("advisory_lock_key"),
        },
        "state": state_row,
        "receipt_summary_visible_to_agent": visible_receipts,
        "raw_receipts": raw_receipts,
        "chain_verification": chain,
        "wrong_fence_injection": wrong_fence,
        "denied_error": denied_error,
    }


def run_agent_forgery_case(base_url: str, run_id: str, case: dict[str, Any]) -> dict[str, Any]:
    issued = issue_task(base_url, run_id, case["id"].lower(), max_rows=5000, max_queries=20)
    task_id = issued.task["task_id"]
    error = ""
    ok = False
    receipts: list[dict[str, Any]] = []

    with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT taskbound.bind_task(%s, %s)",
                (issued.task["payload_text"], issued.task["signature"]),
            )
            try:
                cur.execute(case["sql"])
                if cur.description is not None:
                    rows_as_dicts(cur)
                ok = True
            except Exception as exc:
                error = str(exc).splitlines()[0]
            finally:
                try:
                    receipts = fetch_receipts(cur)
                finally:
                    try:
                        cur.execute("SELECT taskbound.unbind_task()")
                    except Exception:
                        pass

    raw_receipts = admin_receipts(task_id)
    forged_marker = case["forged_marker"]
    forged_receipts = [
        row
        for row in raw_receipts
        if forged_marker in (row.get("reason") or "")
        or forged_marker in (row.get("budget_account") or "")
        or (row.get("decision") == "allowed" and int(row.get("rows_returned") or 0) == 999)
    ]
    passed = (
        not ok
        and len(raw_receipts) == 1
        and raw_receipts[0]["decision"] == "denied"
        and not forged_receipts
    )
    return {
        "id": case["id"],
        "name": case["name"],
        "passed": passed,
        "expected": "agent direct forgery attempt is denied and inserts no forged receipt",
        "actual": "passed" if passed else "failed",
        "sql": case["sql"],
        "error": error,
        "agent_visible_receipts": receipts,
        "raw_receipts": raw_receipts,
        "forged_receipts": forged_receipts,
    }


def reset_command_fixture(marker: str) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE app_data.expenses SET status = 'submitted' WHERE expense_id = %s",
                (COMMAND_EXPENSE_ID,),
            )
            cur.execute(
                """
                DELETE FROM app_data.approval_events
                WHERE expense_id = %s
                  AND comment = %s
                """,
                (COMMAND_EXPENSE_ID, marker),
            )


def command_fixture_state(marker: str) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM app_data.expenses WHERE expense_id = %s",
                (COMMAND_EXPENSE_ID,),
            )
            status_row = cur.fetchone()
            cur.execute(
                """
                SELECT count(*)::int AS n
                FROM app_data.approval_events
                WHERE expense_id = %s
                  AND event_type = 'finance_compliant'
                  AND comment = %s
                """,
                (COMMAND_EXPENSE_ID, marker),
            )
            event_count = int(cur.fetchone()["n"])
            return {
                "expense_id": COMMAND_EXPENSE_ID,
                "status": status_row["status"] if status_row else None,
                "marker_event_count": event_count,
            }


def issue_finance_command_task(base_url: str, run_id: str, suffix: str) -> IssuedTask:
    return issue_task(
        base_url,
        run_id,
        suffix,
        max_rows=5000,
        max_queries=20,
        task_type="finance_compliance_review",
        delegator="user:fiona",
        actor="agent:finance-compliance-analyst",
    )


def run_allowed_command_receipt_case(base_url: str, run_id: str) -> dict[str, Any]:
    marker = f"receipt-fault-{run_id}-command-allowed"
    reset_command_fixture(marker)
    issued = issue_finance_command_task(base_url, run_id, "command_allowed")
    task_id = issued.task["task_id"]
    binding: dict[str, Any] = {}
    command_result: dict[str, Any] = {}
    visible_receipts: list[dict[str, Any]] = []
    state: list[dict[str, Any]] = []
    fixture_after: dict[str, Any] = {}
    error = ""

    try:
        with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT taskbound.bind_task(%s, %s)",
                    (issued.task["payload_text"], issued.task["signature"]),
                )
                binding = dict(cur.fetchone()[0])
                try:
                    cur.execute(
                        "SELECT taskbound.command(%s, %s::jsonb)",
                        (
                            "finance_approve",
                            json.dumps({"expense_id": COMMAND_EXPENSE_ID, "comment": marker}),
                        ),
                    )
                    command_result = dict(cur.fetchone()[0])
                    state = fetch_state(cur)
                    visible_receipts = fetch_receipts(cur)
                    fixture_after = command_fixture_state(marker)
                except Exception as exc:
                    error = str(exc).splitlines()[0]
                finally:
                    try:
                        cur.execute("SELECT taskbound.unbind_task()")
                    except Exception:
                        pass
    finally:
        reset_command_fixture(marker)

    raw_receipts = admin_receipts(task_id)
    chain = verify_receipt_chain(raw_receipts)
    latest = raw_receipts[0] if raw_receipts else {}
    state_row = state[0] if state else {}
    passed = (
        not error
        and command_result.get("ok") is True
        and command_result.get("command") == "finance_approve"
        and fixture_after.get("status") == "department_approval_requested"
        and fixture_after.get("marker_event_count") == 1
        and len(raw_receipts) == 1
        and latest.get("decision") == "allowed"
        and latest.get("rows_returned") == 0
        and latest.get("unique_rows_added") == 0
        and latest.get("receipt_hash") == command_result.get("receipt_hash")
        and latest.get("receipt_sequence") == command_result.get("receipt_sequence")
        and "controlled command allowed: finance_approve" in str(latest.get("reason") or "")
        and set(latest.get("touched_views") or []) == {"expenses", "approval_events"}
        and state_row.get("query_count") == 0
        and state_row.get("returned_rows") == 0
        and chain["all_hashes_match"]
        and chain["chain_ok"]
        and chain["sequence_ok"]
        and chain["required_fields_present"]
        and chain["unique_execution_ids"]
    )
    return {
        "id": "RF06",
        "name": "controlled_command_allowed_appends_hash_chained_receipt",
        "passed": passed,
        "expected": "allowed controlled command mutates business state and appends one recomputable allowed receipt",
        "actual": "passed" if passed else "failed",
        "task_id": task_id,
        "binding": {
            "binding_id": binding.get("binding_id"),
            "fence_token": binding.get("fence_token"),
            "credential_id": binding.get("credential_id"),
        },
        "command_result": command_result,
        "fixture_after_command": fixture_after,
        "state": state_row,
        "receipt_summary_visible_to_agent": visible_receipts,
        "raw_receipts": raw_receipts,
        "chain_verification": chain,
        "error": error,
    }


def run_allowed_command_rollback_case(base_url: str, run_id: str) -> dict[str, Any]:
    marker = f"receipt-fault-{run_id}-command-rollback"
    reset_command_fixture(marker)
    issued = issue_finance_command_task(base_url, run_id, "command_rollback")
    task_id = issued.task["task_id"]
    binding: dict[str, Any] = {}
    command_result: dict[str, Any] = {}
    visible_receipts: list[dict[str, Any]] = []
    state: list[dict[str, Any]] = []
    fixture_after: dict[str, Any] = {}
    error = ""

    try:
        with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT taskbound.bind_task(%s, %s)",
                    (issued.task["payload_text"], issued.task["signature"]),
                )
                binding = dict(cur.fetchone()[0])
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT taskbound.command(%s, %s::jsonb)",
                    (
                        "finance_approve",
                        json.dumps({"expense_id": COMMAND_EXPENSE_ID, "comment": marker}),
                    ),
                )
                command_result = dict(cur.fetchone()[0])
                conn.rollback()
            conn.autocommit = True
            fixture_after = command_fixture_state(marker)
            with conn.cursor() as cur:
                state = fetch_state(cur)
                visible_receipts = fetch_receipts(cur)
                try:
                    cur.execute("SELECT taskbound.unbind_task()")
                except Exception:
                    pass
    except Exception as exc:
        error = str(exc).splitlines()[0]
    finally:
        reset_command_fixture(marker)

    raw_receipts = admin_receipts(task_id)
    chain = verify_receipt_chain(raw_receipts)
    latest = raw_receipts[0] if raw_receipts else {}
    state_row = state[0] if state else {}
    passed = (
        not error
        and command_result.get("ok") is True
        and command_result.get("command") == "finance_approve"
        and fixture_after.get("status") == "department_approval_requested"
        and fixture_after.get("marker_event_count") == 1
        and len(raw_receipts) == 1
        and latest.get("decision") == "allowed"
        and latest.get("rows_returned") == 0
        and latest.get("unique_rows_added") == 0
        and latest.get("receipt_hash") == command_result.get("receipt_hash")
        and latest.get("receipt_sequence") == command_result.get("receipt_sequence")
        and "controlled command allowed: finance_approve" in str(latest.get("reason") or "")
        and set(latest.get("touched_views") or []) == {"expenses", "approval_events"}
        and state_row.get("query_count") == 0
        and state_row.get("returned_rows") == 0
        and chain["all_hashes_match"]
        and chain["chain_ok"]
        and chain["sequence_ok"]
        and chain["required_fields_present"]
        and chain["unique_execution_ids"]
    )
    return {
        "id": "RF07",
        "name": "controlled_command_allowed_survives_client_rollback",
        "passed": passed,
        "expected": "allowed controlled command business mutation and receipt persist after caller ROLLBACK",
        "actual": "passed" if passed else "failed",
        "task_id": task_id,
        "binding": {
            "binding_id": binding.get("binding_id"),
            "fence_token": binding.get("fence_token"),
            "credential_id": binding.get("credential_id"),
        },
        "command_result": command_result,
        "fixture_after_command": fixture_after,
        "state": state_row,
        "receipt_summary_visible_to_agent": visible_receipts,
        "raw_receipts": raw_receipts,
        "chain_verification": chain,
        "error": error,
    }


def run_command_operation_denial_case(base_url: str, run_id: str) -> dict[str, Any]:
    marker = f"receipt-fault-{run_id}-command-op-denied"
    reset_command_fixture(marker)
    issued = issue_finance_command_task(base_url, run_id, "command_op_denied")
    task_id = issued.task["task_id"]
    payload = dict(issued.task["payload"])
    payload["operations"] = ["SELECT"]
    payload_text = canonical(payload)
    signature = sign(payload_text)
    binding: dict[str, Any] = {}
    command_result: dict[str, Any] = {}
    error = ""
    ok = False
    fixture_after: dict[str, Any] = {}

    try:
        with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
                binding = dict(cur.fetchone()[0])
                try:
                    cur.execute(
                        "SELECT taskbound.command(%s, %s::jsonb)",
                        (
                            "finance_approve",
                            json.dumps({"expense_id": COMMAND_EXPENSE_ID, "comment": marker}),
                        ),
                    )
                    command_result = dict(cur.fetchone()[0])
                    ok = bool(command_result.get("ok"))
                    if not ok:
                        error = str(command_result.get("error") or "")
                except Exception as exc:
                    error = str(exc).splitlines()[0]
                finally:
                    fixture_after = command_fixture_state(marker)
                    try:
                        cur.execute("SELECT taskbound.unbind_task()")
                    except Exception:
                        pass
    finally:
        reset_command_fixture(marker)

    raw_receipts = admin_receipts(task_id)
    chain = verify_receipt_chain(raw_receipts)
    latest = raw_receipts[0] if raw_receipts else {}
    passed = (
        not ok
        and error == "SessionBoundDB denied command"
        and fixture_after.get("status") == "submitted"
        and fixture_after.get("marker_event_count") == 0
        and len(raw_receipts) == 1
        and latest.get("decision") == "denied"
        and latest.get("reason") == "controlled command denied"
        and set(latest.get("touched_views") or []) == {"expenses", "approval_events"}
        and chain["all_hashes_match"]
        and chain["chain_ok"]
        and chain["sequence_ok"]
        and chain["required_fields_present"]
        and chain["unique_execution_ids"]
    )
    return {
        "id": "RF08",
        "name": "controlled_command_requires_operation_claim_and_receipts_denial",
        "passed": passed,
        "expected": "token without CONTROLLED_COMMAND binds for SELECT but command returns sanitized denial with one receipt",
        "actual": "passed" if passed else "failed",
        "task_id": task_id,
        "binding": {
            "binding_id": binding.get("binding_id"),
            "fence_token": binding.get("fence_token"),
            "credential_id": binding.get("credential_id"),
        },
        "command_result": command_result,
        "error": error,
        "fixture_after_command": fixture_after,
        "raw_receipts": raw_receipts,
        "chain_verification": chain,
    }


def run_eval(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    records = [run_chain_and_fault_case(base_url, run_id)]
    records.append(run_allowed_command_receipt_case(base_url, run_id))
    records.append(run_allowed_command_rollback_case(base_url, run_id))
    records.append(run_command_operation_denial_case(base_url, run_id))
    records.extend(run_agent_forgery_case(base_url, run_id, case) for case in FORGERY_CASES)
    run = {
        "all_receipt_fault_checks_passed": all(record["passed"] for record in records),
        "base_url": base_url,
        "case_count": len(records),
        "commit": git_commit(),
        "failed": sum(1 for record in records if not record["passed"]),
        "passed": sum(1 for record in records if record["passed"]),
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    return {"run": run, "records": records}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("TDSC_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    payload = run_eval(args.base_url.rstrip("/"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"receipt_fault_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2))
    if not payload["run"]["all_receipt_fault_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
