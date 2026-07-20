#!/usr/bin/env python3
"""Evaluate the PostgreSQL sessionbound_guard parser/analyzer hook path."""

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
CONTROL_PLANE_KEY = os.environ.get(
    "TASKBOUND_CONTROL_PLANE_KEY",
    "tdsc-demo-control-plane-key",
)


AGENT_CASES: list[dict[str, str]] = [
    {
        "id": "HG01",
        "name": "allowed_safe_view_select_through_runtime",
        "expected": "Allowed",
        "sql": "SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2",
        "expected_reason": "",
    },
    {
        "id": "HG02",
        "name": "hook_blocks_union",
        "expected": "Blocked",
        "sql": "SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees",
        "expected_reason": "UNION, INTERSECT, and EXCEPT are not allowed",
    },
    {
        "id": "HG03",
        "name": "hook_blocks_catalog_view",
        "expected": "Blocked",
        "sql": "SELECT schemaname, tablename FROM pg_tables",
        "expected_reason": "catalog access is not allowed",
    },
    {
        "id": "HG04",
        "name": "hook_blocks_recursive_cte",
        "expected": "Blocked",
        "sql": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 2) SELECT * FROM r",
        "expected_reason": "recursive CTEs are not allowed",
    },
    {
        "id": "HG05",
        "name": "runtime_helper_name_blocked",
        "expected": "Blocked",
        "sql": "SELECT claim(ARRAY['tenant_id']) FROM expenses LIMIT 1",
        "expected_reason": "direct access to taskbound runtime helper functions is not allowed",
    },
    {
        "id": "HG06",
        "name": "bound_bare_safe_view_select_native_accounted",
        "mode": "bare_select",
        "expected": "Allowed",
        "sql": "SELECT expense_id, amount FROM taskbound.expenses LIMIT 1",
        "expected_reason": "",
    },
    {
        "id": "HG07",
        "name": "native_prepared_execute",
        "mode": "native_script",
        "expected": "Allowed",
        "sql": "PREPARE q AS SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2;\nEXECUTE q;",
        "expected_reason": "",
    },
    {
        "id": "HG08",
        "name": "native_cursor_fetch_blocked",
        "mode": "native_script",
        "expected": "Blocked",
        "sql": "BEGIN;\nDECLARE c CURSOR FOR SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 3;\nFETCH 2 FROM c;\nCLOSE c;\nCOMMIT;",
        "expected_reason": "cursors are not available under task binding",
    },
    {
        "id": "HG09",
        "name": "native_copy_select",
        "mode": "native_script",
        "expected": "Allowed",
        "sql": "COPY (SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2) TO STDOUT WITH CSV HEADER;",
        "expected_reason": "",
    },
    {
        "id": "HG10",
        "name": "native_explain_select_blocked",
        "mode": "native_script",
        "expected": "Blocked",
        "sql": "EXPLAIN SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2;",
        "expected_reason": "EXPLAIN output is not available under task binding",
    },
    {
        "id": "HG11",
        "name": "native_explain_analyze_blocked",
        "mode": "native_script",
        "expected": "Blocked",
        "sql": "EXPLAIN ANALYZE SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2;",
        "expected_reason": "EXPLAIN output is not available under task binding",
    },
    {
        "id": "HG12",
        "name": "native_holdable_cursor_blocked",
        "mode": "native_script",
        "expected": "Blocked",
        "sql": "BEGIN;\nDECLARE c CURSOR WITH HOLD FOR SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2;\nCOMMIT;",
        "expected_reason": "cursors are not available under task binding",
    },
]


