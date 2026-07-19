#!/usr/bin/env python3
"""Compare SessionBound API/wrapper/native acceptance on one SQL corpus.

The static review challenged the claim that the API, PL/pgSQL wrapper, and
native PostgreSQL hook implement the same policy.  This evaluator uses a fresh
task and short-lived credential for each (case, path) pair, then compares:

* allow/deny classification;
* normalized reason bucket for denials;
* query/disclosure budget deltas; and
* exactly-one decision receipt for each evaluated query.

Fresh tasks avoid budget state from one path changing the expected outcome of
another path.
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
from pathlib import Path
from typing import Any

import psycopg


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DB_HOST = os.environ.get("TDSC_AGENT_DB_HOST", "localhost")
AGENT_DB_PORT = int(os.environ.get("TDSC_AGENT_DB_PORT", "15432"))


PATHS = ("api_wrapper", "direct_wrapper", "direct_native")


CASES: list[dict[str, Any]] = [
    {
        "id": "PC01",
        "name": "detail_projection_allowed",
        "expected": "Allowed",
        "reason_bucket": "allowed",
        "sql": "SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 2",
    },
    {
        "id": "PC02",
        "name": "safe_view_join_allowed",
        "expected": "Allowed",
        "reason_bucket": "allowed",
        "sql": (
            "SELECT e.expense_id, d.department_name "
            "FROM expenses e JOIN departments d USING (department_id) "
            "ORDER BY e.expense_id LIMIT 2"
        ),
    },
    {
        "id": "PC03",
        "name": "non_aggregate_cte_allowed",
        "expected": "Allowed",
        "reason_bucket": "allowed",
        "sql": (
            "WITH recent AS ("
            "  SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 3"
            ") "
            "SELECT expense_id FROM recent ORDER BY expense_id"
        ),
    },
    {
        "id": "PC04",
        "name": "denied_column_blocked",
        "expected": "Blocked",
        "reason_bucket": "denied_column",
        "excluded_paths_from_consistency": ["direct_native"],
        "exclusion_reason": (
            "salary is not exposed by the hardened safe view, so direct native "
            "PostgreSQL rejects it as an undefined column before post-parse "
            "policy hooks can emit a receipt"
        ),
        "sql": "SELECT employee_name, salary FROM employees",
    },
    {
        "id": "PC05",
        "name": "raw_schema_blocked",
        "expected": "Blocked",
        "reason_bucket": "raw_schema",
        "sql": "SELECT expense_id FROM app_data.expenses LIMIT 1",
    },
    {
        "id": "PC06",
        "name": "catalog_blocked",
        "expected": "Blocked",
        "reason_bucket": "catalog",
        "sql": "SELECT tablename FROM pg_tables LIMIT 1",
    },
    {
        "id": "PC07",
        "name": "set_operation_blocked",
        "expected": "Blocked",
        "reason_bucket": "set_operation",
        "sql": "SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees",
    },
    {
        "id": "PC08",
        "name": "grouped_aggregate_blocked",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": "SELECT merchant, count(*) AS expense_count FROM expenses GROUP BY merchant",
    },
    {
        "id": "PC09",
        "name": "ungrouped_aggregate_blocked",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": "SELECT count(*) AS expense_count FROM expenses",
    },
    {
        "id": "PC10",
        "name": "window_release_blocked",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": (
            "SELECT expense_id, row_number() OVER (ORDER BY amount DESC) AS rn "
            "FROM expenses LIMIT 2"
        ),
    },
    {
        "id": "PC11",
        "name": "side_effect_function_blocked",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": "SELECT pg_sleep(0) FROM expenses LIMIT 1",
    },
    {
        "id": "PC12",
        "name": "tablesample_blocked",
        "expected": "Blocked",
        "reason_bucket": "tablesample",
        "excluded_paths_from_consistency": ["direct_native"],
        "exclusion_reason": (
            "TABLESAMPLE on a view is rejected by PostgreSQL analysis before "
            "the native policy hook receives an analyzed query tree"
        ),
        "sql": "SELECT expense_id FROM expenses TABLESAMPLE SYSTEM (10)",
    },
]


@dataclass
class IssuedTask:
    credential: dict[str, Any]
    task: dict[str, Any]


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


def issue_task(base_url: str, run_id: str, case_id: str, path: str) -> IssuedTask:
    actor = "agent:travel-expense-analyst"
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"path-consistency-{run_id}-{case_id.lower()}-{path}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    )
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": f"task_path_{run_id}_{case_id.lower()}_{path}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": actor,
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 20,
        },
    )
    if not credential.get("db_user") or not task.get("payload_text"):
        raise RuntimeError(
            "failed to create credential/task for "
            f"{case_id}/{path}: credential={credential} task={task}"
        )
    return IssuedTask(credential=credential, task=task)


def credential_dsn(credential: dict[str, Any]) -> str:
    return (
        f"postgresql://{credential['db_user']}:{credential['db_password']}"
        f"@{AGENT_DB_HOST}:{AGENT_DB_PORT}/{credential.get('db_name', 'travel')}"
    )


def rows_as_dicts(cur) -> list[dict[str, Any]]:
    if cur.description is None:
        return []
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def fetch_state(cur) -> list[dict[str, Any]]:
    cur.execute("SELECT * FROM taskbound.inspect_task_state()")
    return rows_as_dicts(cur)


def fetch_receipts(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT decision, rows_returned, unique_rows_added,
               remaining_unique_row_budget, reason, touched_views, created_at
        FROM taskbound.receipts()
        ORDER BY created_at DESC
        LIMIT 20
        """
    )
    return rows_as_dicts(cur)


