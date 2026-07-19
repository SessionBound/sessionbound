#!/usr/bin/env python3
"""Evaluate the SessionBound SQL AST preflight validator."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "app"))

from sql_ast_validator import validate_sql_structure  # noqa: E402


ALLOWED_VIEWS = ["expenses", "departments", "employees", "approval_events", "ledger_entries"]
DENIED_COLUMNS = ["employees.bank_account", "employees.phone", "employees.salary"]
AGGREGATE_POLICY = {
    "min_group_size": 5,
    "entity_id": "employee_id",
    "direct_entity_group_by": "deny",
    "unverifiable_group_by": "deny",
}


CASES: list[dict[str, str]] = [
    {
        "name": "select_safe_view",
        "expected": "allowed",
        "sql": "SELECT expense_id, employee_name, amount FROM expenses ORDER BY amount DESC LIMIT 3",
    },
    {
        "name": "join_safe_views",
        "expected": "allowed",
        "sql": "SELECT e.expense_id, d.department_name FROM expenses e JOIN departments d ON e.department_id = d.department_id LIMIT 5",
    },
    {
        "name": "group_by",
        "expected": "blocked",
        "sql": "SELECT department_name, count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY department_name",
    },
    {
        "name": "group_by_sensitive_entity",
        "expected": "blocked",
        "sql": "SELECT department_id, employee_id, count(*) AS n FROM expenses GROUP BY department_id, employee_id",
    },
    {
        "name": "group_by_expense_id",
        "expected": "blocked",
        "sql": "SELECT expense_id, count(*) AS n FROM expenses GROUP BY expense_id",
    },
    {
        "name": "having_small_group_probe",
        "expected": "blocked",
        "sql": "SELECT department_name, count(*) AS n FROM expenses GROUP BY department_name HAVING count(*) < 5",
    },
    {
        "name": "cte",
        "expected": "blocked",
        "sql": "WITH high AS (SELECT expense_id, amount FROM expenses WHERE amount > 1000) SELECT count(*) FROM high",
    },
    {
        "name": "window_function",
        "expected": "blocked",
        "sql": "SELECT expense_id, row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn FROM expenses",
    },
    {
        "name": "raw_schema",
        "expected": "blocked",
        "sql": "SELECT * FROM app_data.expenses",
    },
    {
        "name": "catalog_schema",
        "expected": "blocked",
        "sql": "SELECT * FROM pg_catalog.pg_tables",
    },
    {
        "name": "bare_catalog_view",
        "expected": "blocked",
        "sql": "SELECT schemaname, tablename FROM pg_tables",
    },
    {
        "name": "mutation_delete",
        "expected": "blocked",
        "sql": "DELETE FROM expenses WHERE expense_month = '2026-06'",
    },
    {
        "name": "copy",
        "expected": "blocked",
        "sql": "COPY expenses TO STDOUT",
    },
    {
        "name": "table_sample",
        "expected": "blocked",
        "sql": "SELECT * FROM expenses TABLESAMPLE SYSTEM (10)",
    },
    {
        "name": "create_function",
        "expected": "blocked",
        "sql": "CREATE FUNCTION leak() RETURNS text AS $$ SELECT 'x' $$ LANGUAGE SQL",
    },
    {
        "name": "do_block",
        "expected": "blocked",
        "sql": "DO $$ BEGIN RAISE NOTICE 'x'; END $$",
    },
    {
        "name": "payload_array_agg",
        "expected": "blocked",
        "sql": "SELECT array_agg(expense_id) FROM expenses",
    },
    {
        "name": "recursive_cte",
        "expected": "blocked",
        "sql": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 10) SELECT * FROM r",
    },
    {
        "name": "union_stacking",
        "expected": "blocked",
        "sql": "SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees",
    },
    {
        "name": "unknown_function",
        "expected": "blocked",
        "sql": "SELECT custom_leak(employee_name) FROM employees",
    },
    {
        "name": "denied_column_alias",
        "expected": "blocked",
        "sql": "SELECT amount AS salary FROM expenses",
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


def run_eval() -> dict[str, Any]:
    records = []
    for case in CASES:
        result = validate_sql_structure(
            case["sql"],
            allowed_views=ALLOWED_VIEWS,
            denied_columns=DENIED_COLUMNS,
            aggregate_policy=AGGREGATE_POLICY,
        )
        actual = "allowed" if result.allowed else "blocked"
        records.append(
            {
                **case,
                "actual": actual,
                "passed": actual == case["expected"],
                "result": result.to_dict(),
            }
        )
    return {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": git_commit(),
            "case_count": len(records),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    payload = run_eval()
    output_path = output_dir / f"ast_validation_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0 if payload["run"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
