#!/usr/bin/env python3
"""Evaluate token-specific denied fields across API/wrapper/native paths.

The static review noted that token-specific `denied_columns` must reach the
database boundary, not only API preflight.  The hardened design rejects binding
when a token denies a column that an approved safe view still exposes.  This
script validates that behavior across the three task SQL paths and across the
query shapes requested by the review.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

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
SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me").encode("utf-8")


CASES: list[dict[str, Any]] = [
    {
        "id": "DF00",
        "name": "control_amount_allowed_without_dynamic_deny",
        "expected": "Allowed",
        "reason_bucket": "allowed",
        "mutate_denied_columns": [],
        "sql": "SELECT amount FROM expenses ORDER BY expense_id LIMIT 1",
    },
    {
        "id": "DF01",
        "name": "projection_denied_by_dynamic_token_policy",
        "expected": "Blocked",
        "reason_bucket": "bind_token_denied_exposed",
        "mutate_denied_columns": ["expenses.amount"],
        "sql": "SELECT amount FROM expenses ORDER BY expense_id LIMIT 1",
    },
    {
        "id": "DF02",
        "name": "alias_denied_by_dynamic_token_policy",
        "expected": "Blocked",
        "reason_bucket": "bind_token_denied_exposed",
        "mutate_denied_columns": ["expenses.amount"],
        "sql": "SELECT amount AS reimbursed_amount FROM expenses LIMIT 1",
    },
    {
        "id": "DF03",
        "name": "where_denied_by_dynamic_token_policy",
        "expected": "Blocked",
        "reason_bucket": "bind_token_denied_exposed",
        "mutate_denied_columns": ["expenses.amount"],
        "sql": "SELECT expense_id FROM expenses WHERE amount > 0 ORDER BY expense_id LIMIT 1",
    },
    {
        "id": "DF04",
        "name": "order_by_denied_by_dynamic_token_policy",
        "expected": "Blocked",
        "reason_bucket": "bind_token_denied_exposed",
        "mutate_denied_columns": ["expenses.amount"],
        "sql": "SELECT expense_id FROM expenses ORDER BY amount DESC LIMIT 1",
    },
    {
        "id": "DF05",
        "name": "aggregate_input_denied_by_dynamic_token_policy",
        "expected": "Blocked",
        "reason_bucket": "bind_token_denied_exposed",
        "mutate_denied_columns": ["expenses.amount"],
        "sql": "SELECT sum(amount) AS total_amount FROM expenses",
    },
    {
        "id": "DF06",
        "name": "window_expression_denied_by_dynamic_token_policy",
        "expected": "Blocked",
        "reason_bucket": "bind_token_denied_exposed",
        "mutate_denied_columns": ["expenses.amount"],
        "sql": (
            "SELECT expense_id, row_number() OVER (ORDER BY amount DESC) AS rn "
            "FROM expenses LIMIT 1"
        ),
    },
]


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign(payload_text: str) -> str:
    return hmac.new(SECRET, payload_text.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_reason(text: str) -> str:
    lowered = (text or "").lower()
    if not lowered:
        return "allowed"
    if "denied column remains exposed by an approved safe view" in lowered:
        return "bind_token_denied_exposed"
    if "denied column" in lowered or "sensitive column" in lowered:
        return "denied_column"
    if "aggregate" in lowered or "group by" in lowered or "window" in lowered:
        return "aggregate_template_required"
    if "budget" in lowered:
        return "budget"
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


def issue_task(base_url: str, run_id: str, case: dict[str, Any], path: str) -> IssuedTask:
    actor = "agent:travel-expense-analyst"
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"dynamic-denied-{run_id}-{case['id'].lower()}-{path}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    )
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": f"task_dynamic_denied_{run_id}_{case['id'].lower()}_{path}",
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
            "failed to create credential/task for "
            f"{case['id']}/{path}: credential={credential} task={task}"
        )

    denied_additions = case.get("mutate_denied_columns") or []
    if denied_additions:
        payload = dict(task["payload"])
        payload["denied_columns"] = list(payload.get("denied_columns") or [])
        for column in denied_additions:
            if column not in payload["denied_columns"]:
                payload["denied_columns"].append(column)
        payload_text = canonical(payload)
        task = {
            **task,
            "payload": payload,
            "payload_text": payload_text,
            "signature": sign(payload_text),
        }

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
    }


def evaluate_case(base_url: str, run_id: str, case: dict[str, Any]) -> dict[str, Any]:
    path_results: dict[str, dict[str, Any]] = {}
    for path in PATHS:
        issued = issue_task(base_url, run_id, case, path)
        if path == "api_wrapper":
            path_results[path] = run_api_wrapper(base_url, issued, case["sql"])
        elif path == "direct_wrapper":
            path_results[path] = run_db_path(issued, case["sql"], native=False)
        else:
            path_results[path] = run_db_path(issued, case["sql"], native=True)

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
        receipt_ok = all(result["receipts"]["count"] == 0 for result in path_results.values())

    return {
        "id": case["id"],
        "name": case["name"],
        "sql": case["sql"],
        "expected": case["expected"],
        "reason_bucket": case["reason_bucket"],
        "mutate_denied_columns": case.get("mutate_denied_columns") or [],
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
    records = [evaluate_case(base_url, run_id, case) for case in CASES]
    run = {
        "all_dynamic_denied_field_checks_passed": all(record["passed"] for record in records),
        "all_path_consistent": all(record["path_consistent"] for record in records),
        "base_url": base_url,
        "case_count": len(records),
        "commit": git_commit(),
        "evaluated_path_decisions": len(records) * len(PATHS),
        "failed": sum(1 for record in records if not record["passed"]),
        "passed": sum(1 for record in records if record["passed"]),
        "path_count": len(PATHS),
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"dynamic_denied_field_{run_id}.json"
    output_path.write_text(json.dumps({"run": run, "records": records}, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(run, indent=2))
    if not run["all_dynamic_denied_field_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
