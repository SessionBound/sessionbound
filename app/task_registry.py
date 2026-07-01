import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any


SECRET = os.environ.get("TASKBOUND_SECRET", "dev-secret-change-me")


SAFE_VIEWS: dict[str, dict[str, Any]] = {
    "expenses": {
        "view_name": "expenses",
        "database_object": "taskbound.expenses",
        "owner": "data-platform",
        "description": "Travel reimbursement facts scoped by tenant, month, and optional department.",
        "raw_tables": ["app_data.expenses", "app_data.departments", "app_data.employees"],
        "safe_columns": [
            "expense_id",
            "expense_month",
            "department_id",
            "department_name",
            "employee_id",
            "employee_name",
            "employee_level",
            "category",
            "merchant",
            "city",
            "amount",
            "submitted_at",
            "status",
            "requires_finance_review",
            "requires_department_approval",
            "requires_c_level_approval",
            "monthly_employee_total",
            "yearly_employee_total",
            "next_required_role",
            "next_task_type",
            "approval_tier",
            "approval_reason",
            "can_finance_approve",
            "can_department_approve",
            "can_c_level_approve",
            "can_request_more_info",
            "can_resubmit",
            "can_pay",
        ],
        "not_exposed_columns": [
            "employees.phone",
            "employees.bank_account",
            "employees.salary",
        ],
        "enforced_scope_claims": [
            "tenant_id",
            "row_scope.expense_month",
            "row_scope.department_id",
        ],
        "workflow_fields": [
            "status",
            "requires_finance_review",
            "requires_department_approval",
            "requires_c_level_approval",
            "next_required_role",
            "next_task_type",
            "approval_tier",
            "approval_reason",
            "can_finance_approve",
            "can_department_approve",
            "can_c_level_approve",
            "can_request_more_info",
            "can_resubmit",
            "can_pay",
        ],
        "recommended_commands": [
            "submit_expense",
            "finance_approve",
            "department_approve",
            "c_level_approve",
            "return_expense_for_more_info",
            "resubmit_expense",
            "pay_expense",
        ],
    },
    "employees": {
        "view_name": "employees",
        "database_object": "taskbound.employees",
        "owner": "data-platform",
        "description": "Employee dimension with sensitive HR and payment fields removed.",
        "raw_tables": ["app_data.employees"],
        "safe_columns": [
            "employee_id",
            "department_id",
            "employee_name",
            "employee_level",
        ],
        "not_exposed_columns": [
            "phone",
            "bank_account",
            "salary",
        ],
        "enforced_scope_claims": [
            "tenant_id",
        ],
    },
    "departments": {
        "view_name": "departments",
        "database_object": "taskbound.departments",
        "owner": "data-platform",
        "description": "Department dimension scoped by tenant.",
        "raw_tables": ["app_data.departments"],
        "safe_columns": [
            "department_id",
            "department_name",
            "manager_user_id",
        ],
        "not_exposed_columns": [],
        "enforced_scope_claims": [
            "tenant_id",
        ],
    },
    "approval_events": {
        "view_name": "approval_events",
        "database_object": "taskbound.approval_events",
        "owner": "data-platform",
        "description": "Workflow audit events emitted by SessionBoundDB commands.",
        "raw_tables": ["app_data.approval_events"],
        "safe_columns": [
            "event_id",
            "expense_id",
            "event_type",
            "actor",
            "agent_actor",
            "supervisor_agent_id",
            "comment",
            "created_at",
        ],
        "not_exposed_columns": [],
        "enforced_scope_claims": ["tenant_id"],
    },
    "ledger_entries": {
        "view_name": "ledger_entries",
        "database_object": "taskbound.ledger_entries",
        "owner": "finance-platform",
        "description": "Ledger entries created by controlled payment commands.",
        "raw_tables": ["app_data.ledger_entries"],
        "safe_columns": [
            "ledger_id",
            "expense_id",
            "debit_account",
            "credit_account",
            "amount",
            "memo",
            "created_by",
            "created_at",
        ],
        "not_exposed_columns": [],
        "enforced_scope_claims": ["tenant_id"],
    },
}


