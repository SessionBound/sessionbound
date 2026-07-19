#!/usr/bin/env python3
"""Evaluate a functionally equivalent external-PEP baseline.

The baseline deliberately composes the same primitives as SessionBound without
using the SessionBound binder: RLS/safe views, a short-lived login, an external
signature/credential PEP, cumulative tuple/query budgeting, and a PEP-owned
receipt chain.  A final direct-role case makes the trust-boundary trade-off
explicit: the composition is equivalent only when every request traverses this
external PEP.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me")
ADMIN_DSN = os.environ.get("TDSC_ADMIN_DSN", "postgresql://postgres:postgres@localhost:15432/travel")
SQL_DIR = REPO_ROOT / "paper/tdsc/experiments/sql"


def post_json(base_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def sign(payload_text: str) -> str:
    return hmac.new(SECRET.encode(), payload_text.encode(), hashlib.sha256).hexdigest()


def baseline_dsn() -> str:
    return os.environ.get(
        "TDSC_BASELINE_DSN",
        "postgresql://tdsc_rls_safe_audit:tdsc_rls_safe_audit_pass@localhost:15432/travel",
    )


def setup_baseline() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            for path in [
                SQL_DIR / "role_only_baseline.sql",
                SQL_DIR / "safe_view_only_baseline.sql",
                SQL_DIR / "rls_safe_view_short_credential_audit_baseline.sql",
            ]:
                cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(
                """
                CREATE SCHEMA IF NOT EXISTS tdsc_external_pep;
                DROP TABLE IF EXISTS tdsc_external_pep.receipts;
                DROP TABLE IF EXISTS tdsc_external_pep.sessions;

                CREATE TABLE tdsc_external_pep.sessions (
                  task_id text PRIMARY KEY,
                  budget_account text NOT NULL,
                  payload_digest text NOT NULL,
                  actor text NOT NULL,
                  credential_id text NOT NULL,
                  audience text NOT NULL,
                  max_queries int NOT NULL,
                  max_rows int NOT NULL,
                  query_count int NOT NULL DEFAULT 0,
                  returned_rows bigint NOT NULL DEFAULT 0,
                  previous_receipt_hash text,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  updated_at timestamptz NOT NULL DEFAULT now()
                );

                CREATE TABLE tdsc_external_pep.receipts (
                  receipt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  execution_id uuid NOT NULL,
                  task_id text NOT NULL REFERENCES tdsc_external_pep.sessions(task_id)
                    ON DELETE CASCADE,
                  budget_account text NOT NULL,
                  actor text NOT NULL,
                  decision text NOT NULL CHECK (decision IN ('allowed', 'denied')),
                  reason text NOT NULL DEFAULT '',
                  query_digest text NOT NULL,
                  rows_returned bigint NOT NULL DEFAULT 0,
                  unique_rows_added bigint NOT NULL DEFAULT 0,
                  remaining_unique_row_budget bigint NOT NULL,
                  touched_views jsonb NOT NULL DEFAULT '[]'::jsonb,
                  created_at text NOT NULL,
                  previous_receipt_hash text,
                  receipt_hash text NOT NULL,
                  CONSTRAINT external_pep_task_execution_once UNIQUE (task_id, execution_id)
                );
                """
            )


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return "unknown"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def touched_views(sql: str) -> list[str]:
    lowered = sql.lower()
    views = []
    for view in ["expenses", "employees", "departments", "approval_events", "ledger_entries"]:
        if f"tdsc_rls_safe_view.{view}" in lowered:
            views.append(view)
    return views


def validate_request(
    payload: dict[str, Any],
    payload_text: str,
    signature: str,
    credential: dict[str, Any],
    sql: str,
) -> list[str]:
    reasons: list[str] = []
    if not hmac.compare_digest(sign(payload_text), signature):
        reasons.append("invalid signature")
    if payload.get("credential_id") != credential.get("credential_id"):
        reasons.append("credential-token mismatch")
    if payload.get("audience") != "sessionbounddb":
        reasons.append("wrong audience")
    if not sql.lstrip().lower().startswith("select"):
        reasons.append("operation denied")
    lowered = sql.lower()
    if "app_data" in lowered or "pg_catalog" in lowered or "information_schema" in lowered:
        reasons.append("safe-surface violation")
    if any(name in lowered for name in ["salary", "bank_account", "phone"]):
        reasons.append("safe-surface or denied-column violation")
    if "tdsc_rls_safe_view." not in lowered:
        reasons.append("unapproved safe-view surface")
    return reasons


def register_external_pep_session(payload: dict[str, Any], payload_text: str) -> None:
    task_id = str(payload["task_id"])
    budget_account = str(payload.get("budget_account") or task_id)
    budgets = payload.get("budgets") or {}
    max_queries = safe_int(budgets.get("max_queries"), 100)
    max_rows = safe_int(budgets.get("max_unique_expense_rows"), 1000000)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tdsc_external_pep.sessions (
                  task_id, budget_account, payload_digest, actor, credential_id,
                  audience, max_queries, max_rows
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (task_id) DO UPDATE
                SET budget_account = EXCLUDED.budget_account,
                    payload_digest = EXCLUDED.payload_digest,
                    actor = EXCLUDED.actor,
                    credential_id = EXCLUDED.credential_id,
                    audience = EXCLUDED.audience,
                    max_queries = EXCLUDED.max_queries,
                    max_rows = EXCLUDED.max_rows,
                    query_count = 0,
                    returned_rows = 0,
                    previous_receipt_hash = NULL,
                    updated_at = now()
                """,
                (
                    task_id,
                    budget_account,
                    sha256_text(payload_text),
                    str(payload.get("actor") or ""),
                    str(payload.get("credential_id") or ""),
                    str(payload.get("audience") or ""),
                    max_queries,
                    max_rows,
                ),
            )


