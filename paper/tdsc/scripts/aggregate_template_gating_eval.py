#!/usr/bin/env python3
"""Evaluate conservative aggregate/window gating across SQL paths.

The static review identified several minimum-group bypasses: forged
cardinality aliases, filtered ungrouped aggregates, ordinary merchant/category
groups, window partitions, and aggregate release through CTE/subquery shapes.
The current prototype intentionally denies all direct aggregate/window release
until approved aggregate templates exist.  This script validates that narrower
claim across API, direct `taskbound.run(...)`, and direct native safe-view SQL.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from path_consistency_eval import (
    PATHS,
    IssuedTask,
    classify_result,
    credential_dsn,
    fetch_receipts,
    fetch_state,
    git_commit,
    post_json,
    post_json_retry,
    rows_as_dicts,
    state_summary,
    wait_for_api,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "paper" / "tdsc" / "raw_results"
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:15432/travel",
)
TENANT_ID = "company_a"
SCOPE = {"expense_month": "2026-06", "department_id": "dep_sales"}


CASES: list[dict[str, Any]] = [
    {
        "id": "AG00",
        "name": "control_non_aggregate_detail_allowed",
        "expected": "Allowed",
        "reason_bucket": "allowed",
        "sql": "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 2",
    },
    {
        "id": "AG01",
        "name": "forged_employee_count_alias_grouped_aggregate",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT merchant, 999 AS employee_count, SUM(amount) AS total_amount "
            "FROM expenses GROUP BY merchant"
        ),
    },
    {
        "id": "AG02",
        "name": "forged_entity_count_alias_grouped_aggregate",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT merchant, 999 AS entity_count, SUM(amount) AS total_amount "
            "FROM expenses GROUP BY merchant"
        ),
    },
    {
        "id": "AG03",
        "name": "forged_distinct_employee_count_alias_grouped_aggregate",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT merchant, 999 AS distinct_employee_count, SUM(amount) AS total_amount "
            "FROM expenses GROUP BY merchant"
        ),
    },
    {
        "id": "AG04",
        "name": "forged_employees_in_group_alias_grouped_aggregate",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT merchant, 999 AS employees_in_group, SUM(amount) AS total_amount "
            "FROM expenses GROUP BY merchant"
        ),
    },
    {
        "id": "AG05",
        "name": "forged_distinct_employees_alias_grouped_aggregate",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT merchant, 999 AS distinct_employees, SUM(amount) AS total_amount "
            "FROM expenses GROUP BY merchant"
        ),
    },
    {
        "id": "AG06",
        "name": "filtered_ungrouped_rare_merchant_aggregate",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql_template": "SELECT SUM(amount) AS total_amount FROM expenses WHERE merchant = '{rare_merchant}'",
        "requires_rare_merchant": True,
    },
    {
        "id": "AG07",
        "name": "ordinary_category_grouping",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": "SELECT category, COUNT(*) AS n, SUM(amount) AS total_amount FROM expenses GROUP BY category",
    },
    {
        "id": "AG08",
        "name": "ordinary_merchant_city_grouping",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT merchant, city, COUNT(*) AS n, SUM(amount) AS total_amount "
            "FROM expenses GROUP BY merchant, city"
        ),
    },
    {
        "id": "AG09",
        "name": "cte_aggregate_release",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "WITH merchant_totals AS ("
            "  SELECT merchant, 999 AS employee_count, SUM(amount) AS total_amount "
            "  FROM expenses GROUP BY merchant"
            ") SELECT merchant, employee_count, total_amount FROM merchant_totals"
        ),
    },
    {
        "id": "AG10",
        "name": "subquery_aggregate_release",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT * FROM ("
            "  SELECT merchant, COUNT(*) AS n, SUM(amount) AS total_amount "
            "  FROM expenses GROUP BY merchant"
            ") grouped"
        ),
    },
    {
        "id": "AG11",
        "name": "window_partition_release",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT expense_id, merchant, "
            "row_number() OVER (PARTITION BY merchant ORDER BY amount DESC) AS merchant_rank "
            "FROM expenses"
        ),
    },
]


def normalize_reason(text: str) -> str:
    lowered = (text or "").lower()
    if not lowered:
        return "allowed"
    if "aggregate" in lowered or "group" in lowered or "having" in lowered or "window" in lowered:
        return "aggregate_template_required"
    if "budget" in lowered:
        return "budget"
    if "denied column" in lowered or "sensitive column" in lowered:
        return "denied_column"
    return "other"


def receipt_summary(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    if not receipts:
        return {"count": 0, "latest_decision": None, "latest_reason_bucket": None}
    latest = receipts[0]
    return {
        "count": len(receipts),
        "latest_decision": latest.get("decision"),
        "latest_reason_bucket": normalize_reason(str(latest.get("reason") or "")),
        "latest_rows_returned": latest.get("rows_returned"),
        "latest_unique_rows_added": latest.get("unique_rows_added"),
        "latest_touched_views": latest.get("touched_views"),
    }


def rare_merchant_context() -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT merchant,
                       count(*)::int AS expense_count,
                       count(DISTINCT employee_id)::int AS distinct_employee_count
                FROM app_data.expenses
                WHERE tenant_id = %s
                  AND expense_month = %s
                  AND department_id = %s
                GROUP BY merchant
                ORDER BY count(DISTINCT employee_id), count(*), merchant
                LIMIT 1
                """,
                (TENANT_ID, SCOPE["expense_month"], SCOPE["department_id"]),
            )
            row = cur.fetchone()
    if row is None:
        raise RuntimeError("seed has no merchant rows in the aggregate-gating scope")
    return dict(row)


