#!/usr/bin/env python3
"""Validate rollback-surviving allowed and denial audit receipts."""

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
STATE_MARKER = "ROLLBACK_AUDIT_STATE="
RECEIPT_MARKER = "ROLLBACK_AUDIT_RECEIPT="


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


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_psql(sql_script: str, user: str, password: str) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "env",
        f"PGPASSWORD={password}",
        "psql",
        "-X",
        "-q",
        "-U",
        user,
        "-d",
        "travel",
    ]
    proc = subprocess.run(
        command,
        cwd=REPO_ROOT,
        input=sql_script,
        text=True,
        capture_output=True,
    )
    output = (proc.stdout + proc.stderr).strip()
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "output_tail": "\n".join(output.splitlines()[-16:]),
    }


def _parse_int(value: str) -> int | None:
    return int(value) if value else None


def extract_snapshot(stdout: str) -> dict[str, Any]:
    state: dict[str, Any] | None = None
    receipts: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if line.startswith(STATE_MARKER):
            parts = line[len(STATE_MARKER) :].split("\t")
            state = {
                "task_id": parts[0],
                "budget_account": parts[1],
                "query_count": _parse_int(parts[2]),
                "returned_rows": _parse_int(parts[3]),
                "unique_expense_rows": _parse_int(parts[4]),
                "revoked": parts[5] == "true",
            }
        elif line.startswith(RECEIPT_MARKER):
            parts = line[len(RECEIPT_MARKER) :].split("\t", 4)
            receipts.append(
                {
                    "decision": parts[0],
                    "rows_returned": _parse_int(parts[1]),
                    "unique_rows_added": _parse_int(parts[2]),
                    "remaining_unique_row_budget": _parse_int(parts[3]),
                    "reason": parts[4] if len(parts) > 4 else "",
                }
            )
    if state is None:
        raise RuntimeError(f"missing {STATE_MARKER} marker in psql output")
    return {"state": state, "receipts": receipts}


def issue_task(base_url: str, run_id: str, suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    credential = post_json(
        base_url,
        "/credentials",
        {
            "agent_id": f"rollback-audit-{run_id}-{suffix}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    task = post_json(
        base_url,
        "/tasks",
        {
            "task_id": f"task_rollback_audit_{run_id}_{suffix}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 50,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(f"failed to create credential/task: {credential} {task}")
    return credential, task


def receipt_snapshot_sql() -> str:
    return f"""
SELECT '{STATE_MARKER}' ||
       task_id || chr(9) ||
       budget_account || chr(9) ||
       query_count::text || chr(9) ||
       returned_rows::text || chr(9) ||
       unique_expense_rows::text || chr(9) ||
       revoked::text
FROM taskbound.inspect_task_state();

SELECT '{RECEIPT_MARKER}' ||
       decision || chr(9) ||
       rows_returned::text || chr(9) ||
       unique_rows_added::text || chr(9) ||
       COALESCE(remaining_unique_row_budget::text, '') || chr(9) ||
       COALESCE(replace(replace(reason, chr(10), ' '), chr(9), ' '), '')
FROM taskbound.receipts()
ORDER BY created_at, receipt_id;
"""


def run_allowed_case(credential: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    sql_script = f"""
\\set ON_ERROR_STOP on
\\pset format unaligned
\\pset tuples_only on
SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});
BEGIN;
SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2;
ROLLBACK;
{receipt_snapshot_sql()}
"""
    result = run_psql(sql_script, credential["db_user"], credential["db_password"])
    snapshot = extract_snapshot(result["stdout"]) if result["returncode"] == 0 else {}
    state = snapshot.get("state") or {}
    receipts = snapshot.get("receipts") or []
    allowed_receipt = any(
        receipt.get("decision") == "allowed" and receipt.get("rows_returned") == 2
        for receipt in receipts
    )
    state_ok = (
        state.get("query_count", 0) >= 1
        and state.get("returned_rows", 0) >= 2
        and state.get("unique_expense_rows", 0) >= 2
    )
    return {
        "id": "RA01",
        "name": "allowed_receipt_and_accounting_survive_rollback",
        "expected": "Allowed receipt and accounting persist after ROLLBACK",
        "actual": "passed" if result["returncode"] == 0 and state_ok and allowed_receipt else "failed",
        "passed": result["returncode"] == 0 and state_ok and allowed_receipt,
        "snapshot": snapshot,
        "psql": result,
    }


def run_denied_case(credential: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    sql_script = f"""
\\set ON_ERROR_STOP off
\\pset format unaligned
\\pset tuples_only on
SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});
BEGIN;
SELECT * FROM app_data.expenses LIMIT 1;
ROLLBACK;
SELECT taskbound.fail_receipt('SELECT * FROM app_data.expenses LIMIT 1', 'raw application schema access is not allowed');
{receipt_snapshot_sql()}
"""
    result = run_psql(sql_script, credential["db_user"], credential["db_password"])
    snapshot = extract_snapshot(result["stdout"]) if result["returncode"] == 0 else {}
    receipts = snapshot.get("receipts") or []
    denial_receipt = any(
        receipt.get("decision") == "denied"
        and "raw application schema access is not allowed" in (receipt.get("reason") or "")
        for receipt in receipts
    )
    error_seen = (
        "SessionBound guard denied query" in result["stderr"]
        or "SessionBoundDB denied query" in result["stderr"]
    )
    return {
        "id": "RA02",
        "name": "denial_receipt_survives_error_and_rollback",
        "expected": "Denied raw-schema attempt emits durable receipt after ROLLBACK",
        "actual": "passed" if result["returncode"] == 0 and error_seen and denial_receipt else "failed",
        "passed": result["returncode"] == 0 and error_seen and denial_receipt,
        "snapshot": snapshot,
        "psql": result,
    }


def run_eval(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = str(int(time.time()))
    allowed_credential, allowed_task = issue_task(base_url, run_id, "allowed")
    denied_credential, denied_task = issue_task(base_url, run_id, "denied")
    records = [
        run_allowed_case(allowed_credential, allowed_task),
        run_denied_case(denied_credential, denied_task),
    ]
    return {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "base_url": base_url,
            "case_count": len(records),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    payload = run_eval(args.base_url)
    output_path = output_dir / f"rollback_audit_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
