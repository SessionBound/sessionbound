# Comparison

SessionBound is not trying to replace every existing security or application pattern. It combines an enterprise task control plane with SessionBoundDB, a database runtime for budgeted AI-agent sessions.

## Summary

| Approach | Main Boundary | Good At | Weakness for Agents |
|---|---|---|---|
| SaaS service layer | fixed APIs and UI | product workflows, stable screens | agents are boxed into prebuilt actions |
| MCP tools / YAML | tool descriptions | exposing existing capabilities | often recreates SaaS APIs as tools |
| Data Product MCP | governed data products | enterprise data-product discovery and access | governs data products rather than turning approved tasks into database sessions |
| Agent identity / delegation | agent identity and delegated API permissions | who delegated, who acted | usually does not bound open SQL discovery |
| Task-scoped API authorization | service operation grants | constraining what an agent may call | less focused on what SQL can reveal |
| PostgreSQL RLS | user/table row policy | row-level isolation | does not express task budget or workflow intent alone |
| Oracle VPD / Snowflake row policies | mature database policies | row/context policy enforcement | identity/context-centered rather than task-budget-centered |
| Database views | SQL projection | hiding tables, simplifying joins | no task token, budget, or command model by itself |
| Stored procedures | controlled writes | high-value transactions | poor for open-ended analysis alone |
| API gateway | request mediation | service perimeter | usually not aware of SQL result disclosure history |
| Direct agent DB access | agent productivity | fast exploration | dangerous without task-scoped data boundary |
| SessionBound | approved enterprise task | budgeted database sessions for agent analysis | early prototype, needs production hardening |

## Versus SaaS Service Layers

Traditional SaaS builds fixed pages and APIs:

```text
controller -> service -> repository -> permission check -> frontend table/form/chart
```

This is reliable, but expensive for low-frequency internal workflows. SessionBound moves read-heavy and analysis-heavy work toward:

```text
task template -> task approval -> signed task token -> budgeted database session -> agent-generated workspace
```

High-value writes still need controlled commands. SessionBound does not claim that safe views replace every service.

## Versus MCP YAML

MCP tools often define tool-level actions:

```text
query_expenses(month, department)
get_employee(employee_id)
finance_approve(expense_id)
department_approve(expense_id)
c_level_approve(expense_id)
```

That can recreate the SaaS service layer as agent-callable functions.

SessionBound defines a task-level boundary:

```text
Alice delegated this agent to review June travel expenses
for company_a, with no salary/bank access and a 50-row disclosure budget.
```

Inside that boundary, the agent can use open-ended SQL over safe views.

The core distinction:

```text
MCP describes tools.
SessionBound turns approved tasks into budgeted database sessions.
```

## Versus Data Product MCP

Data Product MCP is an important adjacent direction. It connects enterprise data-product governance with MCP so agents can discover, request access to, and query governed data products.

SessionBound has a different center:

```text
Data Product MCP governs access to enterprise data products.
SessionBound turns approved enterprise tasks into budgeted database sessions.
```

The two can be complementary. A SessionBound control plane could approve a task whose safe views are backed by governed data products. But SessionBound's contribution is the task lifecycle and database runtime boundary: task templates, applications, approvals, TTLs, budgets, signed task tokens, safe views, and receipts.

## Versus Agent Identity and Delegated Authorization

Agent identity systems and delegated authorization frameworks are important. They answer:

```text
Who is the agent?
Who delegated authority to it?
What API permission did it receive?
```

SessionBound assumes those questions matter, but asks a database-specific question:

```text
What database session should this approved enterprise task create?
```

## Versus Task-Scoped API Authorization

Systems such as PAuth argue that OAuth-style authority is too coarse and that agents need task-scoped authorization. SessionBound agrees with that premise.

The difference is the execution surface:

```text
Task-scoped API authorization constrains what operation an agent may invoke.
SessionBound creates a budgeted database session where SessionBoundDB constrains what SQL may discover.
```

## Versus PostgreSQL RLS

RLS is valuable and compatible with SessionBoundDB. But RLS is usually user/table oriented:

```text
Can this role see this row?
```

SessionBound asks:

```text
Can this agent, for this delegated task, at this time,
with this budget and workflow state, see or change this data?
```

That adds task intent, TTL, query budget, cumulative disclosure, and command semantics.

## Versus Oracle VPD, Snowflake Row Policies, and Agentic Database Security

Database security products and features already provide mature row-level, column-level, identity-aware, role-aware, and context-aware controls. Oracle VPD, Snowflake row access policies, and Oracle Deep Data Security for agentic systems are important related directions.

SessionBound should not claim to be the first database security system for agents. Its design point is different:

```text
Traditional database security: Identity -> Role -> Context -> Policy
SessionBound: Task Template -> Task Application -> Approval -> Signed Token -> Budgeted Database Session -> Receipt
```

SessionBound makes task approval, TTL, disclosure budget, safe views, and receipts first-class elements of the agent database session.

## Versus Plain Views

Plain views can hide tables and simplify schemas. SessionBound Safe Views add conventions:

- business object naming;
- sensitive-field exclusion;
- task scope fields;
- workflow state fields;
- registry metadata;
- task template binding.

The view becomes an agent-facing data contract rather than a query shortcut.

## Versus Stored Procedures

Stored procedures are excellent for controlled writes. SessionBound uses this idea for commands such as approval, finance review, and payment.

But stored procedures alone are too rigid for agent analysis. Agents need to explore, join, rank, summarize, and test hypotheses. That is why SessionBound combines safe views for reads with controlled commands for writes.

## Versus Direct Database Access by Agents

Modern coding and analytical agents are powerful upper-layer systems. The stronger the agent is, the more useful SessionBound becomes:

- the agent can generate better SQL;
- the database still rejects work outside the task;
- the same task-token contract can test multiple agents.

The project should not compete with general-purpose coding agents. It should give them a safer database substrate.

## Practical Claim

SessionBound can reduce the amount of SaaS code needed for:

- internal reporting;
- operational analysis;
- low-frequency admin workflows;
- approval dashboards;
- read-only investigation tools;
- dynamic intranet workspaces.

It is not intended to eliminate all product software, external integrations, or high-volume transactional systems.
