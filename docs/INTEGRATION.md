# Developer Integration Guide

This guide explains how to adapt the demo to your own project.

The short version:

```text
Register safe views maintained by data platform or application developers.
Define task templates in FastAPI.
Expose only low-privilege dynamic DB credentials to Agents.
Expose signed task tokens to Agents.
Put task-scoped views and policies in Postgres.
Let Agents send open SQL through taskbound.run(sql).
```

Version 1 scope:

```text
Supported:
  single PostgreSQL database
  multiple task-scoped views
  complex SELECT
  JOIN, CTE, GROUP BY, HAVING, subquery, window function
  controlled write commands for workflow actions

Not yet supported:
  cross-database federation
  arbitrary external tools
  production-grade SQL parser or planner hooks
```

## 1. Architecture Contract

SessionBoundDB separates runtime authentication from task authorization:

```text
Credential Broker
  issues short-lived DB user/password
  answers: can this Agent runtime connect?

Task Authorization Service
  issues signed task token
  answers: what business task is authorized?

SessionBoundDB / Postgres
  binds task token to DB session
  answers: is this SQL inside the task boundary?
```

Agent developers should treat the system as a three-step protocol:

```text
POST /credentials  -> get dynamic DB credential
POST /tasks        -> get signed task token
POST /agent-query  -> send credential + task token + SQL
```

In a real Agent, `/agent-query` is optional. The Agent can directly connect to Postgres using the dynamic DB credential, then call:

```sql
SELECT taskbound.bind_task(:payload_text, :signature);
SELECT * FROM taskbound.run(:sql);
```

The demo keeps `/agent-query` so the full request is easy to inspect in a browser.

## 2. Where Tasks Are Defined

Before a task template can grant anything, developers or the data platform team must create safe views.

```text
Raw tables:
  app_data.expenses
  app_data.employees
  app_data.departments

Safe views:
  taskbound.expenses
  taskbound.employees
  taskbound.departments
```

Admin users do not grant raw tables. They compose task templates from registered safe views.

The demo safe view registry lives in:

```text
app/task_registry.py
db/004_safe_views.sql
```

Endpoint:

```text
GET /admin/safe-views
```

Example registry entry:

```json
{
  "view_name": "expenses",
  "database_object": "taskbound.expenses",
  "owner": "data-platform",
  "safe_columns": [
    "expense_id",
    "department_name",
    "employee_name",
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
  "not_exposed_columns": ["employees.phone", "employees.bank_account", "employees.salary"],
  "enforced_scope_claims": [
    "tenant_id",
    "row_scope.expense_month",
    "row_scope.department_id"
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
    "monthly_employee_total",
    "yearly_employee_total",
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

Production teams should manage safe views like code:

```text
db/004_safe_views.sql
db/views/expenses.sql
db/views/employees.sql
db/views/departments.sql
```

Use code review and tests for safe views. Admins should only select from the registered view catalog.

In this demo, the task template is currently defined in:

```text
app/taskbound_demo.py
```

Function:

```python
default_task(...)
```

This function builds the signed task payload:

```python
payload = {
    "key_id": "dev",
    "task_id": task_id,
    "tenant_id": "company_a",
    "delegator": "user:alice",
    "actor": "agent:travel-expense-analyst",
    "purpose": "monthly_travel_expense_anomaly_review",
    "operations": ["SELECT"],
    "allowed_views": ["expenses", "departments", "employees"],
    "denied_columns": [
        "employees.bank_account",
        "employees.phone",
        "employees.salary",
    ],
    "row_scope": {
        "expense_month": "2026-06",
        "department_id": "dep_sales",
    },
    "budgets": {
        "max_queries": 5,
        "max_unique_expense_rows": 4,
    },
    "expires_at": "...",
    "policy_version": "travel-demo-v1",
}
```

This demo now exposes task templates through the admin API and web page:

```text
GET  /admin
GET  /admin/task-templates
POST /admin/task-templates
GET  /admin/task-grants
POST /admin/task-grants
```

The current implementation stores templates and grants in FastAPI process memory:

```text
app/task_registry.py
```

That is enough for demo and local development. For a real project, replace this in-memory registry with one of:

```text
Postgres tables: task_templates, task_grants
YAML files loaded at startup
Your existing SaaS admin database
An internal policy service
```

If you prefer file-based configuration, move these definitions into:

```text
app/task_templates.yaml
```

Example:

```yaml
monthly_travel_expense_review:
  purpose: monthly_travel_expense_anomaly_review
  actor: agent:travel-expense-analyst
  operations:
    - SELECT
  allowed_views:
    - expenses
    - departments
    - employees
  denied_columns:
    - employees.bank_account
    - employees.phone
    - employees.salary
  required_scope:
    - tenant_id
    - expense_month
  optional_scope:
    - department_id
  default_budgets:
    max_queries: 30
    max_unique_expense_rows: 5000
  ttl_minutes: 30
