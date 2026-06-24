# Paper Outline

Working title:

```text
From User Permissions to Task Capabilities for AI Agents
```

Possible subtitles:

```text
Rethinking Database Authorization in the Agent Era
A Database-Centered Substrate for Task-Scoped Agent Data Access
```

## Core Claim

Traditional database authorization is user-centric. AI agents require task-centric authorization. Task Capability is the missing abstraction.

## Central Differentiator

```text
Existing work scopes what an agent may call.
SessionBoundDB scopes what an agent may discover through open-ended database queries.
```

## Intended Paper Type

Position paper / vision paper with a PostgreSQL reference prototype.

Target length:

```text
8-12 pages equivalent
```

## Required Structure

```text
1. Introduction
2. Motivation
3. Why Now?
4. Threat Model
5. Why User Permissions Fail for Agents
6. Task Capability Model
7. SessionBoundDB Design
8. Prototype
9. Evaluation
10. Related Work
11. Research Agenda
12. Limitations
13. Conclusion
```

## 1. Introduction

Goal: explain why the agent era needs a new authorization unit.

Key points:

- enterprise data has historically been protected by SaaS screens, APIs, RBAC, RLS, and application services;
- agents can generate open-ended SQL, temporary reports, and dynamic task workspaces;
- giving agents a user's long-lived database authority is unsafe;
- application-layer tool filtering is useful but should not be the final security boundary;
- the key shift is from user permissions to task capabilities.

Core sentences:

```text
Agents do not merely act as users; they act on behalf of users for bounded tasks.
Permissions authorize users; capabilities authorize tasks.
```

Required figure:

```text
Traditional:
User -> Role -> Permission -> Database

Agent Era:
User -> Task -> Capability -> Agent -> Database
```

Optional conceptual figure:

```text
Permission:
Identity -> Role -> Long-lived Access

Capability:
User -> Task -> Scope + TTL + Budget + Views -> Agent Action
```

## 2. Motivation

Use travel reimbursement anomaly analysis.

Contrast:

```text
User permission:
Alice can access expenses.

Task capability:
Alice delegates an agent to analyze June 2026 travel expenses
for the Sales department, read-only, excluding salary and bank fields,
for 15 minutes, with a 5000-row disclosure budget.
```

Make the point:

```text
The unit of authorization changes from a user to a task.
```

## 3. Why Now?

Key point:

```text
RLS is old.
Agents make the query generator dynamic.
Dynamic query generation makes task-scoped database enforcement newly important.
```

Suggested content:

- row-level security, views, and API permissions existed before modern agents;
- most enterprise access historically went through fixed screens, stable APIs, and pre-reviewed reports;
- agents generate joins, filters, drill-downs, and intermediate results at runtime;
- the authorization boundary must move closer to query execution;
- SessionBoundDB defines the task before execution, while SQL is generated during execution.

## 4. Threat Model

Assumptions:

- the agent is useful but not fully trusted;
- the agent may be prompt-injected;
- the agent may generate dangerous SQL;
- the agent may try to access sensitive fields;
- the agent may try to exceed row budgets;
- the agent may try to query outside tenant, department, date, or task scope.

In scope:

- sensitive field exfiltration;
- raw table access;
- tenant escape;
- task scope escape;
- mutation attempts;
- budget evasion;
- unsafe SQL generation;
- prompt injection effects.

Out of scope for v1:

- malicious DBA;
- compromised OS;
- compromised KMS;
- side-channel attacks;
- full formal verification;
- cross-database federation.

## 5. Why User Permissions Fail for Agents

Discuss:

### RBAC

```text
Roles are too coarse for delegated agent work.
```

### RLS

```text
RLS answers: can this user see this row?
Task Capability asks: can this agent perform this task now, under this scope and budget?
```

### MCP / Tool YAML

```text
MCP describes tools.
It should not be the final data security boundary.
```

### Direct DB Access

```text
Direct database access enables exploration, but is unsafe without task boundaries.
```

## 6. Task Capability Model

Define Task Capability as a structured authorization object.

Include Permission vs Capability:

