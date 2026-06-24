# Task Capability Model

Task Capability is the central abstraction behind SessionBoundDB.

SessionBoundDB does not claim that task-scoped authorization is new by itself. The project focuses on applying task capabilities to open-ended relational exploration, where an agent may generate SQL dynamically and the database must constrain what information the agent can discover.

Traditional authorization asks:

```text
What can this user access?
```

Task-centric authorization asks:

```text
What can this agent do for this user, for this task, right now?
```

The difference matters because AI agents do not merely authenticate as users. They explore, compose queries, generate reports, and may attempt actions that were not enumerated in a fixed application screen. A user may have broad authority, but an agent should receive only the authority needed for the delegated task.

## Definition

A Task Capability is a signed, time-bounded, budgeted, auditable authorization object that describes what an agent may do while performing a specific task on behalf of a delegating user.

It is:

- **task-scoped**: bound to a concrete business task;
- **delegated**: issued by or for a user, but narrower than the user's full authority;
- **time-bounded**: expires quickly;
- **operation-bounded**: limits reads and optional controlled commands;
- **data-bounded**: exposes safe views, not raw tables;
- **field-bounded**: denies sensitive fields even if the user may normally access related objects;
- **row-bounded**: applies tenant, department, month, or other task scopes;
- **budgeted**: limits queries, rows, and cumulative disclosure;
- **auditable**: produces receipts for allowed and denied attempts;
- **database-enforced**: checked where the data is accessed, not only in application code.

## Example

User-centric permission:

```text
Alice can access expenses.
```

Task capability:

```text
Alice delegates an agent to analyze June 2026 travel expenses
for Sales department, read-only, excluding salary and bank fields,
for 15 minutes, with a 5000-row disclosure budget.
```

Example payload:

```json
{
  "delegator": "user:alice",
  "actor": "agent:travel-expense-analyst",
  "tenant": "company_a",
  "task": "monthly_expense_review",
  "purpose": "anomaly_analysis",
  "allowed_views": ["expenses", "departments", "employees"],
  "denied_columns": ["employees.salary", "employees.bank_account"],
  "row_scope": {
    "expense_month": "2026-06",
    "department_id": "dep_sales"
  },
  "operations": ["SELECT"],
  "budgets": {
    "max_queries": 20,
    "max_unique_rows": 5000
  },
  "expires_at": "2026-06-24T10:30:00Z"
}
```

## Lifecycle

1. A user or system delegates a task to an agent.
2. A control plane creates a task capability from a task template and user grant.
3. A credential broker issues a short-lived database credential.
4. The agent binds the signed task capability to the database session.
5. The agent submits SQL or controlled commands.
6. The database checks safe views, denied fields, row scope, operations, and budgets.
7. The database returns either data or a denial receipt.
8. Receipts are stored for audit and evaluation.

## Enforcement Boundary

Task Capability separates decision-making:

```text
The agent decides what to try.
The database decides what is allowed.
```

This is the key design point. The model does not require the agent to be fully trusted. The agent may generate creative SQL, try joins, or ask follow-up questions. The database remains responsible for enforcing the task boundary.

## Components

### Delegator

The user or principal on whose behalf the task is being performed.

### Actor

The agent runtime performing the task. This may be a local coding agent, an intranet LLM, or another automated system.

### Task and Purpose

The business reason for the capability. Purpose is not just metadata: it is useful for audit, policy review, receipt explanation, and future policy engines.

### Allowed Views

Agents query safe business views rather than raw tables. Safe views encode joins, row filters, computed workflow hints, and hidden sensitive fields.

### Denied Columns

Sensitive fields are explicitly denied even when they live near allowed business objects. For example, an analysis task may allow employee names and departments while denying salary, phone, and bank account fields.

### Row Scope

Row scope narrows data to the task context, such as tenant, date range, department, project, or region.

### Operations

Most analytical task capabilities should be read-only. High-value writes should be exposed as controlled commands, not arbitrary mutation SQL.

### Budgets

Budgets bound cumulative disclosure. They may include query count, returned rows, unique business objects, runtime, or future information-flow metrics.

### Expiration

Task capabilities should be short-lived. Long-lived agent database accounts reintroduce the same risk as user-centric authorization.

### Receipts

Every meaningful attempt should produce an auditable record:

- allowed query receipt;
- denied query receipt;
- budget overrun receipt;
- sensitive field denial receipt;
- controlled command receipt.

## Comparison

The key positioning is:

```text
Existing work scopes what an agent may call.
SessionBoundDB scopes what an agent may discover through open-ended database queries.
```

### RBAC

RBAC maps users to roles. It is stable and coarse. It does not express why an agent is accessing data now, for which task, under which budget.

### RLS

Row-level security answers whether a principal can see a row. It does not directly express task purpose, query budgets, denied fields, temporary delegation, or cumulative disclosure across repeated agent queries.

### MCP and Tool YAML

MCP describes tools and lets agents call them. It is useful for integration, but it should not be the final boundary for enterprise data. Task Capability places enforcement in the database session where SQL is executed.

```text
MCP describes tools.
Task capabilities constrain data access.
```

### Task-Scoped API Authorization

Task-scoped authorization systems constrain which service operations an agent may invoke for a delegated task. SessionBoundDB shares the task-scoped premise, but applies it to database discovery. The agent is not only choosing among predefined APIs; it may generate joins, aggregations, CTEs, and drill-down queries at runtime.

This shifts the enforcement question from:

```text
May the agent call this operation?
```

to:

```text
May the agent discover this information through this query?
```

### Database Security Products

Database-side row, column, identity, and context policies are necessary foundations. SessionBoundDB builds on this direction but moves the authorization center from identity to task. The capability includes task scope, TTL, disclosure budgets, safe views, and receipts.

### SaaS APIs

SaaS APIs expose fixed workflows and screens. They remain valuable for stable processes, but agents need open-ended analytical access that is narrower than raw database access and more flexible than fixed endpoints.

## Design Principle

Task Capability should be narrower than the delegating user's authority but broad enough for the delegated task.

This gives agents useful freedom without giving them permanent trust.
