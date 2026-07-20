#!/usr/bin/env python3
"""Validate that task-allowed safe views enforce the full token row scope.

The static review identified a concrete scope-completeness failure: some
task-allowed safe views previously filtered only by tenant, so a task scoped to
one month/department could enumerate dimension, workflow, or ledger rows from
outside the task.  This evaluator issues real short-lived credentials and
signed task tokens, binds them in PostgreSQL, queries every task-allowed safe
view, and checks returned rows against raw-table provenance.

For each (scenario, view), the oracle verifies:

* the raw seed has at least one out-of-scope candidate for that view, so the
  test is non-vacuous;
* the safe-view row count equals the raw in-scope provenance count; and
* every returned row maps back to tenant/month/department provenance inside
  the signed token scope.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql as psql
from psycopg.rows import dict_row


REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "paper" / "tdsc" / "raw_results"
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:15432/travel",
)
AGENT_DB_HOST = os.environ.get("TDSC_AGENT_DB_HOST", "localhost")
AGENT_DB_PORT = int(os.environ.get("TDSC_AGENT_DB_PORT", "15432"))
CONTROL_PLANE_KEY = os.environ.get(
    "TASKBOUND_CONTROL_PLANE_KEY",
    "tdsc-demo-control-plane-key",
)

VIEW_ORDER_KEY = {
    "expenses": "expense_id",
    "employees": "employee_id",
    "departments": "department_id",
    "approval_events": "event_id",
    "ledger_entries": "ledger_id",
}

VIEW_KEY = {
    "expenses": "expense_id",
    "employees": "employee_id",
    "departments": "department_id",
    "approval_events": "event_id",
    "ledger_entries": "ledger_id",
}

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "SC01",
        "name": "monthly_month_scope",
        "task_type": "monthly_travel_expense_review",
        "delegator": "user:alice",
        "actor": "agent:travel-expense-analyst",
        "scope": {"expense_month": "2026-06"},
    },
    {
        "id": "SC02",
        "name": "monthly_month_department_scope",
        "task_type": "monthly_travel_expense_review",
        "delegator": "user:alice",
        "actor": "agent:travel-expense-analyst",
        "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
    },
    {
        "id": "SC03",
        "name": "finance_workflow_month_department_scope",
        "task_type": "finance_compliance_review",
        "delegator": "user:fiona",
        "actor": "agent:finance-compliance-analyst",
        "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
    },
    {
        "id": "SC04",
        "name": "payment_ledger_month_department_scope",
        "task_type": "payment_readiness_audit",
        "delegator": "user:fiona",
        "actor": "agent:payment-control-analyst",
        "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
    },
]


@dataclass
class IssuedTask:
    credential: dict[str, Any]
    task: dict[str, Any]


def json_default(value: Any) -> str:
    if isinstance(value, (datetime, Decimal, UUID)):
        return str(value)
    raise TypeError(f"{value!r} is not JSON serializable")


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


def post_json_retry(
    base_url: str,
    path: str,
    body: dict[str, Any],
    *,
    attempts: int = 6,
    delay: float = 1.0,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        result = post_json(base_url, path, body)
        if "http_error" not in result and "error" not in result:
            return result
        if result.get("http_error") not in {500, 502, 503, 504} and "error" not in result:
            return result
        if attempt < attempts:
            time.sleep(delay)
    return result


def wait_for_api(base_url: str) -> None:
    for _ in range(30):
        try:
            urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {base_url}")


def issue_task(base_url: str, run_id: str, scenario: dict[str, Any]) -> IssuedTask:
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"scope-{run_id}-{scenario['id'].lower()}",
            "actor": scenario["actor"],
            "ttl_minutes": 30,
        },
    )
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": f"task_scope_{run_id}_{scenario['id'].lower()}",
            "task_type": scenario["task_type"],
            "delegator": scenario["delegator"],
            "actor": scenario["actor"],
            "credential_id": credential.get("credential_id"),
            "scope": scenario["scope"],
            "max_queries": 40,
            "max_rows": 5000,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(
            "failed to create credential/task for "
            f"{scenario['id']}: credential={credential} task={task}"
        )
    return IssuedTask(credential=credential, task=task)


def credential_dsn(credential: dict[str, Any]) -> str:
    return (
        f"postgresql://{credential['db_user']}:{credential['db_password']}"
        f"@{AGENT_DB_HOST}:{AGENT_DB_PORT}/{credential.get('db_name', 'travel')}"
    )


def fetch_safe_view_rows(cur: psycopg.Cursor, view: str) -> list[dict[str, Any]]:
    order_key = VIEW_ORDER_KEY[view]
    query = psql.SQL("SELECT * FROM taskbound.{} ORDER BY {} LIMIT 50000").format(
        psql.Identifier(view),
        psql.Identifier(order_key),
    )
    cur.execute(query)
    return list(cur.fetchall())


def raw_in_scope_count(cur: psycopg.Cursor, view: str, tenant_id: str, scope: dict[str, Any]) -> int:
    month = scope["expense_month"]
    department = scope.get("department_id")
    if view == "expenses":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.expenses e
            WHERE e.tenant_id = %s
              AND e.expense_month = %s
              AND (%s::text IS NULL OR e.department_id = %s)
            """,
            (tenant_id, month, department, department),
        )
    elif view == "employees":
        cur.execute(
            """
            SELECT count(DISTINCT emp.employee_id)::int
            FROM app_data.employees emp
            WHERE emp.tenant_id = %s
              AND (%s::text IS NULL OR emp.department_id = %s)
              AND EXISTS (
                SELECT 1
                FROM app_data.expenses e
                WHERE e.tenant_id = emp.tenant_id
                  AND e.employee_id = emp.employee_id
                  AND e.department_id = emp.department_id
                  AND e.expense_month = %s
              )
            """,
            (tenant_id, department, department, month),
        )
    elif view == "departments":
        cur.execute(
            """
            SELECT count(DISTINCT d.department_id)::int
            FROM app_data.departments d
            WHERE d.tenant_id = %s
              AND (%s::text IS NULL OR d.department_id = %s)
              AND EXISTS (
                SELECT 1
                FROM app_data.expenses e
                WHERE e.tenant_id = d.tenant_id
                  AND e.department_id = d.department_id
                  AND e.expense_month = %s
              )
            """,
            (tenant_id, department, department, month),
        )
    elif view == "approval_events":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.approval_events ae
            JOIN app_data.expenses e ON e.expense_id = ae.expense_id
            WHERE ae.tenant_id = %s
              AND e.tenant_id = ae.tenant_id
              AND e.expense_month = %s
              AND (%s::text IS NULL OR e.department_id = %s)
            """,
            (tenant_id, month, department, department),
        )
    elif view == "ledger_entries":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.ledger_entries l
            JOIN app_data.expenses e ON e.expense_id = l.expense_id
            WHERE l.tenant_id = %s
              AND e.tenant_id = l.tenant_id
              AND e.expense_month = %s
              AND (%s::text IS NULL OR e.department_id = %s)
            """,
            (tenant_id, month, department, department),
        )
    else:
        raise ValueError(f"unknown view: {view}")
    return int(cur.fetchone()["count"])


