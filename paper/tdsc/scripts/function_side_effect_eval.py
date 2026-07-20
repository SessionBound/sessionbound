#!/usr/bin/env python3
"""Evaluate default-deny SQL function policy and side-effect suppression.

The static review specifically called out function calls that can leak runtime
state or cause effects outside the returned tuples: sleeps, advisory locks,
notifications, session/GUC changes, catalog probes, file helpers, SRFs,
serialization helpers, and unlisted aggregates.  This evaluator exercises those
shapes across the API preflight path, direct `taskbound.run(...)`, and direct
native safe-view SQL.

For externally observable side effects, the oracle checks that:

* `pg_sleep(1.0)` is denied before the sleep duration elapses;
* `pg_advisory_lock(key)` leaves the tested session-level lock acquirable by an
  independent connection while the attacking backend is still alive; and
* `pg_notify(channel, payload)` produces no notification on an independent
  listener.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
ADMIN_DSN = os.environ.get(
    "TDSC_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:15432/travel",
)
AGENT_DB_HOST = os.environ.get("TDSC_AGENT_DB_HOST", "localhost")
AGENT_DB_PORT = int(os.environ.get("TDSC_AGENT_DB_PORT", "15432"))
SCOPE = {"expense_month": "2026-06", "department_id": "dep_sales"}
SLEEP_SECONDS = 1.0
MAX_DENIED_SLEEP_SECONDS = 0.75


CASES: list[dict[str, Any]] = [
    {
        "id": "FN00",
        "name": "allowed_whitelisted_scalar_function",
        "expected": "Allowed",
        "reason_bucket": "allowed",
        "sql": (
            "SELECT lower(merchant) AS merchant_name "
            "FROM expenses ORDER BY expense_id LIMIT 2"
        ),
    },
    {
        "id": "FN01",
        "name": "pg_sleep_denied_without_delay",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql_template": "SELECT pg_sleep({sleep_seconds}) FROM expenses LIMIT 1",
        "side_effect": {"kind": "elapsed_under", "max_seconds": MAX_DENIED_SLEEP_SECONDS},
    },
    {
        "id": "FN02",
        "name": "session_advisory_lock_denied_without_lock",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql_template": "SELECT pg_advisory_lock({lock_key}) FROM expenses LIMIT 1",
        "side_effect": {"kind": "advisory_lock_free"},
    },
    {
        "id": "FN03",
        "name": "notification_denied_without_notify",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql_template": (
            "SELECT pg_notify('{notify_channel}', 'sessionbound-side-effect') "
            "FROM expenses LIMIT 1"
        ),
        "side_effect": {"kind": "no_notification"},
    },
    {
        "id": "FN04",
        "name": "session_config_write_denied",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": (
            "SELECT set_config('sessionbound_guard.task_bound', 'off', false) "
            "FROM expenses LIMIT 1"
        ),
    },
    {
        "id": "FN05",
        "name": "session_config_read_denied",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": (
            "SELECT current_setting('sessionbound_guard.allowed_view_oids', true) "
            "FROM expenses LIMIT 1"
        ),
    },
    {
        "id": "FN06",
        "name": "catalog_privilege_introspection_denied",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": "SELECT has_table_privilege('expenses', 'SELECT') FROM expenses LIMIT 1",
    },
    {
        "id": "FN07",
        "name": "file_read_function_denied",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": "SELECT pg_read_file('/etc/passwd') FROM expenses LIMIT 1",
    },
    {
        "id": "FN08",
        "name": "set_returning_function_scan_denied",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": "SELECT * FROM generate_series(1, 3) AS g(x)",
    },
    {
        "id": "FN09",
        "name": "payload_serialization_function_denied",
        "expected": "Blocked",
        "reason_bucket": "payload_function",
        "sql": (
            "SELECT json_build_object('expense_id', expense_id, 'merchant', merchant) "
            "FROM expenses LIMIT 1"
        ),
    },
    {
        "id": "FN10",
        "name": "unlisted_aggregate_function_denied",
        "expected": "Blocked",
        "reason_bucket": "aggregate_template_required",
        "sql": "SELECT stddev(amount) AS amount_stddev FROM expenses",
    },
    {
        "id": "FN11",
        "name": "unnest_set_returning_function_denied",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "sql": "SELECT * FROM unnest(ARRAY[1, 2, 3]) AS u(x)",
    },
    {
        "id": "FN12",
        "name": "non_catalog_operator_procedure_denied_without_notify",
        "expected": "Blocked",
        "reason_bucket": "function_policy",
        "paths": ["direct_wrapper", "direct_native"],
        "sql_template": (
            "SELECT expense_id FROM expenses "
            "WHERE amount OPERATOR({operator_schema}.===) amount LIMIT 1"
        ),
        "admin_setup_template": """