def resolve_case_sql(case: dict[str, Any], rare: dict[str, Any]) -> str:
    if "sql" in case:
        return case["sql"]
    merchant = str(rare["merchant"]).replace("'", "''")
    return case["sql_template"].format(rare_merchant=merchant)


def issue_task(base_url: str, run_id: str, case_id: str, path: str) -> IssuedTask:
    actor = "agent:travel-expense-analyst"
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"aggregate-gating-{run_id}-{case_id.lower()}-{path}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    )
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": f"task_aggregate_gating_{run_id}_{case_id.lower()}_{path}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": SCOPE,
            "max_queries": 20,
            "max_rows": 5000,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(
            "failed to create credential/task for "
            f"{case_id}/{path}: credential={credential} task={task}"
        )
    return IssuedTask(credential=credential, task=task)


def run_api_wrapper(base_url: str, issued: IssuedTask, sql: str) -> dict[str, Any]:
    result = post_json(
        base_url,
        "/agent-query",
        {
            "credential": issued.credential,
            "payload_text": issued.task["payload_text"],
            "signature": issued.task["signature"],
            "sql": sql,
        },
    )
    ok = bool(result.get("ok"))
    error = str(result.get("error") or result.get("detail") or "")
    receipts = result.get("receipts") or []
    return {
        "path": "api_wrapper",
        "classification": classify_result(ok),
        "reason_bucket": normalize_reason(error if not ok else ""),
        "row_count": len(result.get("rows") or []),
        "state": state_summary(result.get("state") or []),
        "receipts": receipt_summary(receipts),
        "error": error.splitlines()[0] if error else "",
        "ast_allowed": (result.get("ast_validation") or {}).get("allowed"),
    }


def run_db_path(issued: IssuedTask, sql: str, *, native: bool) -> dict[str, Any]:
    path = "direct_native" if native else "direct_wrapper"
    rows: list[Any] = []
    error = ""
    ok = False
    state: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []

    with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT taskbound.bind_task(%s, %s)",
                    (issued.task["payload_text"], issued.task["signature"]),
                )
                if native:
                    cur.execute(sql)
                    rows = rows_as_dicts(cur)
                else:
                    cur.execute("SELECT * FROM taskbound.run(%s)", (sql,))
                    rows = [row[0] for row in cur.fetchall()]
                ok = True
            except Exception as exc:
                error = str(exc).splitlines()[0]
            finally:
                try:
                    state = fetch_state(cur)
                    receipts = fetch_receipts(cur)
                except Exception as exc:
                    if not error:
                        error = f"post-query inspection failed: {exc}".splitlines()[0]
                try:
                    cur.execute("SELECT taskbound.unbind_task()")
                except Exception:
                    pass

    reason_text = ""
    if not ok:
        reason_text = error
        if receipts:
            latest_reason = receipts[0].get("reason")
            if latest_reason:
                reason_text = str(latest_reason)
    return {
        "path": path,
        "classification": classify_result(ok),
        "reason_bucket": normalize_reason(reason_text),
        "row_count": len(rows),
        "state": state_summary(state),
        "receipts": receipt_summary(receipts),
        "error": error,
        "ast_allowed": None,
    }


