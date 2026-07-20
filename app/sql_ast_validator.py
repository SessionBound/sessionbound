"""SQL structure preflight checks for the SessionBoundDB prototype.

This validator is intentionally an API-layer prototype. It analyzes query shape
before the API invokes ``taskbound.run``; the database remains the trusted
boundary for safe views, task binding, budgets, and receipts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover - exercised only when dependency is absent
    sqlglot = None
    exp = None


BLOCKED_SCHEMAS = {
    "app_data",
    "information_schema",
    "pg_catalog",
}

TASKBOUND_INTERNAL_RELATIONS = {
    "active_sessions",
    "binding_events",
    "credential_ledger",
    "safe_view_registry",
    "signing_keys",
    "task_credential_bindings",
    "task_execution_state",
    "task_query_receipts",
    "task_rows_seen",
}

CATALOG_RELATIONS = {
    "pg_tables",
    "pg_class",
    "pg_attribute",
    "pg_roles",
    "pg_user",
    "pg_stat_activity",
    "pg_namespace",
    "pg_proc",
    "pg_authid",
    "columns",
    "tables",
    "schemata",
}

PAYLOAD_AGGREGATION_FUNCTIONS = {
    "array_agg",
    "json_agg",
    "jsonb_agg",
    "json_build_object",
    "jsonb_build_object",
    "row_to_json",
    "string_agg",
    "xmlagg",
}

DEFAULT_AGGREGATE_POLICY = {
    "min_group_size": 5,
    "entity_id": "employee_id",
    "direct_entity_group_by": "deny",
    "unverifiable_group_by": "deny",
}

DIRECT_ENTITY_GROUP_COLUMNS = {
    "employee_id",
    "expense_id",
}

SQLGLOT_PAYLOAD_AGGREGATION_ALIASES = {
    "group_concat",
    "json_array_agg",
    "json_arrayagg",
    "j_s_o_n_array_agg",
}

ALLOWED_FUNCTIONS = {
    "abs",
    "avg",
    "coalesce",
    "count",
    "current_date",
    "current_timestamp",
    "date_part",
    "date_trunc",
    "dense_rank",
    "extract",
    "greatest",
    "lag",
    "lead",
    "least",
    "lower",
    "max",
    "min",
    "now",
    "nullif",
    "rank",
    "round",
    "row_number",
    "sum",
    "upper",
}

LEXICAL_BLOCKERS = [
    (r"\bcopy\b", "COPY is not allowed"),
    (r"\btablesample\b", "TABLESAMPLE is not allowed"),
    (r"\bdo\s+\$\$", "DO blocks are not allowed"),
    (r"\bcreate\s+(or\s+replace\s+)?function\b", "CREATE FUNCTION is not allowed"),
    (r"\bset\s+search_path\b", "SET search_path is not allowed"),
    (r"^\s*set\b", "SET statements are not allowed"),
    (r"^\s*show\b", "SHOW statements are not allowed"),
]


@dataclass
class SQLValidationResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parser: str = "sqlglot"

    def reason_text(self) -> str:
        if self.allowed:
            return "AST preflight allowed query"
        return "; ".join(self.reasons) or "AST preflight denied query"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "flags": self.flags,
            "metadata": self.metadata,
            "parser": self.parser,
        }


def _norm(value: Any) -> str:
    return str(value or "").strip('"').lower()


def _dedupe(items: list[str]) -> list[str]:
    return sorted({item for item in items if item})


def _node_name(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return _norm(node)
    name = getattr(node, "name", None)
    if name:
        return _norm(name)
    this = getattr(node, "this", None)
    if isinstance(this, str):
        return _norm(this)
    if hasattr(node, "sql_name"):
        try:
            return _norm(node.sql_name())
        except Exception:
            return ""
    return ""


def _function_name(node: Any) -> str:
    if hasattr(node, "sql_name"):
        try:
            sql_name = _norm(node.sql_name())
            if sql_name and sql_name != "anonymous":
                return sql_name
        except Exception:
            pass
    name = _node_name(node)
    if name and name != "anonymous":
        if name == "*" and type(node).__name__.lower() == "count":
            return "count"
        return name
    this = getattr(node, "this", None)
    if isinstance(this, str):
        return _norm(this)
    return ""


def _denied_column_names(denied_columns: list[str] | None) -> set[str]:
    names: set[str] = {"bank_account", "phone", "salary"}
    for column in denied_columns or []:
        normalized = _norm(column)
        if normalized:
            names.add(normalized)
            names.add(normalized.split(".")[-1])
    return names


def _aggregate_policy(aggregate_policy: dict[str, Any] | None) -> dict[str, Any]:
    policy = dict(DEFAULT_AGGREGATE_POLICY)
    policy.update(aggregate_policy or {})
    try:
        policy["min_group_size"] = max(1, int(policy.get("min_group_size", 5)))
    except (TypeError, ValueError):
        policy["min_group_size"] = 5
    return policy


def _statement_kind(root: Any) -> str:
    if root is None:
        return "unknown"
    return type(root).__name__.lower()


def _is_allowed_statement(root: Any) -> bool:
    if exp is None:
        return False
    allowed_types = (exp.Select, exp.Union, exp.Intersect, exp.Except)
    return isinstance(root, allowed_types)


def validate_sql_structure(
    sql_text: str,
    *,
    allowed_views: list[str] | None = None,
    denied_columns: list[str] | None = None,
    aggregate_policy: dict[str, Any] | None = None,
) -> SQLValidationResult:
    allowed_view_names = {_norm(view) for view in allowed_views or []}
    denied_names = _denied_column_names(denied_columns)
    min_group_policy = _aggregate_policy(aggregate_policy)
    reasons: list[str] = []
    flags: list[str] = []
    referenced_relations: list[str] = []
    referenced_columns: list[str] = []
    function_calls: list[str] = []
    aggregate_functions: list[str] = []
    group_by_columns: list[str] = []
    ctes: list[str] = []
    statement_kinds: list[str] = []

    metadata: dict[str, Any] = {
        "referenced_relations": [],
        "referenced_columns": [],
        "function_calls": [],
        "aggregate_functions": [],
        "group_by_columns": [],
        "has_group_by": False,
        "has_having": False,
        "min_group_size": min_group_policy["min_group_size"],
        "ctes": [],
        "recursive_cte": False,
        "subqueries": 0,
        "joins": 0,
        "catalog_access": False,
        "operation_types": [],
        "set_operations": [],
        "container_expressions": [],
        "parser_available": sqlglot is not None,
    }

    if sqlglot is None or exp is None:
        return SQLValidationResult(
            allowed=False,
            reasons=["AST parser sqlglot is not available"],
            flags=["parser_unavailable"],
            metadata=metadata,
        )

    lowered = sql_text.lower()
    for pattern, reason in LEXICAL_BLOCKERS:
        if re.search(pattern, lowered):
            reasons.append(reason)
            flags.append("blocked_lexical_form")

    for function_name in PAYLOAD_AGGREGATION_FUNCTIONS:
        if re.search(rf"\b{re.escape(function_name)}\s*\(", lowered):
            reasons.append(f"payload aggregation function {function_name} is not allowed")
            flags.append("payload_aggregation")

    if re.sub(r";\s*$", "", sql_text).find(";") >= 0:
        reasons.append("multiple SQL statements are not allowed")
        flags.append("multiple_statements")

    try:
        roots = sqlglot.parse(sql_text, read="postgres")
    except Exception as exc:
        return SQLValidationResult(
            allowed=False,
            reasons=[f"SQL parser rejected query: {exc}"],
            flags=["parse_error"],
            metadata=metadata,
        )

    if len(roots) != 1:
        reasons.append("exactly one SQL statement is required")
        flags.append("multiple_statements")

    for root in roots:
        if root is None:
            continue
        statement_kinds.append(_statement_kind(root))
        if not _is_allowed_statement(root):
            reasons.append(f"statement type {type(root).__name__} is not allowed")
            flags.append("non_select_statement")

        set_operation_names = []
        for set_type, set_label in (
            (exp.Union, "UNION"),
            (exp.Intersect, "INTERSECT"),
            (exp.Except, "EXCEPT"),
        ):
            if isinstance(root, set_type) or any(root.find_all(set_type)):
                set_operation_names.append(set_label)
        if set_operation_names:
            metadata["set_operations"].extend(set_operation_names)
            reasons.append("UNION, INTERSECT, and EXCEPT query shapes are not allowed")
            flags.append("set_operation")

        with_node = root.args.get("with") or root.args.get("with_")
        if with_node is not None:
            if bool(with_node.args.get("recursive")):
                metadata["recursive_cte"] = True
                reasons.append("recursive CTEs are not allowed")
                flags.append("recursive_cte")

        for cte in root.find_all(exp.CTE):
            alias = _node_name(cte.alias_or_name)
            if alias:
                ctes.append(alias)

        cte_names = set(ctes)

        group_node = root.args.get("group")
        if group_node is not None:
            metadata["has_group_by"] = True
            for group_expr in getattr(group_node, "expressions", []) or []:
                group_sql = group_expr.sql(dialect="postgres")
                group_by_columns.append(group_sql)
                if isinstance(group_expr, exp.Column):
                    group_name = _norm(group_expr.name)
                    if group_name in DIRECT_ENTITY_GROUP_COLUMNS:
                        reasons.append(
                            f"GROUP BY {group_name} is denied by the minimum group-size policy"
                        )
                        flags.append("small_group_direct_entity_group_by")
            reasons.append("GROUP BY aggregate release requires an approved aggregate template")
            flags.append("unverifiable_group_by")

        if any(root.find_all(exp.Window)):
            reasons.append("window functions require an approved aggregate template")
            flags.append("small_group_window")

        aggregate_present = any(root.find_all(exp.AggFunc))

        where_node = root.args.get("where")
        if where_node is not None and aggregate_present:
            reasons.append("filtered aggregate release requires an approved aggregate template")
            flags.append("small_group_filtered_aggregate")

        if aggregate_present:
            reasons.append("aggregate release requires an approved aggregate template")
            flags.append("aggregate_template_required")

        having_node = root.args.get("having")
        if having_node is not None:
            metadata["has_having"] = True
            reasons.append(
                "HAVING clauses are denied by the minimum group-size policy unless a task template explicitly allows them"
            )
            flags.append("small_group_having")

        for table in root.find_all(exp.Table):
            table_name = _norm(table.name)
            db_name = _norm(getattr(table, "db", ""))
            catalog_name = _norm(getattr(table, "catalog", ""))
            full_name = ".".join(
                part for part in [catalog_name, db_name, table_name] if part
            )
            referenced_relations.append(full_name or table_name)

            if table_name in cte_names:
                continue

            if db_name in BLOCKED_SCHEMAS or catalog_name in BLOCKED_SCHEMAS:
                metadata["catalog_access"] = metadata["catalog_access"] or db_name in {"pg_catalog", "information_schema"} or catalog_name in {"pg_catalog", "information_schema"}
                reasons.append(f"direct access to schema {db_name or catalog_name} is not allowed")
                flags.append("raw_or_catalog_schema")

            if table_name in CATALOG_RELATIONS:
                metadata["catalog_access"] = True
                reasons.append(f"catalog relation {table_name} is not allowed")
                flags.append("catalog_access")

            if db_name == "taskbound" and table_name in TASKBOUND_INTERNAL_RELATIONS:
                reasons.append(f"internal taskbound relation {table_name} is not allowed")
                flags.append("internal_taskbound_relation")

            if db_name and db_name != "taskbound":
                reasons.append(f"schema-qualified relation {full_name} is outside the safe-view registry")
                flags.append("raw_schema_reference")

            if allowed_view_names and table_name not in allowed_view_names:
                reasons.append(f"relation {table_name} is not in the approved safe-view registry")
                flags.append("unapproved_relation")

        for column in root.find_all(exp.Column):
            column_sql = column.sql(dialect="postgres")
            referenced_columns.append(column_sql)
            column_name = _norm(column.name)
            table_name = _norm(getattr(column, "table", ""))
            if column_name in denied_names or f"{table_name}.{column_name}" in denied_names:
                reasons.append(f"denied column {column_name} is outside this task capability")
                flags.append("denied_column")

        for alias in root.find_all(exp.Alias):
            alias_name = _norm(alias.alias)
            if alias_name in denied_names:
                reasons.append(f"alias {alias_name} matches a denied field")
                flags.append("denied_column_alias")

        for subquery in root.find_all(exp.Subquery):
            parent = getattr(subquery, "parent", None)
            if not isinstance(parent, (exp.From, exp.Join)):
                reasons.append("subquery expressions are not allowed")
                flags.append("subquery_expression")

        for array_expr in root.find_all(exp.Array):
            metadata["container_expressions"].append(array_expr.sql(dialect="postgres"))
            reasons.append("ARRAY constructors are not allowed")
            flags.append("container_expression")

        for tuple_expr in root.find_all(exp.Tuple):
            metadata["container_expressions"].append(tuple_expr.sql(dialect="postgres"))
            reasons.append("ROW constructors are not allowed")
            flags.append("container_expression")

        for func in root.find_all(exp.Func):
            function_name = _function_name(func)
            if not function_name:
                continue
            function_calls.append(function_name)
            if function_name in PAYLOAD_AGGREGATION_FUNCTIONS or function_name in SQLGLOT_PAYLOAD_AGGREGATION_ALIASES:
                reasons.append(f"payload aggregation function {function_name} is not allowed")
                flags.append("payload_aggregation")
            elif function_name == "row":
                metadata["container_expressions"].append(func.sql(dialect="postgres"))
                reasons.append("ROW constructors are not allowed")
                flags.append("container_expression")
            elif function_name == "array":
                continue
            elif function_name.startswith("json") or function_name.startswith("xml"):
                metadata["container_expressions"].append(func.sql(dialect="postgres"))
                reasons.append("SQL/JSON and XML constructors are not allowed")
                flags.append("container_expression")
            elif function_name not in ALLOWED_FUNCTIONS:
                reasons.append(f"unknown function {function_name} is not allowed")
                flags.append("unknown_function")

        for agg in root.find_all(exp.AggFunc):
            aggregate_name = _function_name(agg)
            if aggregate_name:
                aggregate_functions.append(aggregate_name)

        metadata["subqueries"] += sum(1 for _ in root.find_all(exp.Subquery))
        metadata["joins"] += sum(1 for _ in root.find_all(exp.Join))

    metadata["referenced_relations"] = _dedupe(referenced_relations)
    metadata["referenced_columns"] = _dedupe(referenced_columns)
    metadata["function_calls"] = _dedupe(function_calls)
    metadata["aggregate_functions"] = _dedupe(aggregate_functions)
    metadata["group_by_columns"] = _dedupe(group_by_columns)
    metadata["ctes"] = _dedupe(ctes)
    metadata["operation_types"] = _dedupe(statement_kinds)
    metadata["set_operations"] = _dedupe(metadata["set_operations"])
    metadata["container_expressions"] = _dedupe(metadata["container_expressions"])

    return SQLValidationResult(
        allowed=not reasons,
        reasons=_dedupe(reasons),
        flags=_dedupe(flags),
        metadata=metadata,
        parser=f"sqlglot {getattr(sqlglot, '__version__', 'unknown')}",
    )