COMMAND_POLICIES: dict[str, dict[str, Any]] = {
    "submit_expense": {
        "label": "提交新报销单",
        "entity_key": None,
        "entity_view": "expenses",
        "required_hint": None,
        "hint_query_columns": [],
        "positive_guidance": "Use submit_expense only when the employee wants to create a new reimbursement claim.",
        "blocked_message": "New expense submission is not available for this task.",
        "already_done_statuses": [],
    },
    "finance_approve": {
        "label": "财务初审通过",
        "entity_key": "expense_id",
        "entity_view": "expenses",
        "required_hint": "can_finance_approve",
        "hint_query_columns": [
            "expense_id",
            "amount",
            "status",
            "requires_finance_review",
            "can_finance_approve",
            "next_required_role",
            "next_task_type",
            "can_request_more_info",
        ],
        "positive_guidance": "Only propose finance_approve when can_finance_approve is true.",
        "blocked_message": "Finance compliance approval is not currently available for this expense.",
        "already_done_statuses": ["department_approval_requested", "payable", "paid"],
    },
    "department_approve": {
        "label": "部门经理审批通过",
        "entity_key": "expense_id",
        "entity_view": "expenses",
        "required_hint": "can_department_approve",
        "hint_query_columns": [
            "expense_id",
            "amount",
            "status",
            "monthly_employee_total",
            "yearly_employee_total",
            "requires_department_approval",
            "requires_c_level_approval",
            "can_department_approve",
            "next_required_role",
            "next_task_type",
            "approval_reason",
        ],
        "positive_guidance": "Only propose department_approve when can_department_approve is true.",
        "blocked_message": "Department approval is not currently available for this expense.",
        "already_done_statuses": ["c_level_approval_requested", "payable", "c_level_approved", "paid"],
    },
    "c_level_approve": {
        "label": "C-level 审批通过",
        "entity_key": "expense_id",
        "entity_view": "expenses",
        "required_hint": "can_c_level_approve",
        "hint_query_columns": [
            "expense_id",
            "amount",
            "status",
            "monthly_employee_total",
            "yearly_employee_total",
            "requires_c_level_approval",
            "can_c_level_approve",
            "approval_tier",
            "approval_reason",
        ],
        "positive_guidance": "Only propose c_level_approve when can_c_level_approve is true.",
        "blocked_message": "C-level approval is not currently available for this expense.",
        "already_done_statuses": ["c_level_approved", "paid"],
    },
    "return_expense_for_more_info": {
        "label": "退回补充材料",
        "entity_key": "expense_id",
        "entity_view": "expenses",
        "required_hint": "can_request_more_info",
        "hint_query_columns": [
            "expense_id",
            "amount",
            "status",
            "can_request_more_info",
            "next_required_role",
            "next_task_type",
        ],
        "positive_guidance": "Only propose return_expense_for_more_info when can_request_more_info is true.",
        "blocked_message": "This expense is not currently in a returnable finance-review state.",
        "already_done_statuses": ["returned_for_more_info", "paid"],
    },
    "resubmit_expense": {
        "label": "补充后重新提交",
        "entity_key": "expense_id",
        "entity_view": "expenses",
        "required_hint": "can_resubmit",
        "hint_query_columns": [
            "expense_id",
            "amount",
            "status",
            "can_resubmit",
            "can_pay",
        ],
        "positive_guidance": "Only propose resubmit_expense when can_resubmit is true.",
        "blocked_message": "This expense is not currently waiting for employee resubmission.",
        "already_done_statuses": ["submitted", "resubmitted", "paid"],
    },
    "pay_expense": {
        "label": "打款",
        "entity_key": "expense_id",
        "entity_view": "expenses",
        "required_hint": "can_pay",
        "hint_query_columns": [
            "expense_id",
            "amount",
            "status",
            "next_required_role",
            "next_task_type",
            "can_pay",
        ],
        "positive_guidance": "Only propose pay_expense when can_pay is true.",
        "blocked_message": "Payment is not currently available for this expense.",
        "already_done_statuses": ["paid"],
    },
}


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign(payload_text: str) -> str:
    return hmac.new(SECRET.encode(), payload_text.encode(), hashlib.sha256).hexdigest()