def semantic_signature(record: dict[str, Any]) -> dict[str, Any]:
    state = record.get("state") or {}
    receipts = record.get("receipts") or {}
    return {
        "classification": record.get("classification"),
        "reason_bucket": record.get("reason_bucket"),
        "query_count": state.get("query_count"),
        "returned_rows": state.get("returned_rows"),
        "unique_expense_rows": state.get("unique_expense_rows"),
        "receipt_count": receipts.get("count"),
        "latest_receipt_decision": receipts.get("latest_decision"),
        "latest_receipt_reason_bucket": receipts.get("latest_reason_bucket"),
    }


def evaluate_case(base_url: str, run_id: str, case: dict[str, Any], rare: dict[str, Any]) -> dict[str, Any]:
    sql = resolve_case_sql(case, rare)
    path_results: dict[str, dict[str, Any]] = {}
    for path in PATHS:
        issued = issue_task(base_url, run_id, case["id"], path)
        if path == "api_wrapper":
            path_results[path] = run_api_wrapper(base_url, issued, sql)
        elif path == "direct_wrapper":
            path_results[path] = run_db_path(issued, sql, native=False)
        else:
            path_results[path] = run_db_path(issued, sql, native=True)

    signatures = {path: semantic_signature(result) for path, result in path_results.items()}
    reference = signatures[PATHS[0]]
    path_consistent = all(signatures[path] == reference for path in PATHS)
    expected_ok = all(
        result["classification"] == case["expected"]
        and result["reason_bucket"] == case["reason_bucket"]
        for result in path_results.values()
    )
    if case["expected"] == "Allowed":
        receipt_ok = all(
            result["receipts"]["count"] == 1
            and result["receipts"]["latest_decision"] == "allowed"
            and result["row_count"] > 0
            for result in path_results.values()
        )
    else:
        receipt_ok = all(
            result["receipts"]["count"] == 1
            and result["receipts"]["latest_decision"] == "denied"
            and result["receipts"]["latest_reason_bucket"] == case["reason_bucket"]
            for result in path_results.values()
        )

    return {
        "id": case["id"],
        "name": case["name"],
        "sql": sql,
        "expected": case["expected"],
        "reason_bucket": case["reason_bucket"],
        "rare_merchant_context": rare if case.get("requires_rare_merchant") else None,
        "passed": path_consistent and expected_ok and receipt_ok,
        "path_consistent": path_consistent,
        "expected_ok": expected_ok,
        "receipt_ok": receipt_ok,
        "semantic_signatures": signatures,
        "path_results": path_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("TDSC_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    wait_for_api(base_url)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rare = rare_merchant_context()
    records = [evaluate_case(base_url, run_id, case, rare) for case in CASES]
    blocked_cases = [record for record in records if record["expected"] == "Blocked"]
    run = {
        "all_aggregate_template_gating_checks_passed": all(record["passed"] for record in records),
        "all_path_consistent": all(record["path_consistent"] for record in records),
        "base_url": base_url,
        "blocked_case_count": len(blocked_cases),
        "case_count": len(records),
        "commit": git_commit(),
        "evaluated_path_decisions": len(records) * len(PATHS),
        "failed": sum(1 for record in records if not record["passed"]),
        "passed": sum(1 for record in records if record["passed"]),
        "path_count": len(PATHS),
        "rare_merchant_context": rare,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"aggregate_template_gating_{run_id}.json"
    output_path.write_text(json.dumps({"run": run, "records": records}, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(run, indent=2))
    if not run["all_aggregate_template_gating_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