```

Then `/tasks` should accept a `task_type`:

```json
{
  "task_type": "monthly_travel_expense_review",
  "scope": {
    "tenant_id": "company_a",
    "expense_month": "2026-06",
    "department_id": "dep_sales"
  },
  "budgets": {
    "max_queries": 30,
    "max_unique_expense_rows": 5000
  }
}
```

The FastAPI service should validate before signing a task token:

```text
Does this task_type exist?
Can this delegator create this task?
Are all required scope fields present?
Are scope values allowed for the delegator?
Are requested budgets below policy limits?
Does this task require approval?
```

After validation, FastAPI signs the task payload. The Agent never signs its own task token.

## 3. What The Database Must Know

The database does not understand natural language. It reads structured claims from the task token.

Current task claims are consumed in:

```text
db/003_runtime_core.sql
db/004_safe_views.sql
db/005_query_runtime.sql
db/006_commands_and_grants.sql
```

Important functions and views:

```sql
taskbound.bind_task(payload_text, signature_hex)
taskbound.claim(path text[])
taskbound.run(sql_text)
taskbound.expenses
taskbound.inspect_task_state()
taskbound.receipts()
```

The travel expense scope is enforced here:

```sql
CREATE OR REPLACE VIEW taskbound.expenses AS
SELECT ...
FROM app_data.expenses e
...
WHERE e.tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
  AND (
    taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
    OR e.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
  );
```

To adapt the project, create task-scoped views for your own tables:

```sql
CREATE OR REPLACE VIEW taskbound.orders AS
SELECT order_id, customer_id, order_date, total_amount, status
FROM app_data.orders
WHERE tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND order_date >= (taskbound.claim(ARRAY['row_scope', 'start_date']))::date
  AND order_date < (taskbound.claim(ARRAY['row_scope', 'end_date']))::date;