TASK_TEMPLATES: dict[str, dict[str, Any]] = {
    "monthly_travel_expense_review": {
        "task_type": "monthly_travel_expense_review",
        "description": "Read-only monthly reimbursement analysis for trends, outliers, and department totals.",
        "purpose": "monthly_travel_expense_anomaly_review",
        "actor": "agent:travel-expense-analyst",
        "tenant_id": "company_a",
        "operations": ["SELECT"],
        "allowed_views": ["expenses", "departments", "employees"],
        "allowed_commands": [],
        "denied_columns": [
            "employees.bank_account",
            "employees.phone",
            "employees.salary",
        ],
        "required_scope": ["expense_month"],
        "optional_scope": ["department_id"],
        "default_scope": {
            "expense_month": "2026-06",
        },
        "default_budgets": {
            "max_queries": 5,
            "max_unique_expense_rows": 4,
        },
        "max_budgets": {
            "max_queries": 100,
            "max_unique_expense_rows": 5000,
        },
        "ttl_minutes": 30,
        "policy_version": "travel-demo-v1",
    },
    "finance_compliance_review": {
        "task_type": "finance_compliance_review",
        "description": "Finance reviewer task for submitted reimbursements, policy checks, and first-pass approval or return.",
        "purpose": "finance_reimbursement_compliance_review",
        "actor": "agent:finance-compliance-analyst",
        "tenant_id": "company_a",
        "operations": ["SELECT", "CONTROLLED_COMMAND"],
        "allowed_views": ["expenses", "departments", "employees", "approval_events"],
        "allowed_commands": [
            "finance_approve",
            "return_expense_for_more_info",
        ],
        "denied_columns": [
            "employees.bank_account",
            "employees.phone",
            "employees.salary",
        ],
        "required_scope": ["expense_month"],
        "optional_scope": ["department_id"],
        "default_scope": {
            "expense_month": "2026-06",
        },
        "default_budgets": {
            "max_queries": 12,
            "max_unique_expense_rows": 500,
        },
        "max_budgets": {
            "max_queries": 50,
            "max_unique_expense_rows": 2000,
        },
        "ttl_minutes": 20,
        "policy_version": "finance-review-v1",
    },
    "payment_readiness_audit": {
        "task_type": "payment_readiness_audit",
        "description": "Finance payment task for approved reimbursements, ledger checks, and controlled payment execution.",
        "purpose": "reimbursement_payment_readiness_audit",
        "actor": "agent:payment-control-analyst",
        "tenant_id": "company_a",
        "operations": ["SELECT", "CONTROLLED_COMMAND"],
        "allowed_views": ["expenses", "departments", "approval_events", "ledger_entries"],
        "allowed_commands": [
            "pay_expense",
        ],
        "denied_columns": [
            "employees.bank_account",
            "employees.phone",
            "employees.salary",
        ],
        "required_scope": ["expense_month"],
        "optional_scope": ["department_id"],
        "default_scope": {
            "expense_month": "2026-06",
        },
        "default_budgets": {
            "max_queries": 10,
            "max_unique_expense_rows": 300,
        },
        "max_budgets": {
            "max_queries": 40,
            "max_unique_expense_rows": 1000,
        },
        "ttl_minutes": 15,
        "policy_version": "payment-readiness-v1",
    },
}


TASK_GRANTS: list[dict[str, Any]] = [
    {
        "grant_id": "grant_alice_travel_review",
        "delegator": "user:alice",
        "task_type": "monthly_travel_expense_review",
        "allowed_scope": {
            "tenant_id": "company_a",
            "expense_month": "2026-06",
            "department_ids": ["dep_fin", "dep_sales", "dep_eng"],
        },
        "budget_overrides": {
            "max_queries": 30,
            "max_unique_expense_rows": 5000,
        },
    },
    {
        "grant_id": "grant_fiona_travel_review",
        "delegator": "user:fiona",
        "task_type": "monthly_travel_expense_review",
        "allowed_scope": {
            "tenant_id": "company_a",
            "expense_month": "2026-06",
            "department_ids": ["dep_fin", "dep_sales", "dep_eng"],
        },
        "budget_overrides": {
            "max_queries": 30,
            "max_unique_expense_rows": 5000,
        },
    },
    {
        "grant_id": "grant_fiona_finance_compliance",
        "delegator": "user:fiona",
        "task_type": "finance_compliance_review",
        "allowed_scope": {
            "tenant_id": "company_a",
            "expense_month": "2026-06",
            "department_ids": ["dep_fin", "dep_sales", "dep_eng"],
        },
        "budget_overrides": {
            "max_queries": 30,
            "max_unique_expense_rows": 2000,
        },
    },
    {
        "grant_id": "grant_fiona_payment_readiness",
        "delegator": "user:fiona",
        "task_type": "payment_readiness_audit",
        "allowed_scope": {
            "tenant_id": "company_a",
            "expense_month": "2026-06",
            "department_ids": ["dep_fin", "dep_sales", "dep_eng"],
        },
        "budget_overrides": {
            "max_queries": 25,
            "max_unique_expense_rows": 1000,
        },
    },
    {
        "grant_id": "grant_carol_travel_review",
        "delegator": "user:carol",
        "task_type": "monthly_travel_expense_review",
        "allowed_scope": {
            "tenant_id": "company_a",
            "expense_month": "2026-06",
            "department_ids": ["dep_fin", "dep_sales", "dep_eng"],
        },
        "budget_overrides": {
            "max_queries": 30,
            "max_unique_expense_rows": 5000,
        },
    },
    {
        "grant_id": "grant_eve_travel_review",
        "delegator": "user:eve",
        "task_type": "monthly_travel_expense_review",
        "allowed_scope": {
            "tenant_id": "company_a",
            "expense_month": "2026-06",
            "department_ids": ["dep_eng"],
        },
        "budget_overrides": {
            "max_queries": 30,
            "max_unique_expense_rows": 5000,
        },
    },
]