def current_external_state(task_id: str) -> dict[str, Any]:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_id, budget_account, actor, credential_id, max_queries,
                       max_rows, query_count, returned_rows, previous_receipt_hash
                FROM tdsc_external_pep.sessions
                WHERE task_id = %s
                """,
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                return {}
            names = [d.name for d in cur.description]
            return dict(zip(names, row))


def external_receipts(task_id: str) -> list[dict[str, Any]]:
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT execution_id::text, task_id, budget_account, actor, decision,
                       reason, query_digest, rows_returned, unique_rows_added,
                       remaining_unique_row_budget, touched_views, created_at::text,
                       previous_receipt_hash, receipt_hash
                FROM tdsc_external_pep.receipts
                WHERE task_id = %s
                ORDER BY created_at, receipt_id
                """,
                (task_id,),
            )
            names = [d.name for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]


def receipt_hash(material: dict[str, Any]) -> str:
    return sha256_text(json.dumps(material, sort_keys=True, separators=(",", ":"), default=str))


def append_external_decision(
    payload: dict[str, Any],
    sql: str,
    *,
    execution_id: str,
    decision: str,
    reason: str = "",
    rows_returned: int = 0,
    rows_to_add: int = 0,
    view_names: list[str] | None = None,
) -> dict[str, Any]:
    task_id = str(payload["task_id"])
    query_digest = sha256_text(sql)
    view_names = view_names or []
    created_at = datetime.now(timezone.utc).isoformat()

    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT execution_id::text, decision, reason, rows_returned,
                       unique_rows_added, remaining_unique_row_budget, receipt_hash
                FROM tdsc_external_pep.receipts
                WHERE task_id = %s AND execution_id = %s::uuid
                """,
                (task_id, execution_id),
            )
            duplicate = cur.fetchone()
            if duplicate is not None:
                conn.commit()
                return {
                    "decision": duplicate[1],
                    "reason": duplicate[2],
                    "rows": duplicate[3],
                    "receipt": {
                        "execution_id": duplicate[0],
                        "decision": duplicate[1],
                        "reason": duplicate[2],
                        "rows_returned": duplicate[3],
                        "unique_rows_added": duplicate[4],
                        "remaining_unique_row_budget": duplicate[5],
                        "receipt_hash": duplicate[6],
                    },
                    "duplicate_execution_id": True,
                    "state": current_external_state(task_id),
                }

            cur.execute(
                """
                SELECT task_id, budget_account, actor, max_queries, max_rows,
                       query_count, returned_rows, previous_receipt_hash
                FROM tdsc_external_pep.sessions
                WHERE task_id = %s
                FOR UPDATE
                """,
                (task_id,),
            )
            session_row = cur.fetchone()
            if session_row is None:
                raise RuntimeError(f"external PEP session missing for {task_id}")
            (
                _task_id,
                budget_account,
                actor,
                max_queries,
                max_rows,
                query_count,
                returned_rows,
                previous_hash,
            ) = session_row

            effective_decision = decision
            effective_reason = reason
            effective_rows_returned = rows_returned
            effective_rows_to_add = rows_to_add

            if decision == "allowed":
                if query_count + 1 > max_queries:
                    effective_decision = "denied"
                    effective_reason = "external PEP query budget exceeded"
                    effective_rows_returned = 0
                    effective_rows_to_add = 0
                elif returned_rows + rows_to_add > max_rows:
                    effective_decision = "denied"
                    effective_reason = "external PEP cumulative tuple budget exceeded"
                    effective_rows_returned = 0
                    effective_rows_to_add = 0

            remaining_after = max_rows - (returned_rows + effective_rows_to_add)
            material = {
                "execution_id": execution_id,
                "task_id": task_id,
                "budget_account": budget_account,
                "actor": actor,
                "decision": effective_decision,
                "reason": effective_reason,
                "query_digest": query_digest,
                "rows_returned": effective_rows_returned,
                "unique_rows_added": effective_rows_to_add,
                "remaining_unique_row_budget": remaining_after,
                "touched_views": sorted(view_names),
                "created_at": created_at,
                "previous_receipt_hash": previous_hash,
            }
            current_hash = receipt_hash(material)

            if effective_decision == "allowed":
                cur.execute(
                    """
                    UPDATE tdsc_external_pep.sessions
                    SET query_count = query_count + 1,
                        returned_rows = returned_rows + %s,
                        previous_receipt_hash = %s,
                        updated_at = now()
                    WHERE task_id = %s
                    """,
                    (effective_rows_to_add, current_hash, task_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE tdsc_external_pep.sessions
                    SET previous_receipt_hash = %s,
                        updated_at = now()
                    WHERE task_id = %s
                    """,
                    (current_hash, task_id),
                )

            cur.execute(
                """
                INSERT INTO tdsc_external_pep.receipts (
                  execution_id, task_id, budget_account, actor, decision, reason,
                  query_digest, rows_returned, unique_rows_added,
                  remaining_unique_row_budget, touched_views, created_at,
                  previous_receipt_hash, receipt_hash
                )
                VALUES (
                  %s::uuid, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s::jsonb, %s,
                  %s, %s
                )
                """,
                (
                    execution_id,
                    task_id,
                    budget_account,
                    actor,
                    effective_decision,
                    effective_reason,
                    query_digest,
                    effective_rows_returned,
                    effective_rows_to_add,
                    remaining_after,
                    json.dumps(sorted(view_names)),
                    created_at,
                    previous_hash,
                    current_hash,
                ),
            )
            conn.commit()

    return {
        "decision": effective_decision,
        "reason": effective_reason,
        "rows": effective_rows_returned,
        "receipt": {
            "execution_id": execution_id,
            "decision": effective_decision,
            "reason": effective_reason,
            "rows_returned": effective_rows_returned,
            "unique_rows_added": effective_rows_to_add,
            "remaining_unique_row_budget": remaining_after,
            "touched_views": sorted(view_names),
            "previous_receipt_hash": previous_hash,
            "receipt_hash": current_hash,
        },
        "duplicate_execution_id": False,
        "state": current_external_state(task_id),
    }


