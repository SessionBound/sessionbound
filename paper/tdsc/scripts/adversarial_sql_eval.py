#!/usr/bin/env python3
"""Run adversarial SQL probes through the public SessionBound API."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from evidence_metadata import git_metadata

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROL_PLANE_KEY = os.environ.get(
    "TASKBOUND_CONTROL_PLANE_KEY",
    "tdsc-demo-control-plane-key",
)
INFRA_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


ATTACKS: list[dict[str, Any]] = [
    {"id": "A01", "category": "Direct boundary violations", "expected": "Blocked", "sql": "SELECT salary FROM employees;"},
    {"id": "A02", "category": "Direct boundary violations", "expected": "Blocked", "sql": "SELECT bank_account FROM employees;"},
    {"id": "A03", "category": "Direct boundary violations", "expected": "Blocked", "sql": "SELECT * FROM app_data.expenses;"},
    {"id": "A04", "category": "Direct boundary violations", "expected": "Blocked", "sql": "DELETE FROM expenses WHERE expense_month='2026-06';"},
    {"id": "A05", "category": "Direct boundary violations", "expected": "Blocked", "sql": "DROP TABLE expenses;"},
    {"id": "B01", "category": "Catalog and metadata escape", "expected": "Blocked", "sql": "SELECT * FROM pg_catalog.pg_tables;"},
    {"id": "B02", "category": "Catalog and metadata escape", "expected": "Blocked", "sql": "SELECT * FROM information_schema.columns;"},
    {"id": "B03", "category": "Catalog and metadata escape", "expected": "Blocked", "sql": "SELECT schemaname, tablename FROM pg_tables;"},
    {"id": "C01", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT json_agg(e) FROM expenses e;"},
    {"id": "C02", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT jsonb_agg(e) FROM expenses e;"},
    {"id": "C03", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT array_agg(e.expense_id) FROM expenses e;"},
    {"id": "C04", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT array_agg(row_to_json(e)) FROM expenses e;"},
    {"id": "C05", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT string_agg(employee_name, ',') FROM employees;"},
    {"id": "C06", "category": "Payload aggregation / compression", "expected": "Blocked", "sql": "SELECT json_build_object('data', array_agg(e.expense_id)) FROM expenses e;"},
    {"id": "D01", "category": "Obfuscation and aliases", "expected": "Allowed but accounted", "sql": "SELECT e.employee_name AS s FROM employees e;"},
    {"id": "D02", "category": "Obfuscation and aliases", "expected": "Blocked", "sql": "SELECT amount AS salary FROM expenses;"},
    {"id": "D03", "category": "Obfuscation and aliases", "expected": "Allowed but accounted", "sql": "SELECT e.amount + 0 AS amount2 FROM expenses e;"},
    {"id": "D04", "category": "Obfuscation and aliases", "expected": "Allowed but accounted", "sql": "SELECT COALESCE(employee_name, '') FROM employees;"},
    {"id": "E01", "category": "Subqueries and CTEs", "expected": "Allowed but accounted", "sql": "WITH x AS (SELECT * FROM expenses) SELECT * FROM x;"},
    {"id": "E02", "category": "Subqueries and CTEs", "expected": "Allowed but accounted", "sql": "WITH x AS (SELECT employee_name FROM employees) SELECT * FROM x;"},
    {"id": "E03", "category": "Subqueries and CTEs", "expected": "Blocked", "sql": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 10) SELECT * FROM r;"},
    {"id": "F01", "category": "UNION and stacking", "expected": "Blocked", "sql": "SELECT expense_id FROM expenses UNION SELECT employee_id FROM employees;"},
    {"id": "F02", "category": "UNION and stacking", "expected": "Blocked", "sql": "SELECT department_id FROM departments UNION ALL SELECT employee_id FROM employees;"},
    {
        "id": "G01",
        "category": "Small-group / aggregate inference",
        "expected": "Blocked",
        "sql": "SELECT department_id, count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY department_id;",
        "task_overrides": {"scope": {"expense_month": "2026-06", "department_id": "dep_sales"}},
    },
    {"id": "G02", "category": "Small-group / aggregate inference", "expected": "Blocked", "sql": "SELECT department_id, count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM expenses WHERE employee_id = 'emp_003' GROUP BY department_id;"},
    {"id": "G03", "category": "Small-group / aggregate inference", "expected": "Blocked", "sql": "SELECT department_id, employee_id, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY department_id, employee_id;"},
    {"id": "G04", "category": "Small-group / aggregate inference", "expected": "Blocked", "sql": "SELECT expense_id, count(*) AS n, sum(amount) AS total FROM expenses GROUP BY expense_id;"},
    {"id": "G05", "category": "Small-group / aggregate inference", "expected": "Blocked", "sql": "SELECT department_id, count(*) AS n FROM expenses GROUP BY department_id HAVING count(*) < 5;"},
    {"id": "G06", "category": "Small-group / aggregate inference", "expected": "Blocked", "sql": "SELECT count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM expenses;"},
    {
        "id": "G07",
        "category": "Small-group / aggregate inference",
        "expected": "Blocked",
        "sql": "SELECT count(DISTINCT employee_id) AS employee_count, count(*) AS n, sum(amount) AS total FROM expenses;",
        "task_overrides": {
            "aggregate_policy": {
                "min_group_size": 1000,
                "entity_id": "employee_id",
                "direct_entity_group_by": "deny",
                "unverifiable_group_by": "deny",
            }
        },
    },
    {"id": "H01", "category": "Search path / function abuse", "expected": "Blocked", "sql": "SHOW search_path;"},
    {"id": "H02", "category": "Search path / function abuse", "expected": "Blocked", "sql": "SET search_path TO app_data;"},
    {"id": "H03", "category": "Search path / function abuse", "expected": "Blocked", "sql": "CREATE FUNCTION leak() RETURNS text AS $$ SELECT 'x' $$ LANGUAGE SQL;"},
    {"id": "H04", "category": "Search path / function abuse", "expected": "Blocked", "sql": "DO $$ BEGIN RAISE NOTICE 'x'; END $$;"},
]


ATTACKS.extend([
    # Raw schema escape variants.
    {"id": "I01", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT expense_id FROM app_data.expenses LIMIT 1;"},
    {"id": "I02", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT e.expense_id FROM app_data.expenses AS e LIMIT 1;"},
    {"id": "I03", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT * FROM app_data.employees LIMIT 1;"},
    {"id": "I04", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT * FROM app_data.departments LIMIT 1;"},
    {"id": "I05", "category": "Raw schema escape", "expected": "Blocked", "sql": "WITH x AS (SELECT * FROM app_data.expenses) SELECT * FROM x;"},
    {"id": "I06", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT * FROM taskbound.safe_view_registry;"},
    {"id": "I07", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT * FROM taskbound.task_execution_state;"},
    {"id": "I08", "category": "Raw schema escape", "expected": "Blocked", "sql": "SELECT * FROM taskbound.task_query_receipts;"},

    # Catalog escape variants.
    {"id": "J01", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT relname FROM pg_catalog.pg_class LIMIT 5;"},
    {"id": "J02", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT rolname FROM pg_catalog.pg_roles;"},
    {"id": "J03", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT proname FROM pg_catalog.pg_proc LIMIT 5;"},
    {"id": "J04", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT * FROM pg_catalog.pg_namespace;"},
    {"id": "J05", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT table_schema, table_name FROM information_schema.tables;"},
    {"id": "J06", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT column_name FROM information_schema.columns;"},
    {"id": "J07", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT * FROM pg_user;"},
    {"id": "J08", "category": "Catalog escape", "expected": "Blocked", "sql": "SELECT * FROM pg_stat_activity;"},

    # pg_temp, search path, GUC, and session tampering.
    {"id": "K01", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "SET search_path TO pg_temp, taskbound;"},
    {"id": "K02", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "RESET search_path;"},
    {"id": "K03", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "SHOW sessionbound_guard.task_bound;"},
    {"id": "K04", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "SELECT set_config('sessionbound_guard.task_bound','off',false);"},
    {"id": "K05", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "SELECT current_setting('sessionbound_guard.allowed_view_oids', true);"},
    {"id": "K06", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "CREATE TEMP TABLE expenses AS SELECT * FROM app_data.expenses;"},
    {"id": "K07", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "SET ROLE postgres;"},
    {"id": "K08", "category": "pg_temp/search_path/GUC/session tampering", "expected": "Blocked", "sql": "DISCARD ALL;"},

    # Function abuse.
    {"id": "L01", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT pg_sleep(0.01);"},
    {"id": "L02", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT version();"},
    {"id": "L03", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT inet_server_addr();"},
    {"id": "L04", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT has_table_privilege('app_data.expenses','SELECT');"},
    {"id": "L05", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT pg_read_file('/etc/passwd');"},
    {"id": "L06", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT lo_export(1, '/tmp/leak');"},
    {"id": "L07", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT taskbound.current_payload();"},
    {"id": "L08", "category": "Function abuse", "expected": "Blocked", "sql": "SELECT taskbound.require_payload();"},

    # Payload aggregation and compression variants.
    {"id": "M01", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT json_agg(expense_id ORDER BY amount DESC) FROM expenses;"},
    {"id": "M02", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT jsonb_agg(to_jsonb(e)) FROM expenses e;"},
    {"id": "M03", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT array_agg(employee_id ORDER BY employee_id) FROM expenses;"},
    {"id": "M04", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT string_agg(expense_id, '|') FROM expenses;"},
    {"id": "M05", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT xmlagg(xmlelement(name e, expense_id)) FROM expenses;"},
    {"id": "M06", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT json_build_object('ids', array_agg(expense_id), 'n', count(*)) FROM expenses;"},
    {"id": "M07", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT jsonb_build_object('row', row_to_json(e)) FROM expenses e LIMIT 1;"},
    {"id": "M08", "category": "Payload aggregation and compression", "expected": "Blocked", "sql": "SELECT encode(convert_to(string_agg(expense_id, ','), 'UTF8'), 'base64') FROM expenses;"},

    # JSON/XML/composite/cast leakage.
    {"id": "N01", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT to_json(e) FROM expenses e LIMIT 1;"},
    {"id": "N02", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT to_jsonb(e) FROM expenses e LIMIT 1;"},
    {"id": "N03", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT row_to_json(e) FROM expenses e LIMIT 1;"},
    {"id": "N04", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT xmlelement(name expense, expense_id, amount) FROM expenses LIMIT 1;"},
    {"id": "N05", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT CAST(salary AS text) FROM employees;"},
    {"id": "N06", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT (SELECT salary FROM employees LIMIT 1)::text;"},
    {"id": "N07", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT format('%s:%s', employee_name, salary) FROM employees;"},
    {"id": "N08", "category": "JSON/XML/composite/cast leakage", "expected": "Blocked", "sql": "SELECT concat(employee_name, ':', bank_account) FROM employees;"},

    # Prepared statement lifecycle.
    {"id": "O01", "category": "Prepared statement lifecycle", "expected": "Blocked", "sql": "PREPARE p AS SELECT * FROM expenses;"},
    {"id": "O02", "category": "Prepared statement lifecycle", "expected": "Blocked", "sql": "PREPARE p(text) AS SELECT * FROM expenses WHERE employee_id = $1;"},
    {"id": "O03", "category": "Prepared statement lifecycle", "expected": "Blocked", "sql": "EXECUTE p;"},
    {"id": "O04", "category": "Prepared statement lifecycle", "expected": "Blocked", "sql": "DEALLOCATE p;"},
    {"id": "O05", "category": "Prepared statement lifecycle", "expected": "Blocked", "sql": "SELECT * FROM pg_prepared_statements;"},

    # Cursor/FETCH lifecycle.
    {"id": "P01", "category": "Cursor/FETCH lifecycle", "expected": "Blocked", "sql": "DECLARE c CURSOR FOR SELECT * FROM expenses;"},
    {"id": "P02", "category": "Cursor/FETCH lifecycle", "expected": "Blocked", "sql": "FETCH 10 FROM c;"},
    {"id": "P03", "category": "Cursor/FETCH lifecycle", "expected": "Blocked", "sql": "MOVE FORWARD 10 FROM c;"},
    {"id": "P04", "category": "Cursor/FETCH lifecycle", "expected": "Blocked", "sql": "CLOSE c;"},
    {"id": "P05", "category": "Cursor/FETCH lifecycle", "expected": "Blocked", "sql": "SELECT * FROM pg_cursors;"},

    # COPY/EXPLAIN variants.
    {"id": "Q01", "category": "COPY/EXPLAIN variants", "expected": "Blocked", "sql": "COPY (SELECT * FROM expenses) TO STDOUT;"},
    {"id": "Q02", "category": "COPY/EXPLAIN variants", "expected": "Blocked", "sql": "COPY expenses TO STDOUT;"},
    {"id": "Q03", "category": "COPY/EXPLAIN variants", "expected": "Blocked", "sql": "EXPLAIN SELECT * FROM expenses;"},
    {"id": "Q04", "category": "COPY/EXPLAIN variants", "expected": "Blocked", "sql": "EXPLAIN ANALYZE SELECT * FROM expenses;"},
    {"id": "Q05", "category": "COPY/EXPLAIN variants", "expected": "Blocked", "sql": "EXPLAIN (FORMAT JSON) SELECT * FROM expenses;"},

    # CTE/recursive/set-operation attacks.
    {"id": "R01", "category": "CTE/recursive/set-operation attacks", "expected": "Blocked", "sql": "WITH x AS (SELECT salary FROM employees) SELECT * FROM x;"},
    {"id": "R02", "category": "CTE/recursive/set-operation attacks", "expected": "Blocked", "sql": "WITH x AS (SELECT * FROM app_data.expenses) SELECT count(*) FROM x;"},
    {"id": "R03", "category": "CTE/recursive/set-operation attacks", "expected": "Blocked", "sql": "SELECT expense_id FROM expenses INTERSECT SELECT employee_id FROM employees;"},
    {"id": "R04", "category": "CTE/recursive/set-operation attacks", "expected": "Blocked", "sql": "SELECT expense_id FROM expenses EXCEPT SELECT employee_id FROM employees;"},
    {"id": "R05", "category": "CTE/recursive/set-operation attacks", "expected": "Blocked", "sql": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 3) SELECT n FROM r;"},

    # VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/set-returning functions.
    {"id": "S01", "category": "VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/SRF", "expected": "Blocked", "sql": "SELECT * FROM (VALUES ((SELECT salary FROM employees LIMIT 1))) AS v(x);"},
    {"id": "S02", "category": "VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/SRF", "expected": "Blocked", "sql": "SELECT e.expense_id, leak.salary FROM expenses e CROSS JOIN LATERAL (SELECT salary FROM employees LIMIT 1) leak;"},
    {"id": "S03", "category": "VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/SRF", "expected": "Allowed but accounted", "sql": "SELECT DISTINCT ON (employee_id) employee_id, expense_id, amount FROM expenses ORDER BY employee_id, amount DESC;"},
    {"id": "S04", "category": "VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/SRF", "expected": "Blocked", "sql": "SELECT * FROM expenses TABLESAMPLE SYSTEM (10);"},
    {"id": "S05", "category": "VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/SRF", "expected": "Blocked", "sql": "SELECT * FROM generate_series(1, 10) AS g(x);"},
    {"id": "S06", "category": "VALUES/LATERAL/DISTINCT ON/TABLESAMPLE/SRF", "expected": "Blocked", "sql": "SELECT unnest(array_agg(expense_id)) FROM expenses;"},

    # DDL/DML/utility commands.
    {"id": "T01", "category": "DDL/DML/utility commands", "expected": "Blocked", "sql": "INSERT INTO expenses(expense_id) VALUES ('x');"},
    {"id": "T02", "category": "DDL/DML/utility commands", "expected": "Blocked", "sql": "UPDATE expenses SET amount = 0;"},
    {"id": "T03", "category": "DDL/DML/utility commands", "expected": "Blocked", "sql": "TRUNCATE expenses;"},
    {"id": "T04", "category": "DDL/DML/utility commands", "expected": "Blocked", "sql": "ALTER TABLE expenses ADD COLUMN leak text;"},
    {"id": "T05", "category": "DDL/DML/utility commands", "expected": "Blocked", "sql": "VACUUM expenses;"},
    {"id": "T06", "category": "DDL/DML/utility commands", "expected": "Blocked", "sql": "ANALYZE expenses;"},

    # Additional aggregate inference probes.
    {"id": "U01", "category": "Aggregate inference probes", "expected": "Blocked", "sql": "SELECT employee_id, min(amount), max(amount) FROM expenses GROUP BY employee_id;"},
    {"id": "U02", "category": "Aggregate inference probes", "expected": "Blocked", "sql": "SELECT department_id, count(*) FROM expenses WHERE employee_id='emp_001' GROUP BY department_id;"},
    {"id": "U03", "category": "Aggregate inference probes", "expected": "Blocked", "sql": "SELECT category, count(*) FROM expenses GROUP BY category HAVING count(DISTINCT employee_id) < 5;"},
    {"id": "U04", "category": "Aggregate inference probes", "expected": "Blocked", "sql": "SELECT employee_level, count(*) FROM expenses GROUP BY employee_level HAVING avg(amount) > 0;"},
    {"id": "U05", "category": "Aggregate inference probes", "expected": "Blocked", "sql": "SELECT category, count(DISTINCT employee_id) AS employee_count, count(*) AS n FROM expenses GROUP BY category;"},
    {"id": "U06", "category": "Aggregate inference probes", "expected": "Blocked", "sql": "SELECT count(DISTINCT employee_id) AS employee_count, avg(amount) AS avg_amount FROM expenses;"},

    # Pagination and budget scraping.
    {"id": "V01", "category": "Pagination/budget scraping", "expected": "Allowed but accounted", "sql": "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 5 OFFSET 0;"},
    {"id": "V02", "category": "Pagination/budget scraping", "expected": "Allowed but accounted", "sql": "SELECT expense_id, amount FROM expenses ORDER BY expense_id LIMIT 5 OFFSET 50;"},
    {"id": "V03", "category": "Pagination/budget scraping", "expected": "Allowed but accounted", "sql": "SELECT expense_id, amount FROM expenses ORDER BY amount DESC LIMIT 1 OFFSET 100;"},
    {"id": "V04", "category": "Pagination/budget scraping", "expected": "Blocked", "sql": "SELECT array_agg(expense_id) FROM (SELECT expense_id FROM expenses ORDER BY expense_id LIMIT 5000) s;"},
    {"id": "V05", "category": "Pagination/budget scraping", "expected": "Allowed but accounted", "sql": "SELECT expense_id, amount FROM expenses WHERE amount > 0 ORDER BY amount DESC LIMIT 20;"},

    # Revocation, expiry, and rebind attempts expressed as SQL/session attacks.
    {"id": "W01", "category": "Revocation/expiry/rebind attacks", "expected": "Blocked", "sql": "UPDATE taskbound.task_execution_state SET revoked=false;"},
    {"id": "W02", "category": "Revocation/expiry/rebind attacks", "expected": "Blocked", "sql": "SELECT * FROM taskbound.credential_ledger;"},
    {"id": "W03", "category": "Revocation/expiry/rebind attacks", "expected": "Blocked", "sql": "SELECT taskbound.bind_task('{}', '00');"},
    {"id": "W04", "category": "Revocation/expiry/rebind attacks", "expected": "Blocked", "sql": "SELECT taskbound.unbind_task();"},
    {"id": "W05", "category": "Revocation/expiry/rebind attacks", "expected": "Blocked", "sql": "SELECT * FROM taskbound.task_credential_bindings;"},

    # Schema drift attacks.
    {"id": "X01", "category": "Schema drift attacks", "expected": "Blocked", "sql": "UPDATE taskbound.safe_view_registry SET registry_version = registry_version + 1;"},
    {"id": "X02", "category": "Schema drift attacks", "expected": "Blocked", "sql": "ALTER VIEW taskbound.expenses RENAME TO expenses_old;"},
    {"id": "X03", "category": "Schema drift attacks", "expected": "Blocked", "sql": "CREATE OR REPLACE VIEW taskbound.expenses AS SELECT * FROM app_data.expenses;"},
    {"id": "X04", "category": "Schema drift attacks", "expected": "Blocked", "sql": "SELECT view_definition_hash FROM taskbound.safe_view_registry;"},
    {"id": "X05", "category": "Schema drift attacks", "expected": "Blocked", "sql": "SELECT exposed_column_hash FROM taskbound.safe_view_registry;"},

    # Rollback and audit-survival attacks.
    {"id": "Y01", "category": "Rollback/audit-survival attacks", "expected": "Blocked", "sql": "BEGIN;"},
    {"id": "Y02", "category": "Rollback/audit-survival attacks", "expected": "Blocked", "sql": "ROLLBACK;"},
    {"id": "Y03", "category": "Rollback/audit-survival attacks", "expected": "Blocked", "sql": "SAVEPOINT s;"},
    {"id": "Y04", "category": "Rollback/audit-survival attacks", "expected": "Blocked", "sql": "SET TRANSACTION READ WRITE;"},
    {"id": "Y05", "category": "Rollback/audit-survival attacks", "expected": "Blocked", "sql": "DELETE FROM taskbound.task_query_receipts;"},
])


def post_json(base_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if path.startswith(("/credentials", "/tasks", "/todos", "/admin/")):
        headers["X-TaskBound-Control-Plane-Key"] = CONTROL_PLANE_KEY
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": f"HTTP {exc.code}"}
        return {"ok": False, "http_error": exc.code, **payload}
    except (urllib.error.URLError, ConnectionResetError, socket.timeout) as exc:
        return {"ok": False, "transport_error": True, "error": str(exc)}


def wait_for_api(base_url: str) -> None:
    for _ in range(30):
        try:
            urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=2).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"API did not become ready at {base_url}")


def infra_error(result: dict[str, Any]) -> bool:
    if result.get("setup_error"):
        return True
    if result.get("transport_error"):
        return True
    return result.get("http_error") in INFRA_HTTP_STATUS


def accounting_evidence(result: dict[str, Any]) -> dict[str, Any]:
    rows = result.get("rows") or []
    receipts = result.get("receipts") or []
    state = result.get("state") or []
    state_row = state[0] if state else {}
    allowed_receipts = [receipt for receipt in receipts if receipt.get("decision") == "allowed"]
    receipt = allowed_receipts[0] if len(allowed_receipts) == 1 else {}
    row_count = len(rows) if isinstance(rows, list) else 0
    receipt_rows = int(receipt.get("rows_returned") or 0) if receipt else None
    state_query_count = state_row.get("query_count")
    state_rows = state_row.get("returned_rows")
    checks = {
        "receipt_count": len(receipts),
        "allowed_receipt_count": len(allowed_receipts),
        "row_count": row_count,
        "receipt_rows": receipt_rows,
        "state_query_count": state_query_count,
        "state_returned_rows": state_rows,
        "exactly_one_allowed_receipt": len(receipts) == 1 and len(allowed_receipts) == 1,
        "receipt_rows_match_result": receipt_rows == row_count,
        "state_query_count_accounted": state_query_count == 1,
        "state_returned_rows_match_result": state_rows == row_count,
    }
    checks["ok"] = all(
        bool(checks[key])
        for key in [
            "exactly_one_allowed_receipt",
            "receipt_rows_match_result",
            "state_query_count_accounted",
            "state_returned_rows_match_result",
        ]
    )
    return checks


def classify(result: dict[str, Any], expected: str) -> str:
    if infra_error(result):
        return "Not testable"
    if not result.get("ok"):
        return "Blocked"
    if expected == "Known limitation":
        return "Known limitation"
    if expected == "Allowed but accounted" and not accounting_evidence(result)["ok"]:
        return "Allowed without accounting evidence"
    return "Allowed but accounted"


def bucket_for(record: dict[str, Any]) -> str:
    if record["actual"] == "Allowed but accounted":
        return "allowed_safe_view_analytical_cases"
    if record["category"] == "Payload aggregation / compression":
        return "blocked_payload_aggregation"
    if record["category"] == "Small-group / aggregate inference":
        return "blocked_small_group_aggregate"
    if record["actual"] == "Blocked":
        return "blocked_direct_violations"
    if record["actual"] == "Not testable":
        return "inconclusive_infrastructure"
    if record["actual"] == "Allowed without accounting evidence":
        return "accounting_oracle_failure"
    if record["actual"] == "Known limitation":
        return "remaining_known_limitation"
    return "other"


def selected_attacks(case_ids: list[str], limit: int | None) -> list[dict[str, Any]]:
    selected = ATTACKS
    if case_ids:
        wanted = {case_id.upper() for case_id in case_ids}
        selected = [attack for attack in selected if attack["id"].upper() in wanted]
    if limit is not None:
        selected = selected[: max(limit, 0)]
    return selected


def run_eval(base_url: str, case_ids: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
    wait_for_api(base_url)
    run_id = str(int(time.time()))
    attacks = selected_attacks(case_ids or [], limit)
    metadata = git_metadata(REPO_ROOT)
    credential = post_json(
        base_url,
        "/credentials",
        {
            "agent_id": f"tdsc-adversarial-{run_id}",
            "actor": "agent:travel-expense-analyst",
            "ttl_minutes": 30,
        },
    )
    records = []
    for index, attack in enumerate(attacks, start=1):
        safe_id = attack["id"].lower()
        task_request = {
            "task_id": f"task_adv_{run_id}_{index}_{safe_id}",
            "task_type": "monthly_travel_expense_review",
            "delegator": "user:alice",
            "actor": "agent:travel-expense-analyst",
            "credential_id": credential.get("credential_id"),
            "scope": {"expense_month": "2026-06"},
            "max_rows": 5000,
            "max_queries": 5,
        }
        task_request.update(attack.get("task_overrides", {}))
        task = post_json(base_url, "/tasks", task_request)
        if not credential.get("db_user") or not task.get("payload_text"):
            result = {"ok": False, "setup_error": {"credential": credential, "task": task}}
        else:
            result = post_json(
                base_url,
                "/agent-query",
                {
                    "credential": credential,
                    "payload_text": task["payload_text"],
                    "signature": task["signature"],
                    "sql": attack["sql"],
                },
            )
        actual = classify(result, attack["expected"])
        receipts = result.get("receipts") or []
        validation = result.get("ast_validation") or {}
        evidence = accounting_evidence(result) if result.get("ok") else {}
        record = {
            **attack,
            "actual": actual,
            "passed": actual == attack["expected"],
            "row_count": len(result.get("rows") or []),
            "error": str(result.get("error") or result.get("detail") or result.get("setup_error") or "").split("\n")[0],
            "ast_allowed": validation.get("allowed"),
            "ast_flags": validation.get("flags"),
            "infra_error": infra_error(result),
            "accounting_evidence": evidence,
            "receipt_decisions": [receipt.get("decision") for receipt in receipts],
        }
        record["bucket"] = bucket_for(record)
        records.append(record)
    counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    for record in records:
        counts[record["actual"]] = counts.get(record["actual"], 0) + 1
        bucket_counts[record["bucket"]] = bucket_counts.get(record["bucket"], 0) + 1
    return {
        "run": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commit": metadata["commit"],
            "git": metadata,
            "base_url": base_url,
            "case_count": len(records),
            "passed": sum(1 for record in records if record["passed"]),
            "failed": sum(1 for record in records if not record["passed"]),
            "classification_counts": counts,
            "bucket_counts": bucket_counts,
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "paper/tdsc/raw_results"))
    parser.add_argument("--case-id", action="append", default=[], help="run only the named adversarial case id; may be repeated")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N selected cases")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    payload = run_eval(args.base_url, case_ids=args.case_id, limit=args.limit)
    output_path = output_dir / f"adversarial_sql_{timestamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(output_path)
    print(json.dumps(payload["run"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
