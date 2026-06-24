import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://agent_app:agentpass@localhost:15432/travel",
)
SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me")


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign(payload_text: str) -> str:
    return hmac.new(SECRET.encode(), payload_text.encode(), hashlib.sha256).hexdigest()


def default_task(
    task_id: str = "task_expense_review_2026_06",
    department_id: str | None = None,
    max_queries: int = 5,
    max_rows: int = 5,
    child_of: str | None = None,
) -> tuple[str, str]:
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    payload = {
        "key_id": "dev",
        "task_id": task_id,
        "tenant_id": "company_a",
        "delegator": "user:alice",
        "actor": "agent:travel-expense-analyst",
        "purpose": "monthly_travel_expense_anomaly_review",
        "natural_language_goal": "Analyze June 2026 travel reimbursement anomalies.",
        "operations": ["SELECT"],
        "allowed_views": ["expenses", "departments", "employees"],
        "denied_columns": ["employees.bank_account", "employees.phone", "employees.salary"],
        "row_scope": {
            "expense_month": "2026-06",
        },
        "budgets": {
            "max_queries": max_queries,
            "max_unique_expense_rows": max_rows,
        },
        "delegation": {
            "can_spawn_subagents": True,
            "child_capability_must_be_subset": True,
        },
        "expires_at": expires_at,
        "policy_version": "travel-demo-v1",
    }
    if department_id:
        payload["row_scope"]["department_id"] = department_id
    if child_of:
        payload["parent_task_id"] = child_of
        payload["budget_account"] = child_of

    payload_text = canonical_json(payload)
    return payload_text, sign(payload_text)


def connect():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def bind(cur, payload_text: str, signature: str):
    cur.execute("SELECT taskbound.bind_task(%s, %s)", (payload_text, signature))
    print_json("BOUND", cur.fetchone()[0])


def print_json(label: str, value):
    print(f"\n== {label} ==")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def run_query(cur, sql: str):
    print(f"\nSQL> {sql}")
    try:
        cur.execute("SELECT * FROM taskbound.run(%s)", (sql,))
        rows = [row[0] for row in cur.fetchall()]
        print_json("ROWS", rows)
    except Exception as exc:
        print(f"DENIED: {exc}")


def inspect(cur):
    cur.execute("SELECT * FROM taskbound.inspect_task_state()")
    print_json("TASK STATE", [dict(zip([d.name for d in cur.description], row)) for row in cur.fetchall()])
    cur.execute(
        """
        SELECT decision, rows_returned, unique_rows_added,
               remaining_unique_row_budget, reason, created_at
        FROM taskbound.receipts()
        LIMIT 10
        """
    )
    print_json("RECEIPTS", [dict(zip([d.name for d in cur.description], row)) for row in cur.fetchall()])


def command_token(args):
    payload_text, signature = default_task(
        task_id=args.task_id,
        department_id=args.department,
        max_queries=args.max_queries,
        max_rows=args.max_rows,
    )
    print(payload_text)
    print(signature)


def command_query(args):
    payload_text, signature = default_task(max_queries=args.max_queries, max_rows=args.max_rows)
    with connect() as conn:
        with conn.cursor() as cur:
            bind(cur, payload_text, signature)
            run_query(cur, args.sql)
            inspect(cur)


def command_demo(_args):
    payload_text, signature = default_task(max_queries=8, max_rows=4)
    with connect() as conn:
        with conn.cursor() as cur:
            bind(cur, payload_text, signature)

            print("\n--- Legitimate task-scoped analysis ---")
            run_query(
                cur,
                "SELECT department_name, category, sum(amount) AS total_amount "
                "FROM expenses GROUP BY department_name, category ORDER BY total_amount DESC",
            )

            print("\n--- Multi-table JOIN over task-scoped views ---")
            run_query(
                cur,
                "SELECT e.department_name, emp.employee_level, count(*) AS trips, sum(e.amount) AS total_amount "
                "FROM expenses e JOIN employees emp ON emp.employee_id = e.employee_id "
                "GROUP BY e.department_name, emp.employee_level ORDER BY total_amount DESC",
            )

            print("\n--- CTE + HAVING-style analytical query ---")
            run_query(
                cur,
                "WITH dept_totals AS ("
                "SELECT department_name, sum(amount) AS total_amount "
                "FROM expenses GROUP BY department_name"
                ") SELECT department_name, total_amount "
                "FROM dept_totals WHERE total_amount > 3000 ORDER BY total_amount DESC",
            )

            print("\n--- Window function over scoped expense rows ---")
            run_query(
                cur,
                "SELECT expense_id, department_name, employee_name, amount, "
                "rank() OVER (PARTITION BY department_name ORDER BY amount DESC) AS dept_rank "
                "FROM expenses ORDER BY department_name, dept_rank LIMIT 2",
            )

            print("\n--- Detail access consumes cumulative unique-row budget ---")
            run_query(
                cur,
                "SELECT expense_id, department_name, employee_name, category, merchant, amount "
                "FROM expenses ORDER BY amount DESC LIMIT 3",
            )

            print("\n--- Prompt-injection attempt: read sensitive salary data ---")
            run_query(cur, "SELECT employee_name, salary FROM employees")

            print("\n--- Escape attempt: access raw internal tables ---")
            run_query(cur, "SELECT * FROM app_data.employees")

            print("\n--- Pagination attempt: individually legal, cumulatively blocked ---")
            run_query(
                cur,
                "SELECT expense_id, department_name, employee_name, category, merchant, amount "
                "FROM expenses ORDER BY expense_id LIMIT 3 OFFSET 3",
            )

            inspect(cur)

    print("\n--- Child-agent budget sharing demo ---")
    parent_payload, parent_sig = default_task(
        task_id="task_parent_sales_review",
        department_id="dep_sales",
        max_queries=5,
        max_rows=2,
    )
    child_payload, child_sig = default_task(
        task_id="task_child_sales_review_a",
        department_id="dep_sales",
        max_queries=5,
        max_rows=2,
        child_of="task_parent_sales_review",
    )

    with connect() as parent_conn:
        with parent_conn.cursor() as parent_cur:
            bind(parent_cur, parent_payload, parent_sig)
            run_query(
                parent_cur,
                "SELECT expense_id, department_name, employee_name, amount "
                "FROM expenses ORDER BY expense_id LIMIT 2",
            )

            with connect() as child_conn:
                with child_conn.cursor() as child_cur:
                    bind(child_cur, child_payload, child_sig)
                    run_query(
                        child_cur,
                        "SELECT expense_id, department_name, employee_name, amount "
                        "FROM expenses ORDER BY expense_id LIMIT 2 OFFSET 2",
                    )
                    inspect(child_cur)

            inspect(parent_cur)


def build_parser():
    parser = argparse.ArgumentParser(description="SessionBoundDB travel reimbursement demo")
    sub = parser.add_subparsers(required=True)

    token = sub.add_parser("token", help="print a signed task payload and signature")
    token.add_argument("--task-id", default="task_expense_review_2026_06")
    token.add_argument("--department")
    token.add_argument("--max-queries", type=int, default=5)
    token.add_argument("--max-rows", type=int, default=5)
    token.set_defaults(func=command_token)

    query = sub.add_parser("query", help="run one SQL query through taskbound.run")
    query.add_argument("sql")
    query.add_argument("--max-queries", type=int, default=5)
    query.add_argument("--max-rows", type=int, default=5)
    query.set_defaults(func=command_query)

    demo = sub.add_parser("demo", help="run the full attack/allow demo")
    demo.set_defaults(func=command_demo)

    return parser


if __name__ == "__main__":
    try:
        args = build_parser().parse_args()
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)