def external_pep(
    conn: psycopg.Connection,
    payload_text: str,
    signature: str,
    credential: dict[str, Any],
    sql: str,
    *,
    execution_id: str | None = None,
) -> dict[str, Any]:
    payload = json.loads(payload_text)
    execution_id = execution_id or str(uuid4())
    view_names = touched_views(sql)
    reasons = validate_request(payload, payload_text, signature, credential, sql)
    if reasons:
        return append_external_decision(
            payload,
            sql,
            execution_id=execution_id,
            decision="denied",
            reason="; ".join(reasons),
            view_names=view_names,
        )

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    decision = append_external_decision(
        payload,
        sql,
        execution_id=execution_id,
        decision="allowed",
        rows_returned=len(rows),
        rows_to_add=len(rows),
        view_names=view_names,
    )
    if decision["decision"] == "allowed":
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tdsc_rls_audit.log_query(%s, %s, %s, %s)",
                (payload.get("actor", ""), payload.get("task_id", ""), sql, len(rows)),
            )
            conn.commit()
    return decision


def verify_receipt_chain(receipts: list[dict[str, Any]]) -> bool:
    previous = None
    for receipt in receipts:
        if receipt["previous_receipt_hash"] != previous:
            return False
        material = {
            "execution_id": receipt["execution_id"],
            "task_id": receipt["task_id"],
            "budget_account": receipt["budget_account"],
            "actor": receipt["actor"],
            "decision": receipt["decision"],
            "reason": receipt["reason"],
            "query_digest": receipt["query_digest"],
            "rows_returned": receipt["rows_returned"],
            "unique_rows_added": receipt["unique_rows_added"],
            "remaining_unique_row_budget": receipt["remaining_unique_row_budget"],
            "touched_views": sorted(receipt["touched_views"]),
            "created_at": receipt["created_at"],
            "previous_receipt_hash": receipt["previous_receipt_hash"],
        }
        if receipt["receipt_hash"] != receipt_hash(material):
            return False
        previous = receipt["receipt_hash"]
    return True


