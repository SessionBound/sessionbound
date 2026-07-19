#!/usr/bin/env python3
"""Historical triage probe for a fail_receipt backend-crash suspicion.

This script was used after a historical signal-11 observation around
``SELECT taskbound.fail_receipt($1, $2)`` during adversarial sweeps.  It runs
each attack through the public /agent-query endpoint one at a time and compares
the cumulative backend-crash count before and after each call, so any triggering
input can be identified without concurrency masking the cause.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adversarial_sql_eval import ATTACKS, post_json, wait_for_api  # noqa: E402


def segfault_count() -> int:
    """Cumulative number of signal-11 backend crashes in the postgres log."""
    proc = subprocess.run(
        ["docker", "compose", "logs", "postgres"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return (proc.stdout + proc.stderr).count("terminated by signal 11")


def segfault_detail() -> list[str]:
    proc = subprocess.run(
        ["docker", "compose", "logs", "--tail=40", "postgres"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    return [ln.strip() for ln in out.splitlines()
            if "signal 11" in ln or "Failed process was running" in ln]


def wait_healthy(timeout: int = 90) -> bool:
    for _ in range(timeout // 2):
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", "taskbounddb-postgres"],
            capture_output=True, text=True,
        )
        if proc.stdout.strip() == "healthy":
            return True
        time.sleep(2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    parser.add_argument("--stop-after", type=int, default=3, help="stop after this many triggers")
    args = parser.parse_args()

    wait_for_api(args.base_url)
    run_id = str(int(time.time()))
    credential = post_json(
        args.base_url, "/credentials",
        {"agent_id": f"triage-{run_id}", "actor": "agent:travel-expense-analyst", "ttl_minutes": 60},
    )

    triggers: list[dict[str, Any]] = []
    for index, attack in enumerate(ATTACKS, start=1):
        before = segfault_count()
        task = post_json(args.base_url, "/tasks", {
            "task_id": f"task_triage_{run_id}_{index}_{attack['id'].lower()}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 5,
        })
        if not task.get("payload_text"):
            continue
        result = post_json(args.base_url, "/agent-query", {
            "credential": credential,
            "payload_text": task["payload_text"],
            "signature": task["signature"],
            "sql": attack["sql"],
        })
        time.sleep(0.8)  # let the log flush / recovery begin
        after = segfault_count()
        if after > before:
            triggers.append({
                "index": index,
                "id": attack["id"],
                "category": attack.get("category"),
                "expected": attack["expected"],
                "sql": attack["sql"],
                "detail": segfault_detail(),
                "result_error": str(result.get("error") or result.get("detail") or "")[:200],
            })
            print(f"[{index:>3}] SEGFAULT  id={attack['id']:<4} cat={attack.get('category','')[:34]:<34} sql={attack['sql'][:70]}")
            wait_healthy()
            time.sleep(1.0)
            if len(triggers) >= args.stop_after:
                print(f"\nreached --stop-after={args.stop_after} triggers; stopping.")
                break
        else:
            if index % 25 == 0:
                print(f"[{index:>3}] ...no segfault so far")

    out = Path(args.output_dir) / "fail_receipt_segfault_triage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"triggers": triggers, "count": len(triggers)}, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n{len(triggers)} trigger(s) written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
