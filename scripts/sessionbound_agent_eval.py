#!/usr/bin/env python3
"""Current SessionBound arXiv v1 evaluation script.

This script exercises the public HTTP contract:

1. create a short-lived dynamic database credential;
2. create a signed task token;
3. submit SQL through /agent-query;
4. verify allowed, denied, transparent filtering, and budget outcomes.

It intentionally avoids stale workflow-command scenarios from older prototypes.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SCENARIO_CROSSWALK = {
    "safe_view_select": ("V01", "Safe view SELECT"),
    "join_safe_views": ("V02", "Join safe views"),
    "cte": ("V03", "CTE"),
    "group_by": ("V04", "Department totals"),
    "window_function": ("V05", "Ranked expenses"),
    "scoped_drill_down": ("V06", "Scoped drill-down"),
    "salary_access": ("V07", "Salary access"),
    "bank_account_access": ("V08", "Bank account access"),
    "raw_table_access": ("V09", "Raw table access"),
    "mutation_sql": ("V10", "Mutation SQL"),
    "ddl": ("V11", "DDL"),
    "pg_catalog_access": ("V12", "pg_catalog access"),
    "json_agg_payload": ("V13", "json_agg(e) payload"),
    "jsonb_agg_payload": ("V14", "jsonb_agg(e) payload"),
    "array_agg_payload": ("V15", "array_agg(e.expense_id) payload"),
    "string_agg_payload": ("V16", "string_agg(employee_name, ',') payload"),
    "xmlagg_payload": ("V17", "xmlagg(...) payload"),
    "row_to_json_payload": ("V18", "row_to_json(e) payload"),
    "json_build_object_payload": ("V19", "json_build_object(...) payload"),
    "jsonb_build_object_payload": ("V20", "jsonb_build_object(...) payload"),
    "out_of_scope_month": ("V21", "Other month"),
    "out_of_scope_department": ("V22", "Other department"),
    "query_budget_overflow": ("V23", "Query budget overflow"),
    "disclosure_budget_overflow": ("V24", "Disclosure budget overflow"),
}


def paper_reference(scenario_name: str) -> dict[str, str]:
    paper_id, paper_scenario = SCENARIO_CROSSWALK[scenario_name]
    return {"paper_id": paper_id, "paper_scenario": paper_scenario}


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def post_json(base_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    last_error: str | None = None
    for attempt in range(1, 6):
        req = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"detail": f"HTTP {exc.code}"}
            return {"ok": False, "http_error": exc.code, **payload}
        except (urllib.error.URLError, ConnectionResetError, socket.timeout) as exc:
            last_error = str(exc)
            if attempt < 5:
                time.sleep(1)
                continue
    return {"ok": False, "error": last_error or "request failed"}


class SessionBoundEvalClient:
    def __init__(self, base_url: str, run_id: str):
        self.base_url = base_url
        self.run_id = run_id
        self.counter = 0

    def open_session(
        self,
        scenario: str,
        *,
        max_rows: int = 1000,
        max_queries: int = 20,
        delegator: str = "user:alice",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.counter += 1
        safe_scenario = "".join(ch if ch.isalnum() else "_" for ch in scenario.lower())[:48]
        credential = post_json(
            self.base_url,
            "/credentials",
            {"agent_id": f"sessionbound-eval-{safe_scenario}", "ttl_minutes": 15},
        )
        task = post_json(
            self.base_url,
            "/tasks",
            {
                "task_id": f"task_{self.run_id}_{self.counter}_{safe_scenario}",
                "task_type": "monthly_travel_expense_review",
                "delegator": delegator,
                "scope": {"expense_month": "2026-06"},
                "max_rows": max_rows,
                "max_queries": max_queries,
            },
        )
        return credential, task

    def query_with_session(self, credential: dict[str, Any], task: dict[str, Any], sql: str) -> dict[str, Any]:
        if not credential.get("db_user"):
            return {"ok": False, "setup_error": credential}
        if not task.get("payload_text") or not task.get("signature"):
            return {"ok": False, "setup_error": task}
        return post_json(
            self.base_url,
            "/agent-query",
            {
                "credential": credential,
                "payload_text": task["payload_text"],
                "signature": task["signature"],
                "sql": sql,
            },
        )

    def query(
        self,
        scenario: str,
        sql: str,
        *,
        max_rows: int = 1000,
        max_queries: int = 20,
    ) -> dict[str, Any]:
        credential, task = self.open_session(scenario, max_rows=max_rows, max_queries=max_queries)
        return self.query_with_session(credential, task, sql)


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    rows = result.get("rows")
    error = result.get("error") or result.get("detail") or result.get("setup_error")
    return {
        "ok": result.get("ok"),
        "row_count": len(rows) if isinstance(rows, list) else None,
        "rows_preview": rows[:5] if isinstance(rows, list) else None,
        "error": error,
        "state": result.get("state"),
        "receipts": result.get("receipts"),
    }


def classify(result: dict[str, Any], expected_kind: str) -> tuple[str, str]:
    rows = result.get("rows")
    error = result.get("error") or result.get("detail") or result.get("setup_error")
    if expected_kind == "filtered_0_rows":
        if result.get("ok") and isinstance(rows, list) and len(rows) == 0:
            return "filtered_0_rows", "Allowed query shape returned zero rows under task-bound safe-view scope."
        if result.get("ok"):
            return "allowed", f"Expected zero rows but got {len(rows) if isinstance(rows, list) else 'unknown'} rows."
        return "denied", str(error).split("\n")[0] if error else "Denied."
    if result.get("ok"):
        return "allowed", f"rows={len(rows) if isinstance(rows, list) else 'unknown'}"
    return "denied", str(error).split("\n")[0] if error else "Denied."


def run_eval(base_url: str) -> dict[str, Any]:
    run_id = str(int(time.time()))
    client = SessionBoundEvalClient(base_url, run_id)
    scenarios: list[dict[str, Any]] = []

    def record_query(
        name: str,
        category: str,
        expected: str,
        sql: str,
        *,
        max_rows: int = 1000,
        max_queries: int = 20,
    ) -> None:
        result = client.query(name, sql, max_rows=max_rows, max_queries=max_queries)
        actual, evidence = classify(result, expected)
        scenarios.append(
            {
                **paper_reference(name),
                "name": name,
                "category": category,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "request": {"sql": sql, "max_rows": max_rows, "max_queries": max_queries},
                "evidence": evidence,
                "result": compact_result(result),
            }
        )

    record_query(
        "safe_view_select",
        "allowed",
        "allowed",
        "SELECT expense_id, employee_name, amount FROM expenses ORDER BY amount DESC LIMIT 3",
    )
    record_query(
        "join_safe_views",
        "allowed",
        "allowed",
        "SELECT e.expense_id, e.amount, d.department_name "
        "FROM expenses e JOIN departments d ON e.department_id = d.department_id "
        "ORDER BY e.amount DESC LIMIT 3",
    )
    record_query(
        "cte",
        "allowed",
        "allowed",
        "WITH high AS (SELECT expense_id, amount FROM expenses WHERE amount > 1000) "
        "SELECT count(*) AS high_count FROM high",
    )
    record_query(
        "group_by",
        "allowed",
        "allowed",
        "SELECT department_name, COUNT(*) AS n, SUM(amount) AS total "
        "FROM expenses GROUP BY department_name ORDER BY total DESC",
    )
    record_query(
        "window_function",
        "allowed",
        "allowed",
        "SELECT expense_id, department_name, amount, "
        "row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn "
        "FROM expenses ORDER BY amount DESC LIMIT 5",
    )
    record_query(
        "scoped_drill_down",
        "allowed",
        "allowed",
        "SELECT expense_id, merchant, city, amount FROM expenses WHERE department_name = 'Sales' ORDER BY amount DESC",
    )

    record_query("salary_access", "denied_field", "denied", "SELECT employee_name, salary FROM employees")
    record_query("bank_account_access", "denied_field", "denied", "SELECT employee_name, bank_account FROM employees")
    record_query("raw_table_access", "schema_escape", "denied", "SELECT * FROM app_data.expenses LIMIT 1")
    record_query("mutation_sql", "write_attempt", "denied", "DELETE FROM expenses WHERE expense_month = '2026-06'")
    record_query("ddl", "destructive_operation", "denied", "DROP TABLE expenses")
    record_query("pg_catalog_access", "schema_escape", "denied", "SELECT * FROM pg_catalog.pg_tables")

    for name, sql in [
        ("json_agg_payload", "SELECT json_agg(e) FROM expenses e"),
        ("jsonb_agg_payload", "SELECT jsonb_agg(e) FROM expenses e"),
        ("array_agg_payload", "SELECT array_agg(e.expense_id) FROM expenses e"),
        ("string_agg_payload", "SELECT string_agg(employee_name, ',') FROM employees"),
        ("xmlagg_payload", "SELECT xmlagg(xmlelement(name expense, expense_id)) FROM expenses"),
        ("row_to_json_payload", "SELECT row_to_json(e) FROM expenses e LIMIT 1"),
        ("json_build_object_payload", "SELECT json_build_object('expense', e) FROM expenses e LIMIT 1"),
        ("jsonb_build_object_payload", "SELECT jsonb_build_object('expense', e) FROM expenses e LIMIT 1"),
    ]:
        record_query(name, "payload_aggregation", "denied", sql)

    record_query(
        "out_of_scope_month",
        "transparent_scope_filtering",
        "filtered_0_rows",
        "SELECT expense_id, expense_month, amount FROM expenses WHERE expense_month = '2026-05'",
    )
    record_query(
        "out_of_scope_department",
        "transparent_scope_filtering",
        "filtered_0_rows",
        "SELECT expense_id, department_id, amount FROM expenses WHERE department_id = 'dep_nonexistent'",
    )

    credential, task = client.open_session("query_budget_overflow", max_queries=1, max_rows=1000)
    first = client.query_with_session(credential, task, "SELECT count(*) AS n FROM expenses")
    second = client.query_with_session(credential, task, "SELECT count(*) AS n FROM expenses")
    actual, evidence = classify(second, "denied")
    if not first.get("ok"):
        actual = "setup_failed"
        evidence = "First query failed before budget overflow check."
    scenarios.append(
        {
            **paper_reference("query_budget_overflow"),
            "name": "query_budget_overflow",
            "category": "budget",
            "expected": "denied",
            "actual": actual,
            "passed": actual == "denied",
            "request": {"sql": "two SELECT count(*) calls with max_queries=1", "max_queries": 1},
            "evidence": evidence,
            "first_result": compact_result(first),
            "result": compact_result(second),
        }
    )

    record_query(
        "disclosure_budget_overflow",
        "budget",
        "denied",
        "SELECT expense_id, employee_name, amount FROM expenses ORDER BY amount DESC LIMIT 3",
        max_rows=2,
    )

    passed = sum(1 for item in scenarios if item["passed"])
    return {
        "title": "SessionBound evaluation",
        "commit": git_commit(),
        "run_id": run_id,
        "base_url": base_url,
        "passed": passed,
        "failed": len(scenarios) - passed,
        "total": len(scenarios),
        "scenarios": scenarios,
    }


def format_expected(value: str) -> str:
    return {
        "allowed": "Allowed",
        "denied": "Denied",
        "filtered_0_rows": "Filtered / 0 rows",
    }.get(value, value)


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"sessionbound_agent_eval_{report['run_id']}.json"
    md_path = output_dir / f"sessionbound_agent_eval_{report['run_id']}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# SessionBound Evaluation",
        "",
        f"- Commit: `{report['commit']}`",
        f"- Base URL: `{report['base_url']}`",
        f"- Passed: {report['passed']} / {report['total']}",
        f"- Failed: {report['failed']} / {report['total']}",
        "",
        "| Paper ID | Paper Scenario | Script Scenario | Category | Expected | Actual | Pass | Evidence |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for item in report["scenarios"]:
        lines.append(
            "| {paper_id} | {paper_scenario} | {name} | {category} | {expected} | {actual} | {passed} | {evidence} |".format(
                paper_id=item["paper_id"],
                paper_scenario=item["paper_scenario"],
                name=item["name"],
                category=item["category"],
                expected=format_expected(item["expected"]),
                actual=format_expected(item["actual"]),
                passed="yes" if item["passed"] else "no",
                evidence=str(item["evidence"]).replace("\n", " ").replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is the canonical public evaluation for the current SessionBound arXiv v1 prototype. It checks allowed analytical SQL, denied boundary violations, transparent safe-view scope filtering, payload aggregation blocking, and budget enforcement.",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def print_summary(report: dict[str, Any], json_path: Path, md_path: Path) -> None:
    print("SessionBound evaluation")
    print(f"Commit: {report['commit']}")
    print(f"Base URL: {report['base_url']}")
    print()
    print(f"Passed: {report['passed']} / {report['total']}")
    print(f"Failed: {report['failed']} / {report['total']}")
    print()
    for item in report["scenarios"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(
            f"{status} {item['name']}: expected={format_expected(item['expected'])}; "
            f"actual={format_expected(item['actual'])}; evidence={item['evidence']}"
        )
    print()
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default="eval_runs")
    args = parser.parse_args()
    report = run_eval(args.base_url)
    json_path, md_path = write_report(report, Path(args.output_dir))
    print_summary(report, json_path, md_path)


if __name__ == "__main__":
    main()