def raw_out_of_scope_count(cur: psycopg.Cursor, view: str, tenant_id: str, scope: dict[str, Any]) -> int:
    month = scope["expense_month"]
    department = scope.get("department_id")
    if view == "expenses":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.expenses e
            WHERE e.tenant_id = %s
              AND NOT (
                e.expense_month = %s
                AND (%s::text IS NULL OR e.department_id = %s)
              )
            """,
            (tenant_id, month, department, department),
        )
    elif view == "employees":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.employees emp
            WHERE emp.tenant_id = %s
              AND NOT (
                (%s::text IS NULL OR emp.department_id = %s)
                AND EXISTS (
                  SELECT 1
                  FROM app_data.expenses e
                  WHERE e.tenant_id = emp.tenant_id
                    AND e.employee_id = emp.employee_id
                    AND e.department_id = emp.department_id
                    AND e.expense_month = %s
                )
              )
            """,
            (tenant_id, department, department, month),
        )
    elif view == "departments":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.departments d
            WHERE d.tenant_id = %s
              AND NOT (
                (%s::text IS NULL OR d.department_id = %s)
                AND EXISTS (
                  SELECT 1
                  FROM app_data.expenses e
                  WHERE e.tenant_id = d.tenant_id
                    AND e.department_id = d.department_id
                    AND e.expense_month = %s
                )
              )
            """,
            (tenant_id, department, department, month),
        )
    elif view == "approval_events":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.approval_events ae
            JOIN app_data.expenses e ON e.expense_id = ae.expense_id
            WHERE ae.tenant_id = %s
              AND NOT (
                e.tenant_id = ae.tenant_id
                AND e.expense_month = %s
                AND (%s::text IS NULL OR e.department_id = %s)
              )
            """,
            (tenant_id, month, department, department),
        )
    elif view == "ledger_entries":
        cur.execute(
            """
            SELECT count(*)::int
            FROM app_data.ledger_entries l
            JOIN app_data.expenses e ON e.expense_id = l.expense_id
            WHERE l.tenant_id = %s
              AND NOT (
                e.tenant_id = l.tenant_id
                AND e.expense_month = %s
                AND (%s::text IS NULL OR e.department_id = %s)
              )
            """,
            (tenant_id, month, department, department),
        )
    else:
        raise ValueError(f"unknown view: {view}")
    return int(cur.fetchone()["count"])


