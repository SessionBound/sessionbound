#!/usr/bin/env python3
"""Evaluate a functionally equivalent external-PEP baseline.

The baseline deliberately composes the same primitives as SessionBound without
using the SessionBound binder: RLS/safe views, a short-lived login, an external
signature/token PEP, Python-side tuple budgeting, and an audit procedure.  The
last case bypasses the PEP to make the trust-boundary trade-off explicit.
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

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me")


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


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return "unknown"


def external_pep(
    conn: psycopg.Connection,
    payload_text: str,
    signature: str,
    credential: dict[str, Any],
    sql: str,
) -> dict[str, Any]:
    payload = json.loads(payload_text)
    reasons: list[str] = []
    if not hmac.compare_digest(sign(payload_text), signature):
        reasons.append("invalid signature")
    if payload.get("credential_id") != credential.get("credential_id"):
        reasons.append("credential-token mismatch")
    if payload.get("audience") != "sessionbounddb":
        reasons.append("wrong audience")
    if not sql.lstrip().lower().startswith("select"):
        reasons.append("operation denied")
    if "app_data" in sql.lower() or "salary" in sql.lower() or "bank_account" in sql.lower():
        reasons.append("safe-surface or denied-column violation")
    if reasons:
        return {"decision": "denied", "reason": "; ".join(reasons), "rows": 0}

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        max_rows = int(payload.get("budgets", {}).get("max_unique_expense_rows", 0))
        if len(rows) > max_rows:
            cur.execute(
                "SELECT tdsc_rls_audit.log_query(%s, %s, %s, %s)",
                (payload.get("actor", ""), payload.get("task_id", ""), sql, 0),
            )
            conn.commit()
            return {"decision": "denied", "reason": "external PEP tuple budget exceeded", "rows": 0}
        cur.execute(
            "SELECT tdsc_rls_audit.log_query(%s, %s, %s, %s)",
            (payload.get("actor", ""), payload.get("task_id", ""), sql, len(rows)),
        )
        conn.commit()
        return {"decision": "allowed", "reason": "", "rows": len(rows)}


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
            "max_rows": 2,
            "max_queries": 5,
        },
    )
    payload_text, signature = task["payload_text"], task["signature"]
    cases: list[dict[str, Any]] = []
    with psycopg.connect(dsn) as conn:
        cases.append({"id": "EQ01", "name": "external_pep_allows_bound_query", "result": external_pep(conn, payload_text, signature, credential, "SELECT amount FROM tdsc_rls_safe_view.expenses LIMIT 2")})
        cases.append({"id": "EQ02", "name": "external_pep_charges_projection_independent_tuple_budget", "result": external_pep(conn, payload_text, signature, credential, "SELECT amount AS renamed_amount FROM tdsc_rls_safe_view.expenses LIMIT 3")})
        cases.append({"id": "EQ03", "name": "external_pep_denies_credential_replay", "result": external_pep(conn, payload_text, signature, {**credential, "credential_id": "wrong"}, "SELECT amount FROM tdsc_rls_safe_view.expenses LIMIT 1")})
        with conn.cursor() as cur:
            cur.execute("SELECT amount FROM tdsc_rls_safe_view.expenses LIMIT 3")
            bypass_rows = len(cur.fetchall())
        cases.append({"id": "EQ04", "name": "direct_role_bypass_is_outside_external_pep", "result": {"decision": "allowed", "rows": bypass_rows, "reason": "baseline role can bypass PEP"}})

    passed = (
        cases[0]["result"]["decision"] == "allowed"
        and cases[1]["result"]["decision"] == "denied"
        and cases[2]["result"]["decision"] == "denied"
        and cases[3]["result"]["rows"] > 2
    )
    return {
        "run": {"commit": git_commit(), "timestamp": datetime.now(timezone.utc).isoformat(), "case_count": len(cases), "passed": 4 if passed else 0, "failed": 0 if passed else 4, "dsn": dsn.replace("tdsc_rls_safe_audit_pass", "***")},
        "cases": cases,
        "interpretation": "The composition is functionally equivalent only while every request traverses the external PEP; the direct-role bypass is the explicit TCB distinction from database-resident SessionBound enforcement.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dsn", default=baseline_dsn())
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()
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