| Property | User Permission | Task Capability |
|---|---|---|
| Bound to | User, role, or app | Delegated task |
| Lifetime | Long-lived | Short-lived |
| Scope | Broad or role-based | Tenant, object, field, time, purpose |
| Query model | Often fixed or unrestricted | Open-ended but bounded |
| Budget | Rare | First-class |
| Audit unit | User action | Task attempt / receipt |
| Revocation | Account or role change | Task expiry / token revocation |
| Agent suitability | Too broad | Designed for delegated agent work |

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
  "expires_at": "..."
}
```

Formal model:

```text
C = <delegator, actor, task, purpose, scope, operations, views, denied_fields, budget, ttl, receipt_policy>
```

A query is allowed only if the capability is valid, views and operations are allowed, denied fields are absent, scope and budget are respected, and a receipt is produced.

## 7. SessionBoundDB Design

Architecture flow:

```text
User delegates task
Control plane issues signed task token
Credential broker issues short-lived DB credential
Agent binds token to DB session
Agent submits SQL
Database enforces safe views, scope, denied fields, budgets
Database returns result or denial receipt
```

Design principle:

```text
The agent decides what to try.
The database decides what is allowed.
```

Main components:

- Control Plane;
- Credential Broker;
- Task Token;
- Safe Views;
- SessionBound Runtime;
- Query Budget;
- Audit Receipt;
- Controlled Commands, briefly;
- Handoff Queue, future work or secondary case.

Required figure:

```text
User -> Agent -> Credential Broker -> Task Token API
     -> Database Runtime -> Safe Views -> Audit Receipts
```

## 8. Prototype

Describe current implementation without overselling it.

Mention:

- FastAPI control plane;
- PostgreSQL runtime;
- signed task tokens;
- short-lived DB credentials;
- safe views;
- `taskbound.bind_task`;
- `taskbound.run(sql)`;
- denied sensitive fields;
- blocked raw table access;
- query and row budgets;
- evaluation harness using an agent-agnostic HTTP contract.

Do not make UI the center of the paper.

## 9. Evaluation

For arXiv v1, lightweight evaluation is enough. It should test whether the Task Capability boundary is enforceable across representative agent-generated SQL patterns and adversarial attempts.

Allowed scenarios:

- simple SELECT over safe views;
- JOIN;
- CTE;
- GROUP BY;
- window function;
- task-scoped analytical query.

Denied scenarios:

- salary access;
- bank account access;
- raw table access;
- internal schema access;
- other tenant;
- other department;
- other month;
- mutation SQL;
- DDL;
- repeated pagination attack;
- query budget overrun;
- row disclosure budget overrun.

Suggested metrics:

```text
Allowed query success rate
Unauthorized query denial rate
Sensitive field leakage rate
Budget enforcement correctness
Runtime overhead
Configuration size
```

Evaluation scenario table:

| Scenario | Expected Result |
|---|---|
| SELECT from safe view | Allowed |
| JOIN safe views | Allowed |
| CTE over expenses | Allowed |
| Window function ranking | Allowed |
| Query salary | Denied |
| Query raw app_data table | Denied |
| Query other month | Denied |
| DELETE expenses | Denied |
| Exceed query budget | Denied |

## 10. Related Work

Start with the Related Work Matrix so novelty is visible quickly.

Cover at least:

- agent identity and delegated permissions;
- authenticated delegation;
- PAuth or similar task-scoped authorization work;
- PostgreSQL RLS;
- Oracle VPD;
- Snowflake row access policies;
- Oracle Deep Data Security for AI / agentic systems;
- MCP and tool security;
- SaaS and service-layer APIs.

Preserve:

```text
Existing work often scopes what an agent may call.
SessionBoundDB scopes what an agent may discover through open-ended database queries.
```

## 11. Research Agenda

Include:

```text
RQ1: How should task capabilities be expressed and composed?
RQ2: How can databases enforce task-scoped authority for open-ended agent queries?
RQ3: How can systems measure cumulative disclosure across repeated queries?
RQ4: How should task capabilities support cross-role handoff?
RQ5: How can receipts prove that an agent stayed inside its delegated authority?
RQ6: How can task capabilities work across databases and external tools?
```

## 12. Limitations

Include:

- SQL validation is not production-grade yet;
- current prototype is single PostgreSQL database;
- HMAC signing is demo-level;
- no production KMS/JWKS integration yet;
- no formal verification yet;
- handoff is not the main contribution of v1;
- dynamic workspace is product layer, not security boundary;
- malicious DBAs and compromised hosts are out of scope.

## 13. Conclusion

End with:

```text
The agent era requires a shift from user permissions to task capabilities.
SessionBoundDB demonstrates one database-centered path for enforcing this shift:
short-lived credentials, signed task tokens, safe views, disclosure budgets,
and database-side enforcement for open-ended agent queries.
```

## What Not To Claim

Do not claim:

- SessionBoundDB replaces all SaaS;
- agents should perform human approvals;
- safe views can express every application workflow;
- this prototype is production-ready;
- MCP/YAML is useless;
- SessionBoundDB is the first task-scoped authorization system;
- SessionBoundDB is the first database security system for agents.