def bind(cur, task: dict[str, Any]) -> dict[str, Any]:
    cur.execute("SELECT taskbound.bind_task(%s, %s)", (task["payload_text"], task["signature"]))
    return cur.fetchone()[0]


def normalize_reason(text: str) -> str:
    lowered = (text or "").lower()
    if not lowered:
        return "allowed"
    if "salary" in lowered or "bank_account" in lowered or "phone" in lowered or "denied column" in lowered or "sensitive column" in lowered:
        return "denied_column"
    if "aggregate" in lowered or "group by" in lowered or "having" in lowered or "window" in lowered:
        return "aggregate_template_required"
    if "function" in lowered or "pg_sleep" in lowered or "not allowed for this task" in lowered:
        return "function_policy"
    if "app_data" in lowered or "raw application schema" in lowered or "internal schemas" in lowered:
        return "raw_schema"
    if "catalog" in lowered or "information_schema" in lowered or "pg_tables" in lowered:
        return "catalog"
    if "union" in lowered or "intersect" in lowered or "except" in lowered:
        return "set_operation"
    if "tablesample" in lowered or "sample" in lowered:
        return "tablesample"
    if "only select" in lowered or "multiple sql statements" in lowered:
        return "operation"
    if "relation is outside" in lowered or "approved safe-view" in lowered:
        return "relation_policy"
    if "budget" in lowered:
        return "budget"
    return "other"


def classify_result(ok: bool) -> str:
    return "Allowed" if ok else "Blocked"


def state_summary(state: list[dict[str, Any]]) -> dict[str, Any]:
    if not state:
        return {}
    row = state[0]
    return {
        "query_count": row.get("query_count"),
        "returned_rows": row.get("returned_rows"),
        "unique_expense_rows": row.get("unique_expense_rows"),
    }


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
                bind(cur, issued.task)
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


def evaluate_case(base_url: str, run_id: str, case: dict[str, Any]) -> dict[str, Any]:
    path_results: dict[str, dict[str, Any]] = {}
    for path in PATHS:
        issued = issue_task(base_url, run_id, case["id"], path)
        if path == "api_wrapper":
            path_results[path] = run_api_wrapper(base_url, issued, case["sql"])
        elif path == "direct_wrapper":
            path_results[path] = run_db_path(issued, case["sql"], native=False)
        else:
            path_results[path] = run_db_path(issued, case["sql"], native=True)

    signatures = {path: semantic_signature(result) for path, result in path_results.items()}
    excluded_paths = set(case.get("excluded_paths_from_consistency") or [])
    included_paths = [path for path in PATHS if path not in excluded_paths]
    reference = signatures[included_paths[0]]
    path_consistent = all(signatures[path] == reference for path in included_paths)
    expected_ok = all(
        result["classification"] == case["expected"]
        and result["reason_bucket"] == case["reason_bucket"]
        for result in path_results.values()
    )
    receipt_ok = all(
        path_results[path]["receipts"]["count"] == 1
        and path_results[path]["receipts"]["latest_decision"] == (
            "allowed" if result["classification"] == "Allowed" else "denied"
        )
        for path, result in path_results.items()
        if path not in excluded_paths
    )
    excluded_observations = {
        path: {
            "classification": path_results[path]["classification"],
            "reason_bucket": path_results[path]["reason_bucket"],
            "receipt_count": path_results[path]["receipts"]["count"],
        }
        for path in excluded_paths
        if path in path_results
    }
    passed = path_consistent and expected_ok and receipt_ok
    return {
        **case,
        "path_results": path_results,
        "semantic_signatures": signatures,
        "included_paths_for_consistency": included_paths,
        "excluded_path_observations": excluded_observations,
        "path_consistent": path_consistent,
        "expected_ok": expected_ok,
        "receipt_ok": receipt_ok,
        "passed": passed,
    }


def run_eval(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = str(int(time.time()))
    records = [evaluate_case(base_url, run_id, case) for case in CASES]
    return {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "base_url": base_url,
            "case_count": len(records),
            "path_count": len(PATHS),
            "evaluated_path_decisions": len(records) * len(PATHS),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
            "all_path_consistent": all(record["path_consistent"] for record in records),
            "all_receipted": all(record["receipt_ok"] for record in records),
            "excluded_path_observation_count": sum(
                len(record.get("excluded_path_observations") or {}) for record in records
            ),
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
    payload = run_eval(args.base_url)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    output_path = output_dir / f"path_consistency_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