HOOK_ONLY_CASES: list[dict[str, str]] = [
    {
        "id": "HH01",
        "name": "hook_blocks_non_select",
        "allowed_views": "all",
        "expected": "Blocked",
        "sql": "DELETE FROM expenses WHERE expense_id = 'exp_001'",
        "expected_reason": "only SELECT statements are allowed",
    },
    {
        "id": "HH02",
        "name": "hook_blocks_raw_schema",
        "allowed_views": "all",
        "expected": "Blocked",
        "sql": "SELECT * FROM app_data.expenses",
        "expected_reason": "raw application schema access is not allowed",
    },
    {
        "id": "HH03",
        "name": "hook_blocks_payload_aggregation",
        "allowed_views": "all",
        "expected": "Blocked",
        "sql": "SELECT array_agg(expense_id) FROM expenses",
        "expected_reason": "aggregate release requires an approved aggregate template",
    },
    {
        "id": "HH04",
        "name": "hook_blocks_unapproved_safe_view_oid",
        "allowed_views": "expenses_only",
        "expected": "Blocked",
        "sql": "SELECT department_id FROM departments",
        "expected_reason": "relation is outside the approved safe-view registry",
    },
    {
        "id": "HH05",
        "name": "hook_blocks_group_by_sensitive_entity",
        "allowed_views": "all",
        "expected": "Blocked",
        "sql": "SELECT department_id, employee_id, count(*) FROM expenses GROUP BY department_id, employee_id",
        "expected_reason": "minimum group-size policy denies grouping by sensitive entity identifiers",
    },
    {
        "id": "HH06",
        "name": "hook_blocks_having_small_group_probe",
        "allowed_views": "all",
        "expected": "Blocked",
        "sql": "SELECT department_id, count(*) FROM expenses GROUP BY department_id HAVING count(*) < 5",
        "expected_reason": "minimum group-size policy denies HAVING predicates",
    },
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
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if path.startswith(("/credentials", "/tasks", "/todos", "/admin/")):
        headers["X-TaskBound-Control-Plane-Key"] = CONTROL_PLANE_KEY
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
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


def run_psql(sql_script: str, user: str = "postgres", password: str = "postgres") -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "env",
        f"PGPASSWORD={password}",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
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
        "output_tail": "\n".join(output.splitlines()[-12:]),
    }


def classify_psql(result: dict[str, Any]) -> str:
    return "Allowed" if result["returncode"] == 0 else "Blocked"