def upsert_template(template: dict[str, Any]) -> dict[str, Any]:
    unknown_views = [
        view for view in template.get("allowed_views", [])
        if view not in SAFE_VIEWS
    ]
    if unknown_views:
        raise ValueError(f"template references unregistered safe view(s): {unknown_views}")
    task_type = template["task_type"]
    TASK_TEMPLATES[task_type] = template
    return template


def upsert_grant(grant: dict[str, Any]) -> dict[str, Any]:
    if not grant.get("grant_id"):
        grant["grant_id"] = f"grant_{len(TASK_GRANTS) + 1}"
    TASK_GRANTS[:] = [g for g in TASK_GRANTS if g["grant_id"] != grant["grant_id"]]
    TASK_GRANTS.append(grant)
    return grant


def find_grant(delegator: str, task_type: str) -> dict[str, Any] | None:
    for grant in TASK_GRANTS:
        if grant["delegator"] == delegator and grant["task_type"] == task_type:
            return grant
    return None


def _clamp_budget(value: int, max_value: int) -> int:
    return min(max(value, 1), max_value)


def build_task_from_template(
    *,
    task_id: str,
    task_type: str,
    delegator: str,
    actor: str | None,
    requested_scope: dict[str, Any] | None,
    requested_budgets: dict[str, int] | None,
) -> tuple[dict[str, Any], str, str]:
    template = TASK_TEMPLATES.get(task_type)
    if template is None:
        raise ValueError(f"unknown task_type: {task_type}")
    unknown_views = [
        view for view in template.get("allowed_views", [])
        if view not in SAFE_VIEWS
    ]
    if unknown_views:
        raise ValueError(f"template references unregistered safe view(s): {unknown_views}")

    grant = find_grant(delegator, task_type)
    if grant is None:
        raise PermissionError(f"{delegator} is not granted task_type {task_type}")

    scope = dict(template.get("default_scope", {}))
    scope.update(requested_scope or {})
    allowed_scope = grant.get("allowed_scope", {})

    tenant_id = allowed_scope.get("tenant_id", template.get("tenant_id"))
    if not tenant_id:
        raise ValueError("tenant_id is required by template or grant")

    for key in template.get("required_scope", []):
        if not scope.get(key):
            raise ValueError(f"missing required scope: {key}")

    requested_department = scope.get("department_id")
    allowed_departments = allowed_scope.get("department_ids")
    if requested_department and allowed_departments and requested_department not in allowed_departments:
        raise PermissionError(f"department_id {requested_department} is outside grant scope")

    budgets = dict(template.get("default_budgets", {}))
    budgets.update(grant.get("budget_overrides", {}))
    budgets.update(requested_budgets or {})
    max_budgets = template.get("max_budgets", {})
    for key, max_value in max_budgets.items():
        if key in budgets:
            budgets[key] = _clamp_budget(int(budgets[key]), int(max_value))

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=int(template.get("ttl_minutes", 30)))
    ).isoformat()

    payload = {
        "key_id": "dev",
        "task_id": task_id,
        "task_type": task_type,
        "tenant_id": tenant_id,
        "delegator": delegator,
        "actor": actor or template.get("actor", "agent:unknown"),
        "purpose": template["purpose"],
        "natural_language_goal": template.get("description", ""),
        "operations": template.get("operations", ["SELECT"]),
        "allowed_views": template.get("allowed_views", []),
        "allowed_commands": template.get("allowed_commands", []),
        "denied_columns": template.get("denied_columns", []),
        "row_scope": scope,
        "budgets": budgets,
        "delegation": template.get(
            "delegation",
            {
                "can_spawn_subagents": True,
                "child_capability_must_be_subset": True,
            },
        ),
        "expires_at": expires_at,
        "policy_version": template.get("policy_version", "template-v1"),
    }
    payload_text = canonical_json(payload)
    return payload, payload_text, sign(payload_text)