```

Do not grant Agents direct access to raw tables:

```sql
REVOKE ALL ON SCHEMA app_data FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA app_data FROM PUBLIC;
```

Only grant the constrained runtime role access to SessionBoundDB entry points:

```sql
GRANT USAGE ON SCHEMA taskbound TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.bind_task(text, text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.run(text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.inspect_task_state() TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.receipts() TO agent_runtime;
```

## 3.1 Complex SQL Support

Agents may issue complex analytical SQL as long as it reads from task-scoped views.

Supported demo examples:

```sql
-- JOIN
SELECT e.department_name, emp.employee_level, count(*) AS trips, sum(e.amount) AS total_amount
FROM expenses e
JOIN employees emp ON emp.employee_id = e.employee_id
GROUP BY e.department_name, emp.employee_level;

-- CTE
WITH dept_totals AS (
  SELECT department_name, sum(amount) AS total_amount
  FROM expenses
  GROUP BY department_name
)
SELECT *
FROM dept_totals
WHERE total_amount > 3000;

-- Window function
SELECT expense_id, department_name, amount,
       rank() OVER (PARTITION BY department_name ORDER BY amount DESC) AS dept_rank
FROM expenses;
```

The key rule:

```text
Complex SQL is allowed over safe views.
Raw tables and internal schemas remain inaccessible.
```

Current implementation note:

```text
The demo uses conservative string checks plus Postgres permissions.
Production should replace this with SQL AST validation or planner/executor hooks.
```

## 3.2 Controlled Write Commands

Safe views are for exploratory reads. High-value SaaS workflows need controlled writes.

The demo exposes controlled commands through:

```text
POST /agent-command
```

Database function:

```sql
SELECT taskbound.command(command_name, args_json);
```

Current commands:

```text
request_finance_review
finance_approve
department_approve
c_level_approve
return_expense_for_more_info
resubmit_expense
pay_expense
```

The command flow:

```text
User Agent proposes action
System Agent reviews and prepares arguments
SessionBoundDB command validates deterministic rules
Postgres executes transaction
Approval event / ledger entry / receipt is written
```

Example:

```json
{
  "command_name": "c_level_approve",
  "args": {
    "expense_id": "exp_002",
    "supervisor_agent_id": "agent:expense-supervisor",
    "comment": "Department approval passed, but aggregate reimbursement risk requires C-level approval."
  }
}
```

Rules enforced inside Postgres:

```text
request_finance_review:
  expense must be in task scope
  status must be submitted or resubmitted

finance_approve:
  expense must be in task scope
  delegator must be finance reviewer
  status must be finance_review_requested
  evidence and accounting checks must pass
  creates or enables the department approval handoff

department_approve:
  expense must be in task scope
  delegator must have department_manager role
  status must be department_approval_requested
  creates C-level handoff if single amount or aggregate reimbursement total requires it
  otherwise marks the expense payable

c_level_approve:
  expense must be in task scope
  delegator must have c_level role
  status must be c_level_approval_requested
  high-value single amount or aggregate reimbursement total must require C-level approval

pay_expense:
  finance user must execute it
  status must be payable or c_level_approved
  duplicate ledger is denied
  ledger entry and status update happen in one transaction
```

The C-level routing decision may depend on aggregate safe-view hints:

```text
single claim amount > 5000
or employee monthly reimbursement total > 10000
or employee yearly reimbursement total > 50000
```

These hints help the agent explain the workflow, but controlled commands must recompute or verify them before writing state.

The next role itself should also be data-driven:

```text
next_required_role = department_manager
or next_required_role = c_level
or next_required_role = budget_owner
```

Adding a new role should mean adding role registry records, task grants, command policies, and handoff policies. The SessionBoundDB runtime should continue to evaluate structured claims and should not need a new branch for each enterprise role.

This is the write-side counterpart to safe views:

```text
Read path:
  open SQL over safe views

Write path:
  named task commands with database-enforced workflow invariants
```

## 4. FastAPI Configuration Points

Current endpoint locations:

```text
app/api.py
```

### Credential Broker

Endpoint:

```text
POST /credentials
```

Current behavior:

```sql
CREATE ROLE agent_xxx LOGIN PASSWORD '...' VALID UNTIL '...';
GRANT agent_runtime TO agent_xxx;
```

Demo response:

```json
{
  "db_host": "postgres",
  "db_port": 5432,
  "db_name": "travel",
  "db_user": "agent_travel_analyst_6465e25b",
  "db_password": "temporary-password",
  "expires_at": "2026-06-23T01:30:00+08:00",
  "role": "agent_runtime"
}
```

Production recommendations:

```text
Protect /credentials with real service authentication.
Issue credentials only to trusted Agent runtimes.
Keep TTL short.
Restrict network access to the database.
Use Vault, IAM DB auth, mTLS, or a managed secret broker when available.
```

### Task Authorization

Endpoint:

```text
POST /tasks
```

Current behavior:

```text
Build task payload using default_task(...)
Sign canonical JSON with HMAC
Return payload_text and signature
```

Demo response:

```json
{
  "payload": {
    "task_id": "task_api_123",
    "delegator": "user:alice",
    "actor": "agent:travel-expense-analyst",
    "row_scope": {
      "expense_month": "2026-06",
      "department_id": "dep_sales"
    },
    "budgets": {
      "max_queries": 5,
      "max_unique_expense_rows": 4
    }
  },
  "payload_text": "...canonical-json...",
  "signature": "...hmac..."
}
```

Production recommendations:

```text
Use JWT, PASETO, or signed JSON with key rotation.
Put signing keys in KMS or Vault, not in the database.
Include policy_version.
Include expires_at.
Include delegator, actor, tenant, purpose, row_scope, operations, budgets.
Support revocation by task_id.
```

## 5. Agent Configuration

An Agent needs three configuration values:

```text
CONTROL_PLANE_URL=http://localhost:8000
AGENT_ID=travel-analyst
TASK_TYPE=monthly_travel_expense_review
```

Optional DeepSeek-backed demo Agent:

```text
DEEPSEEK_API_KEY=your-key
DEEPSEEK_MODEL=deepseek-v4-flash
```

Endpoint:

```text
POST /agent-chat
```

Input:

```json
{
  "user_request": "Analyze June travel reimbursement anomalies and approve small submitted expenses if policy allows.",
  "task_type": "monthly_travel_expense_review",
  "delegator": "user:alice",
  "department_id": "dep_sales",
  "max_rows": 50,
  "max_queries": 20
}
```

Flow:

```text
FastAPI issues dynamic DB credential
FastAPI issues task token
FastAPI gives DeepSeek the task schema and safe view registry
DeepSeek returns JSON plan: query steps and command steps
FastAPI executes each step through /agent-query or /agent-command
SessionBoundDB allows or denies each step deterministically
```

Important:

```text
DeepSeek does not get raw database privileges.
DeepSeek does not sign task tokens.
DeepSeek does not bypass workflow rules.
It only proposes SQL and commands.
```

The Agent flow:

```python
import requests
import psycopg

control_plane = "http://localhost:8000"

cred = requests.post(
    f"{control_plane}/credentials",
    json={"agent_id": "travel-analyst", "ttl_minutes": 15},
).json()

task = requests.post(
    f"{control_plane}/tasks",
    json={
        "task_id": "task_001",
        "department_id": "dep_sales",
        "max_rows": 5000,
        "max_queries": 30,
    },
).json()

conninfo = (
    f"postgresql://{cred['db_user']}:{cred['db_password']}"
    f"@{cred['db_host']}:{cred['db_port']}/{cred['db_name']}"
)

with psycopg.connect(conninfo, autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT taskbound.bind_task(%s, %s)",
            (task["payload_text"], task["signature"]),
        )
        cur.execute(
            "SELECT * FROM taskbound.run(%s)",
            ("SELECT * FROM expenses LIMIT 10",),
        )
        rows = cur.fetchall()
```

For browser demos, you can call `/agent-query` instead:

```json
{
  "credential": {
    "db_host": "postgres",
    "db_port": 5432,
    "db_name": "travel",
    "db_user": "agent_travel_analyst_6465e25b",
    "db_password": "temporary-password"
  },
  "payload_text": "...",
  "signature": "...",
  "sql": "SELECT * FROM expenses LIMIT 10"
}
```

## 6. How To Add Your Own Task

Use this checklist.

### Step 1: Define the business task

Example:

```text
Task type: customer_churn_analysis
Purpose: analyze churn risk for one business unit
Allowed data: customers, subscriptions, tickets
Denied data: payment_card, tax_id, raw_email_body
Scope: tenant_id, business_unit, date range
Budget: 50 queries, 10000 unique customer rows
TTL: 30 minutes
```

### Step 2: Add task payload generation

Add a function like:

```python
def customer_churn_task(...):
    return {
        "purpose": "customer_churn_analysis",
        "allowed_views": ["customers", "subscriptions", "tickets"],
        "denied_columns": [
            "customers.payment_card",
            "customers.tax_id",
            "tickets.raw_email_body",
        ],
        "row_scope": {
            "tenant_id": tenant_id,
            "business_unit": business_unit,
            "start_date": start_date,
            "end_date": end_date,
        },
        "budgets": {
            "max_queries": 50,
            "max_unique_customer_rows": 10000,
        },
    }
```

### Step 3: Add task-scoped database views

Example:

```sql
CREATE OR REPLACE VIEW taskbound.customers AS
SELECT customer_id, business_unit, plan, status, created_at
FROM app_data.customers
WHERE tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND business_unit = taskbound.claim(ARRAY['row_scope', 'business_unit']);
```

### Step 4: Add budget accounting if needed

The demo tracks unique `expense_id`.

For your own task, track a domain row id:

```text
customer_id
order_id
ticket_id
employee_id
document_id
```

Production implementations should move this from PL/pgSQL text checks into a parser, planner hook, or database extension.

### Step 5: Add UI examples

Update the demo page examples so reviewers can see:

```text
allowed query
scope filtering
sensitive field attack
raw table escape
cumulative budget attack
```

## 7. What Developers Should Not Do

Do not let Agents create arbitrary task tokens.

```text
Agent can request a task.
Control plane authorizes the task.
Database enforces the task.
```

Do not give Agents admin DB credentials.

```text
Good: short-lived role inheriting agent_runtime
Bad: postgres, app_admin, owner role, direct table grants
```

Do not rely on natural language task descriptions in the database.

```text
Good: structured row_scope, denied_columns, operations, budgets
Bad: database asks an LLM whether SQL is safe
```

Do not make MCP YAML the final data security boundary.

```text
MCP can describe tools.
SessionBoundDB enforces data access.
```

## 8. Minimal Production Hardening

Before using this pattern beyond a demo:

```text
Replace HMAC demo signing with JWT/PASETO/JWKS or KMS-backed signatures.
Protect /credentials and /tasks with strong authentication.
Move secrets out of docker-compose.
Use a real SQL parser or Postgres extension hooks.
Log denials outside user transactions.
Add task revocation.
Add role cleanup for expired dynamic DB users.
Add tests for scope, sensitive fields, raw table access, mutation attempts, pagination, and child-Agent budget sharing.
```

## 9. Core Message

For your project README, use this sentence:

```text
DB credentials authenticate the Agent runtime; task tokens authorize the work; Postgres enforces the boundary.
```