def row_provenance(cur: psycopg.Cursor, view: str, row: dict[str, Any]) -> dict[str, Any] | None:
    if view == "expenses":
        cur.execute(
            """
            SELECT tenant_id, expense_month, department_id
            FROM app_data.expenses
            WHERE expense_id = %s
            """,
            (row["expense_id"],),
        )
    elif view == "employees":
        cur.execute(
            """
            SELECT emp.tenant_id,
                   emp.department_id,
                   EXISTS (
                     SELECT 1
                     FROM app_data.expenses e
                     WHERE e.tenant_id = emp.tenant_id
                       AND e.employee_id = emp.employee_id
                       AND e.department_id = emp.department_id
                   ) AS has_any_expense
            FROM app_data.employees emp
            WHERE emp.employee_id = %s
            """,
            (row["employee_id"],),
        )
    elif view == "departments":
        cur.execute(
            """
            SELECT d.tenant_id,
                   d.department_id,
                   EXISTS (
                     SELECT 1
                     FROM app_data.expenses e
                     WHERE e.tenant_id = d.tenant_id
                       AND e.department_id = d.department_id
                   ) AS has_any_expense
            FROM app_data.departments d
            WHERE d.department_id = %s
            """,
            (row["department_id"],),
        )
    elif view == "approval_events":
        cur.execute(
            """
            SELECT ae.tenant_id,
                   e.tenant_id AS expense_tenant_id,
                   e.expense_month,
                   e.department_id
            FROM app_data.approval_events ae
            JOIN app_data.expenses e ON e.expense_id = ae.expense_id
            WHERE ae.event_id = %s
            """,
            (row["event_id"],),
        )
    elif view == "ledger_entries":
        cur.execute(
            """
            SELECT l.tenant_id,
                   e.tenant_id AS expense_tenant_id,
                   e.expense_month,
                   e.department_id
            FROM app_data.ledger_entries l
            JOIN app_data.expenses e ON e.expense_id = l.expense_id
            WHERE l.ledger_id = %s
            """,
            (row["ledger_id"],),
        )
    else:
        raise ValueError(f"unknown view: {view}")
    return cur.fetchone()