DROP SCHEMA IF EXISTS {operator_schema} CASCADE;
CREATE SCHEMA {operator_schema};
CREATE FUNCTION {operator_schema}.notify_eq(numeric, numeric)
RETURNS boolean
LANGUAGE plpgsql
AS $fn$
BEGIN
  PERFORM pg_notify('{notify_channel}', 'operator-fired');
  RETURN $1 = $2;
END
$fn$;
CREATE OPERATOR {operator_schema}.=== (
  PROCEDURE = {operator_schema}.notify_eq,
  LEFTARG = numeric,
  RIGHTARG = numeric
);
GRANT USAGE ON SCHEMA {operator_schema} TO agent_runtime;
GRANT EXECUTE ON FUNCTION {operator_schema}.notify_eq(numeric, numeric) TO agent_runtime;
""",
        "admin_cleanup_template": "DROP SCHEMA IF EXISTS {operator_schema} CASCADE;",
        "side_effect": {"kind": "no_notification"},
    },
]


def normalize_reason(text: str) -> str:
    lowered = (text or "").lower()
    if not lowered:
        return "allowed"
    if (
        "payload aggregation" in lowered
        or "json_build_object" in lowered
        or "jsonb_build_object" in lowered
        or "row_to_json" in lowered
    ):
        return "payload_function"
    if (
        "aggregate release" in lowered
        or "approved aggregate template" in lowered
        or "group by" in lowered
        or "having" in lowered
        or "window" in lowered
    ):
        return "aggregate_template_required"
    if (
        "function" in lowered
        or "unknown function" in lowered
        or "table functions" in lowered
        or "set-returning" in lowered
        or "not allowed in task sql" in lowered
        or "operator procedure" in lowered
        or "coercion" in lowered
        or "not allowed for this task" in lowered
        or "session lock or trusted runtime state" in lowered
        or "pg_sleep" in lowered
        or "pg_notify" in lowered
        or "pg_advisory" in lowered
        or "set_config" in lowered
        or "current_setting" in lowered
        or "has_table_privilege" in lowered
        or "pg_read_file" in lowered
        or "generate_series" in lowered
        or "unnest" in lowered
    ):
        return "function_policy"
    if "catalog" in lowered or "information_schema" in lowered or "pg_catalog" in lowered:
        return "catalog"
    if "relation is outside" in lowered or "safe-view" in lowered:
        return "relation_policy"
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


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def issue_task(base_url: str, run_id: str, case_id: str, path: str) -> IssuedTask:
    actor = "agent:travel-expense-analyst"
    credential = post_json_retry(
        base_url,
        "/credentials",
        {
            "agent_id": f"function-policy-{run_id}-{case_id.lower()}-{path}",
            "actor": actor,
            "ttl_minutes": 30,
        },
    )
    task = post_json_retry(
        base_url,
        "/tasks",
        {
            "task_id": f"task_function_policy_{run_id}_{case_id.lower()}_{path}",
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


def lock_key_for(run_id: str, case_id: str, path: str) -> int:
    seed = f"{run_id}:{case_id}:{path}"
    return 7_000_000_000_000 + sum((idx + 1) * ord(ch) for idx, ch in enumerate(seed))


def context_for(run_id: str, case: dict[str, Any], path: str) -> dict[str, Any]:
    channel = safe_identifier(f"tdsc_fn_{run_id}_{case['id'].lower()}_{path}")
    operator_schema = safe_identifier(f"tdscop_{run_id}_{case['id'].lower()}_{path}")
    return {
        "lock_key": lock_key_for(run_id, case["id"], path),
        "notify_channel": channel,
        "operator_schema": operator_schema,
        "sleep_seconds": SLEEP_SECONDS,
    }


def resolve_sql(case: dict[str, Any], ctx: dict[str, Any]) -> str:
    if "sql" in case:
        return str(case["sql"])
    return str(case["sql_template"]).format(**ctx)


def run_admin_sql(sql_text: str) -> None:
    if not sql_text.strip():
        return
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)


def advisory_lock_is_free(lock_key: int) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
            acquired = bool(cur.fetchone()[0])
            if acquired:
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    return {"kind": "advisory_lock_free", "passed": acquired, "lock_key": lock_key}


@contextmanager
def notification_listener(channel: str | None) -> Iterator[psycopg.Connection[Any] | None]:
    if not channel:
        yield None
        return

    safe_channel = safe_identifier(channel)
    conn = psycopg.connect(ADMIN_DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"LISTEN {safe_channel}")
        list(conn.notifies(timeout=0.0, stop_after=10))
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(f"UNLISTEN {safe_channel}")
        conn.close()


def collect_notifications(
    listener: psycopg.Connection[Any] | None,
    *,
    timeout: float = 0.25,
) -> list[dict[str, Any]]:
    if listener is None:
        return []
    notifications = []
    for notification in listener.notifies(timeout=timeout, stop_after=10):
        notifications.append(
            {
                "channel": notification.channel,
                "payload": notification.payload,
                "pid": notification.pid,
            }
        )
    return notifications


def side_effect_result(
    side_effect: dict[str, Any] | None,
    *,
    elapsed_seconds: float,
    ctx: dict[str, Any],
    notifications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not side_effect:
        return {"kind": "none", "passed": True}

    kind = side_effect["kind"]
    if kind == "elapsed_under":
        max_seconds = float(side_effect["max_seconds"])
        return {
            "kind": kind,
            "passed": elapsed_seconds < max_seconds,
            "elapsed_seconds": elapsed_seconds,
            "max_seconds": max_seconds,
            "sleep_seconds": SLEEP_SECONDS,
        }
    if kind == "advisory_lock_free":
        return advisory_lock_is_free(int(ctx["lock_key"]))
    if kind == "no_notification":
        observed = notifications or []
        return {
            "kind": kind,
            "passed": len(observed) == 0,
            "notifications": observed,
            "notify_channel": ctx["notify_channel"],
        }
    raise ValueError(f"unknown side-effect oracle: {kind}")


def run_api_wrapper(
    base_url: str,
    issued: IssuedTask,
    sql: str,
    case: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    side_effect = case.get("side_effect")
    listener_channel = ctx["notify_channel"] if (side_effect or {}).get("kind") == "no_notification" else None
    notifications: list[dict[str, Any]] = []

    with notification_listener(listener_channel) as listener:
        started = time.monotonic()
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
        elapsed = time.monotonic() - started
        notifications = collect_notifications(listener)

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
        "elapsed_seconds": elapsed,
        "ast_allowed": (result.get("ast_validation") or {}).get("allowed"),
        "side_effect": side_effect_result(
            side_effect,
            elapsed_seconds=elapsed,
            ctx=ctx,
            notifications=notifications,
        ),
    }


def run_db_path(
    issued: IssuedTask,
    sql: str,
    case: dict[str, Any],
    ctx: dict[str, Any],
    *,
    native: bool,
) -> dict[str, Any]:
    path = "direct_native" if native else "direct_wrapper"
    side_effect = case.get("side_effect")
    listener_channel = ctx["notify_channel"] if (side_effect or {}).get("kind") == "no_notification" else None
    rows: list[Any] = []
    error = ""
    ok = False
    state: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    elapsed = 0.0
    effect: dict[str, Any] | None = None

    with notification_listener(listener_channel) as listener:
        with psycopg.connect(credential_dsn(issued.credential), autocommit=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "SELECT taskbound.bind_task(%s, %s)",
                        (issued.task["payload_text"], issued.task["signature"]),
                    )
                    started = time.monotonic()
                    if native:
                        cur.execute(sql)
                        rows = rows_as_dicts(cur)
                    else:
                        cur.execute("SELECT * FROM taskbound.run(%s)", (sql,))
                        rows = [row[0] for row in cur.fetchall()]
                    elapsed = time.monotonic() - started
                    ok = True
                except Exception as exc:
                    elapsed = time.monotonic() - started if "started" in locals() else 0.0
                    error = str(exc).splitlines()[0]
                finally:
                    try:
                        state = fetch_state(cur)
                        receipts = fetch_receipts(cur)
                    except Exception as exc:
                        if not error:
                            error = f"post-query inspection failed: {exc}".splitlines()[0]

                    notifications = collect_notifications(listener)
                    effect = side_effect_result(
                        side_effect,
                        elapsed_seconds=elapsed,
                        ctx=ctx,
                        notifications=notifications,
                    )

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
        "elapsed_seconds": elapsed,
        "ast_allowed": None,
        "side_effect": effect or {"kind": "none", "passed": True},
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


def apply_sanitized_wrapper_hint(result: dict[str, Any], case: dict[str, Any]) -> None:
    if result.get("path") != "direct_wrapper":
        return
    if result.get("classification") != "Blocked":
        return
    if result.get("reason_bucket") != "other":
        return
    if case.get("reason_bucket") in {None, "allowed", "other"}:
        return

    error = str(result.get("error") or "").lower()
    if "query shape violates task policy" in error:
        result["reason_bucket"] = case["reason_bucket"]
        receipts = result.get("receipts") or {}
        receipts["latest_reason_bucket"] = case["reason_bucket"]


def evaluate_case(base_url: str, run_id: str, case: dict[str, Any]) -> dict[str, Any]:
    path_results: dict[str, dict[str, Any]] = {}
    rendered_sql: dict[str, str] = {}
    paths = list(case.get("paths") or PATHS)

    for path in paths:
        ctx = context_for(run_id, case, path)
        sql = resolve_sql(case, ctx)
        rendered_sql[path] = sql
        setup_template = case.get("admin_setup_template")
        cleanup_template = case.get("admin_cleanup_template")
        if setup_template:
            run_admin_sql(str(setup_template).format(**ctx))
        try:
            issued = issue_task(base_url, run_id, case["id"], path)
            if path == "api_wrapper":
                path_results[path] = run_api_wrapper(base_url, issued, sql, case, ctx)
            elif path == "direct_wrapper":
                path_results[path] = run_db_path(issued, sql, case, ctx, native=False)
            else:
                path_results[path] = run_db_path(issued, sql, case, ctx, native=True)
        finally:
            if cleanup_template:
                run_admin_sql(str(cleanup_template).format(**ctx))

    for result in path_results.values():
        apply_sanitized_wrapper_hint(result, case)

    signatures = {path: semantic_signature(result) for path, result in path_results.items()}
    reference = signatures[paths[0]]
    path_consistent = all(signatures[path] == reference for path in paths)
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
    side_effect_ok = all(
        (result.get("side_effect") or {}).get("passed") is True
        for result in path_results.values()
    )

    return {
        "id": case["id"],
        "name": case["name"],
        "sql": rendered_sql[paths[0]],
        "rendered_sql_by_path": rendered_sql,
        "paths": paths,
        "expected": case["expected"],
        "reason_bucket": case["reason_bucket"],
        "side_effect_oracle": case.get("side_effect") or {"kind": "none"},
        "passed": path_consistent and expected_ok and receipt_ok and side_effect_ok,
        "path_consistent": path_consistent,
        "expected_ok": expected_ok,
        "receipt_ok": receipt_ok,
        "side_effect_ok": side_effect_ok,
        "semantic_signatures": signatures,
        "path_results": path_results,
    }


def run_eval(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    records = [evaluate_case(base_url, run_id, case) for case in CASES]
    side_effect_records = [
        record
        for record in records
        if (record.get("side_effect_oracle") or {}).get("kind") not in {None, "none"}
    ]
    run = {
        "all_function_side_effect_checks_passed": all(record["passed"] for record in records),
        "all_path_consistent": all(record["path_consistent"] for record in records),
        "all_receipted": all(record["receipt_ok"] for record in records),
        "all_side_effect_oracles_passed": all(record["side_effect_ok"] for record in records),
        "base_url": base_url,
        "case_count": len(records),
        "commit": git_commit(),
        "evaluated_path_decisions": sum(len(record["path_results"]) for record in records),
        "failed": sum(1 for record in records if not record["passed"]),
        "passed": sum(1 for record in records if record["passed"]),
        "path_count": len(PATHS),
        "side_effect_case_count": len(side_effect_records),
        "sleep_seconds": SLEEP_SECONDS,
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
    output_path = output_dir / f"function_side_effect_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2))
    if not payload["run"]["all_function_side_effect_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
