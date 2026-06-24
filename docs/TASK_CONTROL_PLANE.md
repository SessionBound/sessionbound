# SessionBound Control Plane

SessionBound turns approved enterprise tasks into budgeted database sessions for AI agents.

The control plane is the layer where enterprise intent becomes structured authorization. It is SaaS-like because it handles users, roles, grants, approvals, templates, and audit records. It is not a traditional SaaS service layer because it does not need to implement every analytical query as a fixed API.

## Main Flow

```text
Task Template
  |
  v
Task Application
  |
  v
Task Approval / Grants / Budgets
  |
  v
Signed Task Token
  |
  v
Agent-generated SQL
  |
  v
SessionBoundDB Runtime
  |
  v
Safe Views + Budgets + Receipts
```

## Core Objects

### Task Template

A task template is a reusable enterprise-approved task type. It defines:

- allowed safe views;
- required scope fields;
- denied fields;
- allowed operations or controlled commands;
- default TTL;
- query budget;
- disclosure budget;
- approval rules;
- receipt policy.

Templates are maintained by data platform, application, or governance administrators. Business approvers select and approve task instances; they do not write database predicates.

### Task Application

A task application is a request to execute a specific task instance:

```json
{
  "task_type": "monthly_expense_anomaly_review",
  "requested_by": "user:alice",
  "actor": "agent:travel-expense-analyst",
  "scope": {
    "tenant_id": "company_a",
    "expense_month": "2026-06",
    "department_id": "dep_sales"
  },
  "reason": "Review unusual travel reimbursement patterns before quarterly close."
}
```

The application is not a database grant. It is a business request that may be checked against user grants, role rules, data sensitivity, and budget policy.

### Task Approval

Approval may be automatic or manual:

- automatic when a user already has a grant for a low-risk template and scope;
- manager-approved for department-scoped work;
- data-owner-approved for sensitive data;
- compliance-approved for higher-risk analysis;
- budget-owner-approved when the requested disclosure budget is large.

The database does not need to know the organization's approval routing. It enforces the signed result.

### Signed Task Token

After approval, the control plane signs a structured task token:

```json
{
  "task_id": "task_2026_06_sales_expense_review",
  "task_type": "monthly_expense_anomaly_review",
  "delegator": "user:alice",
  "actor": "agent:travel-expense-analyst",
  "tenant_id": "company_a",
  "purpose": "internal_audit_analysis",
  "allowed_views": ["expenses", "employees", "departments"],
  "denied_columns": ["employees.salary", "employees.bank_account", "employees.phone"],
  "row_scope": {
    "expense_month": "2026-06",
    "department_id": "dep_sales"
  },
  "operations": ["SELECT"],
  "budgets": {
    "max_queries": 20,
    "max_unique_expense_rows": 5000
  },
  "expires_at": "2026-06-24T10:30:00Z",
  "policy_version": "expense-review-v1"
}
```

The token is database-consumable. It should be short-lived, scoped, revocable, and signed by a trusted service.

## Boundaries

Authenticated Delegation establishes who may act for whom.

PAuth verifies task-implied service/tool operations.

Oracle DDS provides identity/context-aware database enforcement.

Data Product MCP governs access to enterprise data products.

SessionBound adds task templates, task applications, approvals, budgets, signed task tokens, and task receipts.

## Production Notes

A production control plane should support:

- template versioning;
- approval history;
- token revocation;
- policy-version invalidation;
- user/role/grant registry;
- task receipt search;
- budget escalation requests;
- integration with identity providers and KMS/JWKS signing.

The prototype keeps this intentionally small so the first paper can focus on the architecture.
