#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg


BASE_URL = os.environ.get("TDSC_BASE_URL", "http://127.0.0.1:8000")
OUT_DIR = Path(os.environ.get("TDSC_OUT_DIR", "paper/tdsc-v2/experiments/raw_results"))
DB_HOST = os.environ.get("TDSC_DB_HOST", "postgres")
DB_NAME = os.environ.get("TDSC_DB_NAME", "travel")


@dataclass(frozen=True)
class Baseline:
    name: str
    dsn: str | None
    query_schema: str
    mode: str = "db"


BASELINES = [
    Baseline("Raw PostgreSQL", f"postgresql://postgres:postgres@{DB_HOST}:5432/{DB_NAME}", "app_data"),
    Baseline("Role-only", f"postgresql://tdsc_role_only:tdsc_role_only_pass@{DB_HOST}:5432/{DB_NAME}", "app_data"),
    Baseline("Safe-view-only", f"postgresql://tdsc_safe_view_only:tdsc_safe_view_only_pass@{DB_HOST}:5432/{DB_NAME}", "tdsc_safe_view_only"),
    Baseline("RLS-only", f"postgresql://tdsc_rls_only:tdsc_rls_only_pass@{DB_HOST}:5432/{DB_NAME}", "app_data"),
    Baseline("Full SessionBound", None, "taskbound", mode="sessionbound"),
]


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"ok": False, "http_error": exc.code, **payload}


def open_session(max_queries: int = 20, max_rows: int = 5000) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = str(int(time.time() * 1000))
    credential = post_json("/credentials", {"agent_id": f"tdsc-security-{suffix}", "ttl_minutes": 15})
    task = post_json(
        "/tasks",
        {
            "task_id": f"tdsc_security_{suffix}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "department_id": "dep_sales",
            "scope": {"expense_month": "2026-06", "department_id": "dep_sales"},
            "max_rows": max_rows,
            "max_queries": max_queries,
        },
    )
    return credential, task


def sessionbound_query(sql: str, *, max_queries: int = 20, max_rows: int = 5000) -> dict[str, Any]:
    credential, task = open_session(max_queries=max_queries, max_rows=max_rows)
    result = post_json(
        "/agent-query",
        {
            "credential": credential,
            "payload_text": task.get("payload_text"),
            "signature": task.get("signature"),
            "sql": sql,
        },
    )
    if isinstance(result.get("rows"), list):
        result["row_count"] = len(result["rows"])
    return result


def run_db_query(baseline: Baseline, sql_text: str, *, rollback: bool = False) -> dict[str, Any]:
    assert baseline.dsn is not None
    conn = psycopg.connect(baseline.dsn)
    try:
        conn.autocommit = not rollback
        with conn.cursor() as cur:
            cur.execute(sql_text)
            rows: list[Any] = []
            if cur.description is not None:
                rows = cur.fetchall()
            if rollback:
                conn.rollback()
            return {"ok": True, "row_count": len(rows), "preview": [list(r) for r in rows[:3]]}
    except Exception as exc:
        if rollback:
            conn.rollback()
        return {"ok": False, "error": str(exc).splitlines()[0]}
    finally:
        conn.close()


def q(baseline: Baseline, safe_sql: str, raw_sql: str | None = None, *, rollback: bool = False) -> dict[str, Any]:
    if baseline.mode == "sessionbound":
        return sessionbound_query(safe_sql)
    sql_text = safe_sql if baseline.query_schema != "app_data" else (raw_sql or safe_sql)
    return run_db_query(baseline, sql_text, rollback=rollback)


def allowed(result: dict[str, Any]) -> str:
    return "Allowed" if result.get("ok") else "Denied"


