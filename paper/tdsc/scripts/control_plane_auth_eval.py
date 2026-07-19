#!/usr/bin/env python3
"""Validate control-plane authentication for task and credential issuance."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "paper" / "tdsc" / "raw_results"
CONTROL_PLANE_KEY = os.environ.get(
    "TASKBOUND_CONTROL_PLANE_KEY",
    "tdsc-demo-control-plane-key",
)


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def wait_for_api(base_url: str) -> None:
    for _ in range(30):
        try:
            urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"API did not become available: {base_url}")


def request_json(
    base_url: str,
    path: str,
    *,
    method: str,
    body: dict[str, Any] | None = None,
    key: str | None = CONTROL_PLANE_KEY,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if key is not None:
        headers["X-TaskBound-Control-Plane-Key"] = key
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"http_status": response.status, **payload}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"http_status": exc.code, **payload}


def evaluate(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    actor = "agent:travel-expense-analyst"

    unauth_credential = request_json(
        base_url,
        "/credentials",
        method="POST",
        body={"agent_id": f"auth-no-key-{run_id}", "actor": actor, "ttl_minutes": 15},
        key=None,
    )
    wrong_credential = request_json(
        base_url,
        "/credentials",
        method="POST",
        body={"agent_id": f"auth-wrong-key-{run_id}", "actor": actor, "ttl_minutes": 15},
        key="wrong-control-plane-key",
    )
    credential = request_json(
        base_url,
        "/credentials",
        method="POST",
        body={"agent_id": f"auth-ok-{run_id}", "actor": actor, "ttl_minutes": 15},
    )
    unauth_task = request_json(
        base_url,
        "/tasks",
        method="POST",
        body={
            "task_id": f"task_auth_no_key_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
        },
        key=None,
    )
    task = request_json(
        base_url,
        "/tasks",
        method="POST",
        body={
            "task_id": f"task_auth_ok_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_queries": 5,
            "max_rows": 5,
        },
    )
    runtime_switch = request_json(
        base_url,
        "/tasks",
        method="POST",
        body={
            "task_id": f"task_auth_runtime_switch_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "runtime_options": {"receipts_enabled": False},
        },
    )
    unauth_admin = request_json(
        base_url,
        "/admin/task-templates",
        method="GET",
        key=None,
    )
    admin = request_json(
        base_url,
        "/admin/task-templates",
        method="GET",
    )

    records = [
        {
            "name": "credentials_without_key_rejected",
            "passed": unauth_credential.get("http_status") == 401,
            "response": unauth_credential,
        },
        {
            "name": "credentials_wrong_key_rejected",
            "passed": wrong_credential.get("http_status") == 401,
            "response": wrong_credential,
        },
        {
            "name": "credentials_with_key_allowed",
            "passed": credential.get("http_status") == 200 and bool(credential.get("credential_id")),
            "response": {k: credential.get(k) for k in ("http_status", "credential_id", "db_user", "actor")},
        },
        {
            "name": "tasks_without_key_rejected",
            "passed": unauth_task.get("http_status") == 401,
            "response": unauth_task,
        },
        {
            "name": "tasks_with_key_allowed",
            "passed": task.get("http_status") == 200 and bool(task.get("payload_text")),
            "response": {k: task.get(k) for k in ("http_status", "signature")},
        },
        {
            "name": "runtime_security_switch_rejected_after_auth",
            "passed": (
                runtime_switch.get("http_status") == 400
                and "runtime_options.receipts_enabled" in str(runtime_switch.get("detail") or "")
            ),
            "response": runtime_switch,
        },
        {
            "name": "admin_without_key_rejected",
            "passed": unauth_admin.get("http_status") == 401,
            "response": unauth_admin,
        },
        {
            "name": "admin_with_key_allowed",
            "passed": admin.get("http_status") == 200 and bool(admin.get("templates")),
            "response": {"http_status": admin.get("http_status"), "template_count": len(admin.get("templates") or {})},
        },
    ]
    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "commit": git_commit(),
            "base_url": base_url,
            "case_count": len(records),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
            "all_control_plane_auth_checks_passed": all(record["passed"] for record in records),
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("TDSC_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    payload = evaluate(args.base_url.rstrip("/"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"control_plane_auth_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
