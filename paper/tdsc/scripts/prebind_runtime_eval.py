#!/usr/bin/env python3
"""Validate that runtime credentials cannot run arbitrary SQL before binding.

This covers the static-review M2 pre-bind gap: a credential inheriting
``agent_runtime`` must be unable to execute ordinary SQL or utility commands
before ``taskbound.bind_task(...)`` installs a trusted task binding.  The public
binding entrypoint itself must remain callable so legitimate sessions can start.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "paper" / "tdsc" / "raw_results"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sessionbound_guard_hook_eval import (  # noqa: E402
    classify_psql,
    git_commit,
    post_json,
    run_psql,
    sql_literal,
    wait_for_api,
)


PREBIND_BLOCK_CASES: list[dict[str, str]] = [
    {
        "id": "PB01",
        "name": "ordinary_select_without_binding",
        "sql": "SELECT 1;",
        "expected_reason": "no trusted task binding is active",
    },
    {
        "id": "PB02",
        "name": "safe_view_select_without_binding",
        "sql": "SELECT expense_id FROM taskbound.expenses LIMIT 1;",
        "expected_reason": "no trusted task binding is active",
    },
    {
        "id": "PB03",
        "name": "side_effect_function_without_binding",
        "sql": "SELECT pg_notify('prebind_runtime_eval', 'payload');",
        "expected_reason": "not allowed in task SQL",
    },
    {
        "id": "PB04",
        "name": "temp_table_utility_without_binding",
        "sql": "CREATE TEMP TABLE tb_prebind_runtime_eval(x int);",
        "expected_reason": "utility statement is not allowed in task SQL",
    },
    {
        "id": "PB05",
        "name": "prepared_statement_without_binding",
        "sql": "PREPARE q AS SELECT 1;",
        "expected_reason": "no trusted task binding is active",
    },
    {
        "id": "PB06",
        "name": "private_runtime_helper_without_binding",
        "sql": "SELECT taskbound.require_payload();",
        "expected_reason": "no trusted task binding is active",
    },
]


def issue_runtime_credential_and_task(base_url: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    credential = post_json(
        base_url,
        "/credentials",
        {
            "agent_id": f"prebind-eval-{run_id}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    task = post_json(
        base_url,
        "/tasks",
        {
            "task_id": f"task_prebind_runtime_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 10,
        },
    )
    return credential, task


def run_block_case(case: dict[str, str], credential: dict[str, Any]) -> dict[str, Any]:
    result = run_psql(case["sql"] + "\n", user=credential["db_user"], password=credential["db_password"])
    output = result.get("stdout", "") + result.get("stderr", "")
    return {
        **case,
        "expected": "Blocked",
        "actual": classify_psql(result),
        "passed": result["returncode"] != 0 and case["expected_reason"] in output,
        "reason_matched": case["expected_reason"] in output,
        "psql": result,
    }


def run_bind_entrypoint_case(credential: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    script = (
        f"SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});\n"
        "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 1;\n"
        "SELECT taskbound.unbind_task();\n"
    )
    result = run_psql(script, user=credential["db_user"], password=credential["db_password"])
    output = result.get("stdout", "") + result.get("stderr", "")
    return {
        "id": "PB07",
        "name": "public_bind_entrypoint_then_bound_select",
        "expected": "Allowed",
        "actual": classify_psql(result),
        "passed": result["returncode"] == 0 and "expense_id" in output,
        "reason_matched": True,
        "psql": result,
    }


def evaluate(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    credential, task = issue_runtime_credential_and_task(base_url, run_id)

    records: list[dict[str, Any]] = []
    if not credential.get("db_user") or not credential.get("db_password") or not task.get("payload_text"):
        records.append(
            {
                "id": "SETUP",
                "name": "credential_and_task_issuance",
                "expected": "Allowed",
                "actual": "Blocked",
                "passed": False,
                "reason_matched": False,
                "credential": credential,
                "task": task,
            }
        )
    else:
        for case in PREBIND_BLOCK_CASES:
            records.append(run_block_case(case, credential))
        records.append(run_bind_entrypoint_case(credential, task))

    passed = sum(1 for record in records if record["passed"])
    failed = sum(1 for record in records if not record["passed"])
    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "commit": git_commit(),
            "base_url": base_url,
            "case_count": len(records),
            "passed": passed,
            "failed": failed,
            "all_prebind_runtime_checks_passed": failed == 0,
            "credential_id": credential.get("credential_id"),
            "task_id": (task.get("payload") or {}).get("task_id"),
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    payload = evaluate(args.base_url.rstrip("/"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"prebind_runtime_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
