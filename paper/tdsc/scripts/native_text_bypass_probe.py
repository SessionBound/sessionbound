#!/usr/bin/env python3
"""Regression probe for the native accounting text-matching bypass.

Background
----------
``should_account_query`` once skipped accounting for any SELECT whose raw
source text contained a runtime-helper substring ("taskbound.native_", etc.).
That check was both redundant (direct helper calls are denied at plan-analysis
time; guard-internal SELECTs run under guard_in_internal_spi) and an exploitable
bypass: a task SELECT could exempt itself from row/query counting merely by
embedding such a substring in a block comment, string literal, or quoted alias.

The text check has been removed.  This probe verifies the bypass is closed on
the NATIVE (bare-SELECT) accounting path, which is the path the C executor hook
accounts.

Method
------
Bind a task with max_queries = 1, then in one session issue:

  Q1: a legitimate safe-view SELECT that embeds a helper substring in a comment
      (the attacker's attempted free query);
  Q2: a clean safe-view SELECT.

Native accounting is made observable via the budget.  With the fix in place, Q1
is accounted (counter 0 -> 1), so Q2 is rejected as "query budget exhausted".
Under the old text-matching bypass, Q1 was exempt (counter stays 0), so Q2 was
accounted and allowed.

Exit code 0 means the bypass is closed; non-zero means it is present.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the canonical eval harness helpers so this probe follows the exact
# credential/task minting and psql execution paths the suite uses.
from sessionbound_guard_hook_eval import (  # noqa: E402
    post_json,
    run_psql,
    sql_literal,
    wait_for_api,
)

SAFE_SELECT = "SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 1"
# Embed several former magic substrings in positions the parser ignores.
Q1_BYPASS_ATTEMPT = SAFE_SELECT + " /* taskbound.native_bypass taskbound.audit_x taskbound.run */"
Q2_CLEAN = SAFE_SELECT


def run_probe(base_url: str) -> dict[str, Any]:
    wait_for_api(base_url)
    # Unique per run so single-active-binding replay protection does not reject a
    # fresh bind when the probe is invoked repeatedly (e.g. across builds).
    run_id = f"text_bypass_probe_{int(time.time())}"
    credential = post_json(
        base_url,
        "/credentials",
        {"agent_id": f"tdsc-{run_id}", "actor": "agent:travel-expense-analyst", "ttl_minutes": 30},
    )
    task = post_json(
        base_url,
        "/tasks",
        {
            "task_id": f"task_{run_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 1,  # one native SELECT is accounted, a second is denied
        },
    )

    if not credential.get("db_user") or not task.get("payload_text"):
        return {
            "ok": False,
            "error": "credential/task setup failed",
            "credential": credential,
            "task": task,
        }

    # Single session so the binding and the per-task counter are shared.
    script = (
        f"SELECT taskbound.bind_task({sql_literal(task['payload_text'])}, {sql_literal(task['signature'])});\n"
        f"{Q1_BYPASS_ATTEMPT};\n"
        f"{Q2_CLEAN};\n"
    )
    result = run_psql(script, user=credential["db_user"], password=credential["db_password"])
    tail = result.get("output_tail", "")

    # Bypass closed  <=> Q1 was accounted, exhausting the budget so Q2 is denied.
    q2_budget_exhausted = "query budget exhausted" in (result.get("stdout", "") + result.get("stderr", ""))
    return {
        "ok": True,
        "q2_budget_exhausted": q2_budget_exhausted,
        "bypass_closed": bool(q2_budget_exhausted),
        "psql_returncode": result["returncode"],
        "output_tail": tail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()

    payload = run_probe(args.base_url)
    out = Path(args.output_dir) / "native_text_bypass_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(payload, indent=2, sort_keys=True))
    if not payload.get("ok"):
        return 2
    return 0 if payload["bypass_closed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
