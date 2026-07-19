#!/usr/bin/env python3
"""Evaluate the native-feeling TaskboundSession.query(sql) SDK surface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "app"))

from taskbound_demo import default_task  # noqa: E402
from taskbound_sdk import TaskboundSession  # noqa: E402


DEFAULT_DSN = "postgresql://agent_app:agentpass@postgres:5432/travel"


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


def bare_select_attempt(conn) -> dict[str, Any]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT expense_id, amount FROM taskbound.expenses LIMIT 1")
            rows = cur.fetchall()
        return {"ok": True, "rows": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate(dsn: str) -> dict[str, Any]:
    task_id = f"task_sdk_query_{int(time.time())}"
    payload_text, signature = default_task(task_id=task_id, max_queries=5, max_rows=5)

    with psycopg.connect(dsn, autocommit=True) as conn:
        session = TaskboundSession(conn=conn)
        bound = session.bind_task(payload_text, signature)
        rows = session.query(
            "SELECT expense_id, amount "
            "FROM expenses "
            "ORDER BY amount DESC "
            "LIMIT 2"
        )
        state = session.inspect_state()
        receipts = session.receipts(limit=5)
        native_bare_select = bare_select_attempt(conn)
        native_state = session.inspect_state()
        native_receipts = session.receipts(limit=5)
        session.unbind_task()
        unbound_bare_select = bare_select_attempt(conn)

    sdk_receipt_ok = any(
        receipt.get("decision") == "allowed"
        and receipt.get("rows_returned") == 2
        and "expenses" in receipt.get("touched_views", [])
        for receipt in receipts
    )
    sdk_state_ok = bool(state) and state[0].get("query_count") == 1
    sdk_case = {
        "id": "SDK01",
        "name": "sdk_query_uses_native_select",
        "expected": "Allowed",
        "actual": "Allowed" if rows and sdk_receipt_ok and sdk_state_ok else "Unexpected",
        "passed": bool(rows) and len(rows) == 2 and sdk_receipt_ok and sdk_state_ok,
        "evidence": {
            "bound": bound,
            "rows": rows,
            "state": state,
            "receipts": receipts,
        },
    }
    native_bare_case = {
        "id": "SDK02",
        "name": "bound_bare_safe_view_select_is_native_accounted",
        "expected": "Allowed",
        "actual": "Allowed" if native_bare_select["ok"] else "Blocked",
        "passed": (
            native_bare_select["ok"]
            and bool(native_state)
            and native_state[0].get("query_count") == 2
            and any(
                receipt.get("decision") == "allowed"
                and "expenses" in receipt.get("touched_views", [])
                for receipt in native_receipts
            )
        ),
        "evidence": {
            "select": native_bare_select,
            "state": native_state,
            "receipts": native_receipts,
        },
    }
    unbound_bare_case = {
        "id": "SDK03",
        "name": "unbound_safe_view_select_fails_closed",
        "expected": "Blocked",
        "actual": "Blocked" if not unbound_bare_select["ok"] else "Allowed",
        "passed": (not unbound_bare_select["ok"]) and (
            "no trusted task binding" in unbound_bare_select.get("error", "")
            or "permission denied" in unbound_bare_select.get("error", "")
        ),
        "evidence": unbound_bare_select,
    }
    records = [sdk_case, native_bare_case, unbound_bare_case]
    passed = sum(1 for record in records if record["passed"])
    return {
        "records": records,
        "run": {
            "case_count": len(records),
            "passed": passed,
            "failed": len(records) - passed,
            "database_url": dsn.replace(":agentpass@", ":***@"),
            "commit": git_commit(),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate TaskboundSession.query(sql)")
    parser.add_argument("--database-url", default=os.environ.get("SDK_EVAL_DATABASE_URL", DEFAULT_DSN))
    parser.add_argument("--output-dir", default="paper/tdsc/raw_results")
    args = parser.parse_args()

    result = evaluate(args.database_url)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"sdk_query_{stamp}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")

    print(output_path)
    print(json.dumps(result["run"], ensure_ascii=False, indent=2))
    if result["run"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