def issue_task(
    base_url: str,
    run_id: str,
    *,
    task_suffix: str = "",
    credential_id: str | None = None,
    task_type: str = "monthly_travel_expense_review",
    delegator: str = "user:alice",
    actor: str = "agent:travel-expense-analyst",
) -> tuple[dict[str, Any], dict[str, Any]]:
    credential = post_json(
        base_url,
        "/credentials",
        {
            "agent_id": f"hook-eval-{run_id}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    ) if credential_id is None else {"credential_id": credential_id}
    task = post_json(
        base_url,
        "/tasks",
        {
            "task_id": f"task_hook_eval_{run_id}{task_suffix}",
            "task_type": task_type,
            "delegator": delegator,
            "actor": actor,
            "credential_id": credential_id or credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 50,
        },
    )
    return credential, task


def run_prepared_rebind_case(base_url: str, run_id: str, credential: dict[str, Any]) -> dict[str, Any]:
    shared_actor = credential.get("actor") or "agent:travel-expense-analyst"
    _, finance_task = issue_task(
        base_url,
        run_id,
        task_suffix="_prepared_finance",
        credential_id=credential["credential_id"],
        task_type="finance_compliance_review",
        delegator="user:fiona",
        actor=shared_actor,
    )
    _, payment_task = issue_task(
        base_url,
        run_id,
        task_suffix="_prepared_payment",
        credential_id=credential["credential_id"],
        task_type="payment_readiness_audit",
        delegator="user:fiona",
        actor=shared_actor,
    )
    sql_script = (
        f"SELECT taskbound.bind_task({sql_literal(finance_task['payload_text'])}, {sql_literal(finance_task['signature'])});\n"
        "PREPARE crossbind AS "
        "SELECT e.expense_id, emp.employee_name "
        "FROM expenses e JOIN employees emp USING (employee_id) "
        "ORDER BY e.expense_id LIMIT 1;\n"
        "SELECT taskbound.unbind_task();\n"
        f"SELECT taskbound.bind_task({sql_literal(payment_task['payload_text'])}, {sql_literal(payment_task['signature'])});\n"
        "EXECUTE crossbind;\n"
    )
    result = run_psql(sql_script, user=credential["db_user"], password=credential["db_password"])
    actual = classify_psql(result)
    reason_ok = (
        "prepared statement is not available" in result["output_tail"]
        or "prepared statement" in result["output_tail"]
        or "does not exist" in result["output_tail"]
    )
    return {
        "id": "HG13",
        "name": "prepared_plan_dropped_across_rebind",
        "mode": "native_script",
        "expected": "Blocked",
        "expected_reason": "prepared statement is not available after unbind/rebind",
        "path": "agent credential native SQL surface",
        "actual": actual,
        "passed": actual == "Blocked" and reason_ok,
        "reason_matched": reason_ok,
        "psql": result,
    }


def run_agent_case(case: dict[str, str], credential: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    if case.get("mode") == "bare_select":
        sql_script = (
            f"SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});\n"
            f"{case['sql']};\n"
        )
        path = "agent credential direct native safe-view SELECT"
    elif case.get("mode") == "native_script":
        sql_script = (
            f"SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});\n"
            f"{case['sql']}\n"
        )
        path = "agent credential native SQL surface"
    else:
        sql_script = (
            f"SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});\n"
            f"SELECT * FROM taskbound.run({sql_literal(case['sql'])});\n"
        )
        path = "agent credential direct database call through taskbound.run"
    result = run_psql(sql_script, user=credential["db_user"], password=credential["db_password"])
    actual = classify_psql(result)
    reason_ok = not case["expected_reason"] or case["expected_reason"] in result["output_tail"]
    return {
        **case,
        "path": path,
        "actual": actual,
        "passed": actual == case["expected"] and reason_ok,
        "reason_matched": reason_ok,
        "psql": result,
    }


def hook_setup_sql(allowed_views: str) -> str:
    if allowed_views == "expenses_only":
        predicate = "view_name = 'expenses'"
    else:
        predicate = "view_name = ANY(ARRAY['expenses','employees','departments','approval_events','ledger_entries'])"
    return f"""
SET search_path = taskbound, pg_temp;
SELECT set_config(
  'sessionbound_guard.allowed_view_oids',
  (
    SELECT string_agg((database_object::regclass)::oid::text, ',' ORDER BY view_name)
    FROM taskbound.safe_view_registry
    WHERE {predicate}
  ),
  false
);
SELECT set_config('sessionbound_guard.task_bound', 'on', false);
"""


def run_hook_only_case(case: dict[str, str]) -> dict[str, Any]:
    sql_script = hook_setup_sql(case["allowed_views"])
    sql_script += f"SELECT public.sessionbound_guard_check({sql_literal(case['sql'])});\n"
    result = run_psql(sql_script)
    actual = classify_psql(result)
    reason_ok = case["expected_reason"] in result["output_tail"]
    return {
        **case,
        "path": "superuser trusted-GUC hook check without API preflight",
        "actual": actual,
        "passed": actual == case["expected"] and reason_ok,
        "reason_matched": reason_ok,
        "psql": result,
    }


def run_guc_protection_case() -> dict[str, Any]:
    sql_script = """
SET ROLE agent_app;
SET sessionbound_guard.enabled = on;
"""
    result = run_psql(sql_script)
    actual = classify_psql(result)
    reason_ok = "permission denied to set parameter" in result["output_tail"]
    return {
        "id": "HGUC",
        "name": "trusted_guc_requires_superuser",
        "expected": "Blocked",
        "expected_reason": "permission denied to set parameter",
        "path": "non-superuser GUC tamper attempt",
        "actual": actual,
        "passed": actual == "Blocked" and reason_ok,
        "reason_matched": reason_ok,
        "psql": result,
    }


def run_eval(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = str(int(time.time()))
    credential, task = issue_task(base_url, run_id)
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(f"failed to create credential/task: {credential} {task}")

    records = [run_guc_protection_case()]
    records.extend(run_agent_case(case, credential, task) for case in AGENT_CASES)
    records.append(run_prepared_rebind_case(base_url, run_id, credential))
    records.extend(run_hook_only_case(case) for case in HOOK_ONLY_CASES)

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
    output_path = output_dir / f"sessionbound_guard_hook_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
