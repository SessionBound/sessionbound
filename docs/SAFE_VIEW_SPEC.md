# SessionBound Safe View Specification

This is a minimal specification for the first research prototype. It is not a SQL standard. Its purpose is to make safe views repeatable enough that developers can apply SessionBoundDB to their own business objects.

## Definition

A SessionBound Safe View is an agent-facing relational interface that exposes business objects, not raw tables, and carries enough semantic, security, and workflow context for task-bound execution.

In other words:

```text
Raw tables are for applications and DBAs.
Safe views are for agents.
```

## Hard Requirements

A safe view should satisfy these requirements:

1. It must not expose raw internal table names or internal schemas as the public agent contract.
2. It must use business-semantic names for the view and columns.
3. It must exclude default sensitive fields such as salary, bank account, phone number, identity number, private notes, and credential material.
4. It must include scope fields needed by the task runtime, such as `tenant_id`, `department_id`, `employee_id`, `expense_month`, or business status.
5. It should expose workflow state explicitly, such as `status`, `requires_finance_review`, `requires_department_approval`, `requires_c_level_approval`, `next_required_role`, `next_task_type`, `approval_reason`, `can_request_more_info`, `can_resubmit`, or `can_pay`.
6. It should describe non-obvious derived fields through SQL comments or a metadata registry.
7. It should be read-only for agents. Writes go through controlled commands.
8. It should be bound to one or more task templates.

## Minimal Metadata

The prototype uses Python metadata today, but the intended registry shape is:

```json
{
  "view_name": "travel_expense_claims",
  "business_object": "travel_expense_claim",
  "maintainer": "finance_data_team",
  "allowed_tasks": ["monthly_travel_expense_review"],
  "scope_fields": ["tenant_id", "expense_month", "department_id"],
  "sensitive_fields_excluded": ["salary", "bank_account", "phone"],
  "workflow_fields": [
    "status",
    "requires_finance_review",
    "requires_department_approval",
    "requires_c_level_approval",
    "next_required_role",
    "next_task_type",
    "approval_tier",
    "approval_reason",
    "monthly_employee_total",
    "yearly_employee_total",
    "can_request_more_info",
    "can_resubmit",
    "can_pay"
  ],
  "recommended_commands": [
    "request_finance_review",
    "finance_approve",
    "department_approve",
    "c_level_approve",
    "return_expense_for_more_info",
    "resubmit_expense",
    "pay_expense"
  ]
}
```

This turns a view from a SQL convenience into an agent-facing data contract.

## Command Policies

Workflow hints should not be hard-coded in the UI or in agent prompts. A task registry should describe which hint column gates each controlled command:

```json
{
  "c_level_approve": {
    "label": "C-level approve",
    "entity_view": "expenses",
    "entity_key": "expense_id",
    "required_hint": "requires_c_level_approval",
    "hint_query_columns": [
      "expense_id",
      "amount",
      "status",
      "requires_finance_review",
      "requires_department_approval",
      "requires_c_level_approval",
      "next_required_role",
      "next_task_type",
      "approval_tier",
      "approval_reason",
      "monthly_employee_total",
      "yearly_employee_total",
      "can_pay"
    ],
    "blocked_message": "C-level approval is not required for this expense."
  }
}
```

The generic planner gate is:

```text
Find command policy.
Find the target entity row from the last query result.
Read policy.required_hint from that row.
If the hint is true, the agent may propose the command.
If the hint is false, the agent should explain the business state instead.
The database command still performs final enforcement.
```

To adapt SessionBoundDB to a new domain, define new safe view hint columns and command policies. The planner gate should not need business-specific code.

## Workflow Hints and Handoffs

A workflow hint does not automatically mean the current agent can execute the next command.

For example:

```text
requires_c_level_approval = true
```

means the business object needs a high-value approval step. It does not necessarily mean the current delegator is allowed to perform `c_level_approve`.

The command policy should distinguish three cases:

```text
required_hint = true and current task can execute command
  -> show a user-confirmable action

required_hint = true but another role must execute command
  -> create or suggest a handoff todo

required_hint = false
  -> explain the current business state; do not show a confirmation button
```

This distinction is essential for replacing high-value SaaS workflows. Safe views explain business state; command policies explain which actions are plausible; task grants decide who can receive the next task token; controlled commands enforce the final state transition.

Workflow hints should include both forward and backward branches. For example:

```text
requires_finance_review
  tells an agent that the object should enter finance compliance review

requires_department_approval
  tells the system that department-manager business approval is part of the chain

requires_c_level_approval
  tells the system that high-value approval is required after department approval

next_required_role
  tells the handoff queue which role should receive the next task for the current status

approval_tier
  tells the agent and audit log which approval level is being requested, if any

approval_reason
  explains whether the approval was triggered by standard department review, single amount, monthly total, yearly total, or budget exception

can_request_more_info
  tells a finance agent that the object may be returned to the originator

can_resubmit
  tells an employee agent that the object can be supplemented and submitted as a new task round
```

These hints are not permissions. They are agent-facing business state. Permissions come from task tokens and command policies; final enforcement comes from controlled commands.

## Aggregate Workflow Hints

Safe views should be able to expose aggregate policy signals, not just current-row facts.

Many SaaS workflows depend on cumulative risk:

```text
Single claim amount <= 5000
But employee monthly total > 10000
Or employee yearly total > 50000
Therefore additional business approval is still required.
```

This prevents "split spending" behavior where a user avoids a single-expense threshold by submitting many smaller claims.