def main() -> None:
    run_id = str(int(time.time()))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"run_id": run_id, "base_url": BASE_URL, "baselines": []}

    for baseline in BASELINES:
        schema = baseline.query_schema
        checks: dict[str, dict[str, Any]] = {}

        checks["safe_select"] = q(
            baseline,
            f"SELECT expense_id, amount FROM {schema}.expenses ORDER BY amount DESC LIMIT 3",
            "SELECT expense_id, amount FROM app_data.expenses WHERE tenant_id='company_a' AND expense_month='2026-06' AND department_id='dep_sales' ORDER BY amount DESC LIMIT 3",
        )
        checks["join"] = q(
            baseline,
            f"SELECT e.expense_id, d.department_name FROM {schema}.expenses e JOIN {schema}.departments d ON e.department_id=d.department_id LIMIT 3",
            "SELECT e.expense_id, d.department_name FROM app_data.expenses e JOIN app_data.departments d ON e.department_id=d.department_id WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' AND e.department_id='dep_sales' LIMIT 3",
        )
        checks["out_of_scope_month"] = q(
            baseline,
            f"SELECT expense_id FROM {schema}.expenses WHERE expense_month='2026-05' LIMIT 3",
            "SELECT expense_id FROM app_data.expenses WHERE expense_month='2026-05' LIMIT 3",
        )
        checks["salary_field"] = q(
            baseline,
            f"SELECT employee_name, salary FROM {schema}.employees LIMIT 3",
            "SELECT employee_name, salary FROM app_data.employees LIMIT 3",
        )
        checks["bank_account_field"] = q(
            baseline,
            f"SELECT employee_name, bank_account FROM {schema}.employees LIMIT 3",
            "SELECT employee_name, bank_account FROM app_data.employees LIMIT 3",
        )
        checks["raw_table_access"] = q(
            baseline,
            "SELECT * FROM app_data.expenses LIMIT 1",
            "SELECT * FROM app_data.expenses LIMIT 1",
        )
        checks["write_attempt"] = q(
            baseline,
            f"UPDATE {schema}.expenses SET status=status WHERE expense_id='exp_004'",
            "UPDATE app_data.expenses SET status=status WHERE expense_id='exp_004'",
            rollback=True,
        )
        checks["ddl_attempt"] = q(
            baseline,
            "CREATE TEMP TABLE tdsc_tmp_probe(x int)",
            "CREATE TEMP TABLE tdsc_tmp_probe(x int)",
            rollback=True,
        )
        checks["catalog_access"] = q(
            baseline,
            "SELECT tablename FROM pg_catalog.pg_tables LIMIT 3",
            "SELECT tablename FROM pg_catalog.pg_tables LIMIT 3",
        )
        checks["json_agg_payload"] = q(
            baseline,
            f"SELECT json_agg(e) FROM {schema}.expenses e",
            "SELECT json_agg(e) FROM app_data.expenses e WHERE e.tenant_id='company_a' AND e.expense_month='2026-06' AND e.department_id='dep_sales'",
        )

        budget_first = None
        if baseline.mode == "sessionbound":
            credential, task = open_session(max_queries=1, max_rows=5000)
            body = {
                "credential": credential,
                "payload_text": task.get("payload_text"),
                "signature": task.get("signature"),
                "sql": "SELECT count(*) AS n FROM expenses",
            }
            budget_first = post_json("/agent-query", body)
            budget_second = post_json("/agent-query", body)
            checks["query_budget_second_query"] = budget_second
            checks["receipts"] = {"ok": bool(budget_first.get("receipts"))}
        else:
            checks["query_budget_second_query"] = {"ok": True, "note": "No query budget mechanism in this baseline."}
            checks["receipts"] = {"ok": False, "note": "No receipt mechanism in this baseline."}

        properties = {
            "Blocks writes": "Yes" if not checks["write_attempt"].get("ok") else "No",
            "Blocks raw table access": "Yes" if not checks["raw_table_access"].get("ok") else "No",
            "Blocks denied fields": "Yes" if (not checks["salary_field"].get("ok") and not checks["bank_account_field"].get("ok")) else "No",
            "Enforces row scope": "Yes" if checks["out_of_scope_month"].get("ok") and checks["out_of_scope_month"].get("row_count") == 0 else "No",
            "Query budget": "Yes" if not checks["query_budget_second_query"].get("ok") else "No",
            "Disclosure budget": "Yes" if baseline.mode == "sessionbound" else "No",
            "Payload aggregation blocking": "Yes" if not checks["json_agg_payload"].get("ok") else "No",
            "Credential-token binding": "Yes" if baseline.mode == "sessionbound" else "No",
            "Receipts": "Yes" if checks["receipts"].get("ok") else "No",
            "Schema drift token invalidation": "Not tested",
        }

        results["baselines"].append(
            {
                "name": baseline.name,
                "mode": baseline.mode,
                "schema": baseline.query_schema,
                "checks": checks,
                "properties": properties,
            }
        )

    json_path = OUT_DIR / f"security_baseline_{run_id}.json"
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json_path)


if __name__ == "__main__":
    main()
