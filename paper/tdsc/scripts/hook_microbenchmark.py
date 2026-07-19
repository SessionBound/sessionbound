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
        "name": "guard_check_cte_detail",
        "sql": """
            WITH high AS (
              SELECT expense_id, department_name, amount
              FROM expenses
              WHERE amount > 1000
            )
            SELECT department_name, expense_id, amount
            FROM high
            ORDER BY amount DESC
            LIMIT 10
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
    {
        "id": "HD03",
        "name": "guard_blocks_group_by_without_template",
        "sql": """
            SELECT department_name, count(*) AS expense_count, sum(amount) AS total_amount
            FROM expenses
            GROUP BY department_name
            ORDER BY total_amount DESC
        """,
        "expected_reason": "GROUP BY aggregate release requires an approved aggregate template",
    },
    {
        "id": "HD04",
        "name": "guard_blocks_window_without_template",
        "sql": """
            SELECT expense_id, department_name, amount,
                   row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn
            FROM expenses
            ORDER BY amount DESC
            LIMIT 10
        """,
        "expected_reason": "window functions require an approved aggregate template",
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


def psql_command(stop_on_error: bool, *, user: str = "postgres", password: str = "postgres") -> list[str]:
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "env",
        f"PGPASSWORD={password}",
        "psql",
        "-X",
        "-q",
        "-v",
        f"ON_ERROR_STOP={'1' if stop_on_error else '0'}",
        "-U",
        user,
        "-d",
        "travel",
    ]


def run_psql(sql_script: str, *, stop_on_error: bool = True, as_agent: bool = False) -> dict[str, Any]:
    user = "agent_app" if as_agent else "postgres"
    password = "agentpass" if as_agent else "postgres"
    proc = subprocess.run(
        psql_command(stop_on_error, user=user, password=password),
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
CREATE SCHEMA IF NOT EXISTS tdsc_bench;
CREATE OR REPLACE FUNCTION tdsc_bench.enable_hook_microbenchmark()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  allowed_oids text;
BEGIN
  SELECT string_agg((database_object::regclass)::oid::text, ',' ORDER BY view_name)
  INTO allowed_oids
  FROM taskbound.safe_view_registry
  WHERE view_name = ANY(ARRAY['expenses','employees','departments','approval_events','ledger_entries']);

  PERFORM public.sessionbound_guard_install_binding(
    'hook_microbenchmark',
    'hook_microbenchmark',
    COALESCE(allowed_oids, ''),
    '',
    1000000,
    1000000,
    5,
    false,
    false,
    'hook_microbenchmark_binding',
    1,
    0,
    'hook_microbenchmark_token',
    'hook_microbenchmark_credential',
    now() + interval '1 hour'
  );
END;
$$;
GRANT USAGE ON SCHEMA tdsc_bench TO agent_app;
GRANT EXECUTE ON FUNCTION tdsc_bench.enable_hook_microbenchmark() TO agent_app;
GRANT EXECUTE ON FUNCTION public.sessionbound_guard_check(text) TO agent_app;
"""


def cleanup_sql() -> str:
    return """
REVOKE EXECUTE ON FUNCTION public.sessionbound_guard_check(text) FROM agent_app;
DROP FUNCTION IF EXISTS tdsc_bench.enable_hook_microbenchmark();
DROP SCHEMA IF EXISTS tdsc_bench;
"""


def measurement_setup_sql() -> str:
    return """
\\pset tuples_only on
\\pset pager off
SET search_path = taskbound, pg_temp;
SELECT tdsc_bench.enable_hook_microbenchmark();
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
    statements = [measurement_setup_sql()]
    statements.extend(
        f"SELECT public.sessionbound_guard_check({sql_literal(case['sql'])});"
        for _ in range(warmup)
    )
    statements.append("\\timing on")
    statements.extend(
        f"SELECT public.sessionbound_guard_check({sql_literal(case['sql'])});"
        for _ in range(measured)
    )
    statements.append("\\timing off")
    result = run_psql("\n".join(statements), as_agent=True)
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
                measurement_setup_sql(),
                f"SELECT public.sessionbound_guard_check({sql_literal(case['sql'])});",
            ]
        )
        result = run_psql(sql_script, stop_on_error=False, as_agent=True)
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
    setup_result = run_psql(setup_sql())
    if setup_result["returncode"] != 0:
        raise RuntimeError(f"hook microbenchmark setup failed: {setup_result['output_tail']}")
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
        "setup": setup_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--measured", type=int, default=200)
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = run_eval(args.warmup, args.measured)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        output_path = output_dir / f"hook_microbenchmark_{timestamp}.json"
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(output_path)
        print(json.dumps(payload["run"], indent=2, sort_keys=True))
        return 0 if payload["run"]["failed"] == 0 else 1
    finally:
        run_psql(cleanup_sql(), stop_on_error=False)


if __name__ == "__main__":
    raise SystemExit(main())