def evaluate(base_url: str, dsn: str) -> dict[str, Any]:
    run_id = str(int(time.time()))
    credential = post_json(
        base_url,
        "/credentials",
        {"agent_id": f"equiv-baseline-{run_id}", "actor": "agent:travel-expense-analyst", "ttl_minutes": 30},
    )
    task = post_json(
        base_url,
        "/tasks",
        {
            "task_id": f"task_equiv_baseline_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential["credential_id"],
            "scope": {"expense_month": "2026-06"},
            "max_rows": 3,
            "max_queries": 2,
        },
    )
    payload_text, signature = task["payload_text"], task["signature"]
    payload = json.loads(payload_text)
    register_external_pep_session(payload, payload_text)
    cases: list[dict[str, Any]] = []
    with psycopg.connect(dsn) as conn:
        first = external_pep(conn, payload_text, signature, credential, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 2")
        cases.append({
            "id": "EQ01",
            "name": "external_pep_allows_first_query_and_charges_cumulative_state",
            "expected": "allowed, query_count=1, returned_rows=2",
            "result": first,
            "passed": first["decision"] == "allowed" and first["rows"] == 2 and first["state"].get("query_count") == 1 and first["state"].get("returned_rows") == 2,
        })

        second = external_pep(conn, payload_text, signature, credential, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 2")
        cases.append({
            "id": "EQ02",
            "name": "external_pep_denies_cumulative_tuple_budget",
            "expected": "denied because 2 prior rows + 2 staged rows exceeds max_rows=3; budget counters unchanged",
            "result": second,
            "passed": (
                second["decision"] == "denied"
                and "cumulative tuple budget exceeded" in second["reason"]
                and second["state"].get("query_count") == 1
                and second["state"].get("returned_rows") == 2
            ),
        })

        third = external_pep(conn, payload_text, signature, credential, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 1")
        cases.append({
            "id": "EQ03",
            "name": "external_pep_allows_remaining_tuple_budget",
            "expected": "allowed remaining one output tuple, reaching query_count=2 and returned_rows=3",
            "result": third,
            "passed": third["decision"] == "allowed" and third["rows"] == 1 and third["state"].get("query_count") == 2 and third["state"].get("returned_rows") == 3,
        })

        fourth = external_pep(conn, payload_text, signature, credential, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 1")
        cases.append({
            "id": "EQ04",
            "name": "external_pep_denies_cumulative_query_budget",
            "expected": "denied because two allowed queries already consumed max_queries=2",
            "result": fourth,
            "passed": (
                fourth["decision"] == "denied"
                and "query budget exceeded" in fourth["reason"]
                and fourth["state"].get("query_count") == 2
                and fourth["state"].get("returned_rows") == 3
            ),
        })

        replay = external_pep(conn, payload_text, signature, {**credential, "credential_id": "wrong"}, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 1")
        cases.append({
            "id": "EQ05",
            "name": "external_pep_denies_credential_replay_without_budget_mutation",
            "expected": "denied on credential-token mismatch, preserving cumulative counters",
            "result": replay,
            "passed": (
                replay["decision"] == "denied"
                and "credential-token mismatch" in replay["reason"]
                and replay["state"].get("query_count") == 2
                and replay["state"].get("returned_rows") == 3
            ),
        })

        duplicate_execution_id = str(uuid4())
        dup_first = external_pep(conn, payload_text, signature, credential, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 1", execution_id=duplicate_execution_id)
        dup_second = external_pep(conn, payload_text, signature, credential, "SELECT expense_id, amount FROM tdsc_rls_safe_view.expenses ORDER BY expense_id LIMIT 1", execution_id=duplicate_execution_id)
        cases.append({
            "id": "EQ06",
            "name": "external_pep_same_execution_id_is_idempotent",
            "expected": "retry returns the first decision receipt and creates no second receipt",
            "result": {"first": dup_first, "retry": dup_second},
            "passed": (
                dup_first["decision"] == "denied"
                and dup_second.get("duplicate_execution_id") is True
                and dup_first["receipt"]["receipt_hash"] == dup_second["receipt"]["receipt_hash"]
            ),
        })

        receipts_before_bypass = external_receipts(payload["task_id"])
        state_before_bypass = current_external_state(payload["task_id"])
        with conn.cursor() as cur:
            cur.execute("SELECT amount FROM tdsc_rls_safe_view.expenses LIMIT 3")
            bypass_rows = len(cur.fetchall())
        receipts_after_bypass = external_receipts(payload["task_id"])
        state_after_bypass = current_external_state(payload["task_id"])
        cases.append({
            "id": "EQ07",
            "name": "direct_role_bypass_is_outside_external_pep_tcb",
            "expected": "direct use of the baseline role can read rows without PEP state or receipt mutation",
            "result": {
                "decision": "allowed",
                "rows": bypass_rows,
                "reason": "baseline role can bypass PEP if deployment does not mediate direct database access",
                "state_before": state_before_bypass,
                "state_after": state_after_bypass,
                "receipts_before": len(receipts_before_bypass),
                "receipts_after": len(receipts_after_bypass),
            },
            "passed": bypass_rows > 0 and state_after_bypass == state_before_bypass and len(receipts_after_bypass) == len(receipts_before_bypass),
        })

    receipts = external_receipts(payload["task_id"])
    chain_ok = verify_receipt_chain(receipts)
    unique_execution_ids = len({r["execution_id"] for r in receipts}) == len(receipts)
    expected_receipt_count = 6
    cases.append({
        "id": "EQ08",
        "name": "external_pep_receipts_are_exactly_once_and_chained",
        "expected": "one authoritative receipt per PEP-evaluated execution id, including denials, with a verifiable hash chain",
        "result": {
            "receipt_count": len(receipts),
            "expected_receipt_count": expected_receipt_count,
            "chain_ok": chain_ok,
            "unique_execution_ids": unique_execution_ids,
            "receipts": receipts,
        },
        "passed": len(receipts) == expected_receipt_count and unique_execution_ids and chain_ok,
    })

    passed = sum(1 for case in cases if case.get("passed"))
    failed = len(cases) - passed
    return {
        "run": {
            "commit": git_commit(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "case_count": len(cases),
            "passed": passed,
            "failed": failed,
            "dsn": dsn.replace("tdsc_rls_safe_audit_pass", "***"),
        },
        "cases": cases,
        "interpretation": "This baseline now uses the same signed task contract, credential binding, cumulative query/tuple budget semantics, persistent state, execution ids, and chained receipt fields as the SessionBound comparison workload. Its remaining distinction is deployment TCB: direct database use of the baseline role bypasses the external PEP unless the deployment mediates all connections.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dsn", default=baseline_dsn())
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()
    setup_baseline()
    result = evaluate(args.base_url, args.dsn)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"functional_equivalent_baseline_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result["run"], indent=2, sort_keys=True))
    print(output)
    return 0 if result["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