def row_is_in_scope(
    cur: psycopg.Cursor,
    view: str,
    row: dict[str, Any],
    tenant_id: str,
    scope: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None, str | None]:
    provenance = row_provenance(cur, view, row)
    if provenance is None:
        return False, None, "row key not found in raw provenance tables"

    month = scope["expense_month"]
    department = scope.get("department_id")

    if view in {"expenses", "approval_events", "ledger_entries"}:
        if provenance["tenant_id"] != tenant_id:
            return False, provenance, "row tenant does not match token tenant"
        if view in {"approval_events", "ledger_entries"} and provenance["expense_tenant_id"] != tenant_id:
            return False, provenance, "joined expense tenant does not match token tenant"
        if provenance["expense_month"] != month:
            return False, provenance, "row expense_month does not match token scope"
        if department is not None and provenance["department_id"] != department:
            return False, provenance, "row department_id does not match token scope"
        return True, provenance, None

    if provenance["tenant_id"] != tenant_id:
        return False, provenance, "row tenant does not match token tenant"
    if department is not None and provenance["department_id"] != department:
        return False, provenance, "row department_id does not match token scope"

    # Dimension rows must have at least one in-scope expense row.  The separate
    # exact count check proves the month predicate; this query gives a
    # row-local explanation for any unexpected leaked dimension key.
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM app_data.expenses e
          WHERE e.tenant_id = %s
            AND e.expense_month = %s
            AND (%s::text IS NULL OR e.department_id = %s)
            AND (
              (%s = 'employees' AND e.employee_id = %s)
              OR (%s = 'departments' AND e.department_id = %s)
            )
        ) AS has_in_scope_expense
        """,
        (
            tenant_id,
            month,
            department,
            department,
            view,
            row.get("employee_id"),
            view,
            row.get("department_id"),
        ),
    )
    has_in_scope_expense = bool(cur.fetchone()["has_in_scope_expense"])
    if not has_in_scope_expense:
        return False, provenance, "dimension row has no matching in-scope expense"
    return True, provenance, None


def evaluate_view(
    agent_cur: psycopg.Cursor,
    admin_cur: psycopg.Cursor,
    view: str,
    tenant_id: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    rows = fetch_safe_view_rows(agent_cur, view)
    raw_in_scope = raw_in_scope_count(admin_cur, view, tenant_id, scope)
    raw_out_of_scope = raw_out_of_scope_count(admin_cur, view, tenant_id, scope)
    violations = []

    for row in rows:
        ok, provenance, reason = row_is_in_scope(admin_cur, view, row, tenant_id, scope)
        if not ok:
            violations.append(
                {
                    "key": str(row.get(VIEW_KEY[view])),
                    "reason": reason,
                    "provenance": provenance,
                }
            )

    returned_keys = [str(row.get(VIEW_KEY[view])) for row in rows[:20]]
    passed = (
        raw_out_of_scope > 0
        and len(rows) == raw_in_scope
        and not violations
    )
    return {
        "view": view,
        "passed": passed,
        "row_count": len(rows),
        "raw_in_scope_count": raw_in_scope,
        "raw_out_of_scope_candidate_count": raw_out_of_scope,
        "non_vacuous": raw_out_of_scope > 0,
        "row_count_matches_raw_scope": len(rows) == raw_in_scope,
        "violations": violations,
        "returned_key_sample": returned_keys,
    }


def evaluate_scenario(base_url: str, run_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
    issued = issue_task(base_url, run_id, scenario)
    payload = issued.task["payload"]
    tenant_id = payload["tenant_id"]
    allowed_views = payload["allowed_views"]
    view_results = []
    state: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []

    with psycopg.connect(credential_dsn(issued.credential), row_factory=dict_row) as agent_conn:
        agent_conn.autocommit = True
        with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as admin_conn:
            admin_conn.autocommit = True
            with agent_conn.cursor() as agent_cur, admin_conn.cursor() as admin_cur:
                agent_cur.execute(
                    "SELECT taskbound.bind_task(%s, %s)",
                    (issued.task["payload_text"], issued.task["signature"]),
                )
                bound = agent_cur.fetchone()["bind_task"]

                for view in allowed_views:
                    view_results.append(
                        evaluate_view(
                            agent_cur,
                            admin_cur,
                            view,
                            tenant_id,
                            payload["row_scope"],
                        )
                    )

                agent_cur.execute("SELECT * FROM taskbound.inspect_task_state()")
                state = list(agent_cur.fetchall())
                agent_cur.execute("SELECT decision, reason, rows_returned, touched_views FROM taskbound.receipts()")
                receipts = list(agent_cur.fetchall())
                agent_cur.execute("SELECT taskbound.unbind_task()")

    receipt_count = len(receipts)
    allowed_receipts = sum(1 for receipt in receipts if receipt["decision"] == "allowed")
    query_count = state[0]["query_count"] if state else None
    accounting_ok = (
        query_count == len(allowed_views)
        and receipt_count == len(allowed_views)
        and allowed_receipts == len(allowed_views)
    )
    passed = all(result["passed"] for result in view_results) and accounting_ok
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "task_type": scenario["task_type"],
        "delegator": scenario["delegator"],
        "scope": payload["row_scope"],
        "tenant_id": tenant_id,
        "allowed_views": allowed_views,
        "bound": bound,
        "passed": passed,
        "view_results": view_results,
        "accounting": {
            "passed": accounting_ok,
            "query_count": query_count,
            "receipt_count": receipt_count,
            "allowed_receipt_count": allowed_receipts,
            "expected_allowed_queries": len(allowed_views),
            "state": state,
            "receipt_touched_views": [receipt["touched_views"] for receipt in receipts],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("TDSC_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    wait_for_api(base_url)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    records = [evaluate_scenario(base_url, run_id, scenario) for scenario in SCENARIOS]

    view_check_count = sum(len(record["view_results"]) for record in records)
    failed_view_checks = sum(
        1
        for record in records
        for view_result in record["view_results"]
        if not view_result["passed"]
    )
    non_vacuous_view_checks = sum(
        1
        for record in records
        for view_result in record["view_results"]
        if view_result["non_vacuous"]
    )
    run = {
        "all_scope_complete": all(record["passed"] for record in records),
        "all_view_checks_non_vacuous": non_vacuous_view_checks == view_check_count,
        "base_url": base_url,
        "commit": git_commit(),
        "failed_scenarios": sum(1 for record in records if not record["passed"]),
        "failed_view_checks": failed_view_checks,
        "non_vacuous_view_checks": non_vacuous_view_checks,
        "passed_scenarios": sum(1 for record in records if record["passed"]),
        "scenario_count": len(records),
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "view_check_count": view_check_count,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"scope_completeness_{run_id}.json"
    output_path.write_text(
        json.dumps({"run": run, "records": records}, indent=2, default=json_default),
        encoding="utf-8",
    )

    print(output_path)
    print(json.dumps(run, indent=2))
    if not run["all_scope_complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