A safe view can expose aggregate hints such as:

```text
single_amount_risk
monthly_employee_total
yearly_employee_total
requires_c_level_approval
next_required_role
next_task_type
approval_tier
approval_reason
```

The important rule:

```text
Agent may explain aggregate risk.
Database command must re-check aggregate risk before state transition.
```

Aggregate hints can be implemented with:

- joins against policy tables;
- grouped subqueries;
- window functions;
- materialized views refreshed by the application;
- database-maintained summary tables;
- stored functions used by both safe views and controlled commands.

For the first prototype, it is acceptable to compute these hints inside SQL views or helper functions. A production system should centralize policy thresholds so the safe view and controlled command do not drift.

## Example

The prototype implementation lives in [`db/004_safe_views.sql`](../db/004_safe_views.sql).

```sql
CREATE VIEW safe.travel_expense_claims AS
SELECT
  e.tenant_id,
  e.expense_id,
  emp.employee_name,
  d.department_name,
  e.expense_month,
  e.category AS expense_category,
  e.vendor_name,
  e.city,
  e.amount AS claim_amount,
  sum(e.amount) OVER (
    PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
  ) AS monthly_employee_total,
  sum(e.amount) OVER (
    PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
  ) AS yearly_employee_total,
  e.status,
  e.submitted_at,
  e.status IN ('submitted', 'resubmitted') AS requires_finance_review,
  (
    e.amount > 5000
    OR sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 10000
    OR sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 50000
  e.status IN ('finance_compliant', 'department_approval_requested') AS requires_department_approval,
  (
    e.amount > 50000
    OR sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 100000
    OR sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 300000
  ) AS requires_c_level_approval,
  CASE
    WHEN e.status = 'submitted' THEN 'finance_reviewer'
    WHEN e.status = 'finance_compliant' THEN 'department_manager'
    WHEN e.status = 'department_approved' AND (
      e.amount > 50000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
      ) > 100000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
      ) > 300000
    ) THEN 'c_level'
    WHEN e.status IN ('department_approved', 'c_level_approved') THEN 'finance_reviewer'
    ELSE NULL
  END AS next_required_role,
  CASE
    WHEN e.status = 'submitted' THEN 'finance_compliance_review'
    WHEN e.status = 'finance_compliant' THEN 'department_expense_approval'
    WHEN e.status = 'department_approved' AND (
      e.amount > 50000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
      ) > 100000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
      ) > 300000
    ) THEN 'c_level_expense_approval'
    WHEN e.status IN ('department_approved', 'c_level_approved') THEN 'expense_payment'
    ELSE NULL
  END AS next_task_type,
  CASE
    WHEN e.amount > 50000 THEN 2
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 100000 THEN 2
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 300000 THEN 2
    WHEN e.status IN ('finance_compliant', 'department_approval_requested') THEN 1
    ELSE NULL
  END AS approval_tier,
  CASE
    WHEN e.amount > 50000 THEN 'single_amount_over_c_level_limit'
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 100000 THEN 'monthly_total_over_c_level_limit'
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 300000 THEN 'yearly_total_over_c_level_limit'
    WHEN e.status IN ('finance_compliant', 'department_approval_requested') THEN 'standard_department_review'
    ELSE NULL
  END AS approval_reason,
  e.status = 'finance_review_requested' AS can_request_more_info,
  e.status = 'returned_for_more_info' AS can_resubmit,
  e.status = 'department_approved'
    AND NOT (
      e.amount > 50000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
      ) > 100000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
      ) > 300000
    )
    OR e.status = 'c_level_approved' AS can_pay
FROM app_data.expenses e
JOIN app_data.employees emp ON emp.employee_id = e.employee_id
JOIN app_data.departments d ON d.department_id = e.department_id;

COMMENT ON COLUMN safe.travel_expense_claims.can_pay IS
  'True when the claim has completed all required compliance and business approvals and has no existing payment ledger entry.';
```

The agent should query `safe.travel_expense_claims`, not `app_data.expenses`.

## Layering

Safe views should be layered instead of written as one giant view:

```text
Raw Tables
  -> Canonical Business Views
  -> Task-Specific Safe Views
```

Example:

```text
app_data.expenses
  -> business.travel_expense_claims
  -> task.monthly_travel_expense_review_claims
```

This lets a platform team define stable business objects once, then project them into multiple task-specific safe views.

## Authoring Workflow

1. Identify the business object the agent should reason about.
2. Join reference tables so the view speaks business language.
3. Remove sensitive fields by default.
4. Add task scope fields.
5. Add explicit workflow-state fields.
6. Add comments or registry metadata.
7. Bind the safe view to a task template.
8. Test with adversarial agent queries.

## What Safe Views Can Replace

Safe views can replace or reduce many:

- list APIs;
- report APIs;
- dashboard APIs;
- export APIs;
- read-only detail APIs;
- analytics pages;
- low-frequency internal SaaS screens.

## What Safe Views Do Not Replace Alone

Safe views should not be used alone for:

- multi-step write transactions;
- file upload;
- external payment gateway calls;
- complex human approval orchestration;
- rich document or design editors;
- high-volume OLTP write paths.

For those cases, SessionBoundDB uses controlled commands, service integrations, and explicit workflow state.

## First-Version Standard

For the first arXiv version, the safe view specification should prove this:

> A developer can expose business data to agents without exposing raw tables, and the database can still enforce task scope, sensitive-field restrictions, disclosure budgets, and workflow commands.
