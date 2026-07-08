#!/usr/bin/env python3
"""Microbenchmark the native structural guard without executing result rows."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
TIME_RE = re.compile(r"Time:\s+([0-9.]+)\s+ms")

CASES: list[dict[str, str]] = [
    {
        "id": "HM01",
        "name": "guard_check_select",
        "sql": "SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 10",
    },
    {
        "id": "HM02",
        "name": "guard_check_join",
        "sql": """
            SELECT e.expense_id, e.amount, d.department_name
            FROM expenses e
            JOIN departments d ON e.department_id = d.department_id
            ORDER BY e.amount DESC
            LIMIT 10
        """,
    },
    {
        "id": "HM03",
        "name": "guard_check_group_by",
        "sql": """
            SELECT department_name, count(*) AS expense_count, sum(amount) AS total_amount
            FROM expenses
            GROUP BY department_name
            ORDER BY total_amount DESC
        """,
    },
    {
        "id": "HM04",
        "name": "guard_check_cte_window",
        "sql": """
            WITH ranked AS (
              SELECT expense_id, department_name, amount,
                     row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn
              FROM expenses
            )
            SELECT department_name, expense_id, amount
            FROM ranked
            WHERE rn = 1
        """,
    },
]

DENIED_CASES: list[dict[str, str]] = [
    {
        "id": "HD01",
        "name": "guard_blocks_raw_schema",
        "sql": "SELECT * FROM app_data.expenses LIMIT 1",
        "expected_reason": "raw application schema access is not allowed",
    },
    {
        "id": "HD02",
        "name": "guard_blocks_union",
        "sql": "SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees",
        "expected_reason": "UNION, INTERSECT, and EXCEPT are not allowed",
    },
]


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


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_command(stop_on_error: bool) -> list[str]:
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "env",
        "PGPASSWORD=postgres",
        "psql",
        "-X",
        "-q",
        "-v",
        f"ON_ERROR_STOP={'1' if stop_on_error else '0'}",
        "-U",
        "postgres",
        "-d",
        "travel",
    ]


def run_psql(sql_script: str, *, stop_on_error: bool = True) -> dict[str, Any]:
    proc = subprocess.run(
        psql_command(stop_on_error),
        cwd=REPO_ROOT,
        input=sql_script,
        text=True,
        capture_output=True,
    )
    output = (proc.stdout + proc.stderr).strip()
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "output_tail": "\n".join(output.splitlines()[-16:]),
    }


def setup_sql() -> str:
    return """
\\pset tuples_only on
\\pset pager off
SET search_path = taskbound, pg_temp;
CREATE OR REPLACE FUNCTION pg_temp.enable_hook_microbenchmark()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
BEGIN
  PERFORM set_config(
    'sessionbound_guard.allowed_view_oids',
    (
      SELECT string_agg((database_object::regclass)::oid::text, ',' ORDER BY view_name)
      FROM taskbound.safe_view_registry
      WHERE view_name = ANY(ARRAY['expenses','employees','departments','approval_events','ledger_entries'])
    ),
    false
  );
  PERFORM set_config('sessionbound_guard.task_id', 'hook_microbenchmark', false);
  PERFORM set_config('sessionbound_guard.budget_account', 'hook_microbenchmark', false);
  PERFORM set_config('sessionbound_guard.max_queries', '1000000', false);
  PERFORM set_config('sessionbound_guard.max_unique_expense_rows', '1000000', false);
  PERFORM set_config('sessionbound_guard.receipts_enabled', 'off', false);
  PERFORM set_config('sessionbound_guard.budget_accounting_enabled', 'off', false);
  PERFORM set_config('sessionbound_guard.task_bound', 'on', false);
END;
$$;
CREATE OR REPLACE FUNCTION pg_temp.guard_check(sql_text text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, taskbound, pg_temp
AS $$
BEGIN
  PERFORM public.sessionbound_guard_check(sql_text);
END;
$$;
GRANT EXECUTE ON FUNCTION pg_temp.guard_check(text) TO agent_app;
SET ROLE agent_app;
SET search_path = taskbound, pg_temp;
SELECT pg_temp.enable_hook_microbenchmark();
"""


def summarize(latencies_ms: list[float]) -> dict[str, float | int | None]:
    if not latencies_ms:
        return {
            "measurements": 0,
            "p50_ms": None,
            "p95_ms": None,
            "mean_ms": None,
            "stddev_ms": None,
            "max_ms": None,
        }
    ordered = sorted(latencies_ms)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "measurements": len(latencies_ms),
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[p95_index],
        "mean_ms": statistics.fmean(ordered),
        "stddev_ms": statistics.pstdev(ordered),
        "max_ms": ordered[-1],
    }


def measure_case(case: dict[str, str], warmup: int, measured: int) -> dict[str, Any]:
    statements = [setup_sql()]
    statements.extend(
        f"SELECT pg_temp.guard_check({sql_literal(case['sql'])});"
        for _ in range(warmup)
    )
    statements.append("\\timing on")
    statements.extend(
        f"SELECT pg_temp.guard_check({sql_literal(case['sql'])});"
        for _ in range(measured)
    )
    statements.append("\\timing off")
    result = run_psql("\n".join(statements))
    timings = [float(match.group(1)) for match in TIME_RE.finditer(result["stdout"] + result["stderr"])]
    return {
        **case,
        "expected": "Allowed structural check",
        "passed": result["returncode"] == 0 and len(timings) == measured,
        "timing": summarize(timings),
        "psql": result,
    }


def run_denied_checks() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for case in DENIED_CASES:
        sql_script = "\n".join(
            [
                setup_sql(),
                f"SELECT pg_temp.guard_check({sql_literal(case['sql'])});",
            ]
        )
        result = run_psql(sql_script, stop_on_error=False)
        output = result["stdout"] + result["stderr"]
        records.append(
            {
                **case,
                "expected": "Denied structural check",
                "passed": result["returncode"] == 0 and case["expected_reason"] in output,
                "psql": result,
            }
        )
    return records


def run_eval(warmup: int, measured: int) -> dict[str, Any]:
    records = [measure_case(case, warmup, measured) for case in CASES]
    denied_records = run_denied_checks()
    failed = sum(1 for record in records + denied_records if not record["passed"])
    return {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "warmup_iterations": warmup,
            "measurement_iterations": measured,
            "case_count": len(records),
            "denied_check_count": len(denied_records),
            "passed": len(records) + len(denied_records) - failed,
            "failed": failed,
            "notes": [
                "Measures public.sessionbound_guard_check(sql), which prepares SQL through PostgreSQL parse/analyze and the sessionbound_guard structural checks.",
                "The benchmark does not execute result rows and does not measure executor row accounting, receipt insertion, or PL/pgSQL result materialization.",
            ],
        },
        "records": records,
        "denied_checks": denied_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--measured", type=int, default=200)
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = run_eval(args.warmup, args.measured)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    output_path = output_dir / f"hook_microbenchmark_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
