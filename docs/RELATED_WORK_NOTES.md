# Related Work Notes

This note is a working source for the Related Work section of the first arXiv paper. It is not a final bibliography.

## Positioning Sentence

```text
Existing work scopes what an agent may call.
SessionBoundDB scopes what an agent may discover through open-ended database queries.
```

## Agent Identity

Representative area:

- Microsoft Entra Agent ID;
- agent identity providers;
- delegated agent permissions;
- agent-specific audit identity.

What this work contributes:

- recognizes that agents should have identities distinct from human users;
- supports delegated authority;
- improves auditability of agent actions.

SessionBoundDB relation:

- agrees that agent authority should not be ordinary user authority;
- uses actor/delegator separation in task tokens;
- focuses on database discovery rather than only API identity.

Difference:

```text
Agent identity says who the agent is.
SessionBoundDB constrains what the agent can discover for a task.
```

## Authenticated Delegation

Representative area:

- authenticated delegation;
- user-to-agent permission transfer;
- authority chains;
- proof that a user delegated to an agent;
- auditability of delegated execution.

What this work contributes:

- delegation semantics;
- provenance of authority;
- audit of who delegated and who executed.

SessionBoundDB relation:

- uses delegation as motivation;
- adds a database execution model for delegated analytical tasks.

Difference:

```text
Delegation work asks who delegated authority.
SessionBoundDB asks how the database bounds what the delegated agent can discover.
```

## Task-Scoped Authorization

Representative area:

- PAuth or similar task-scoped authorization systems;
- OAuth limitations for agents;
- task-scoped operation grants.

What this work contributes:

- argues that ordinary user/operator authority is too coarse for agents;
- introduces task-scoped authorization for service operation invocation.

SessionBoundDB relation:

- shares the task-scoped premise;
- should cite this work positively;
- should not claim to be first to task-scoped authorization.

Difference:

```text
Task-scoped API authorization constrains what operation an agent may invoke.
SessionBoundDB constrains what information an agent may discover through SQL.
```

## Database Security

Representative area:

- PostgreSQL row-level security;
- Oracle Virtual Private Database;
- Snowflake row access policies;
- column masking and dynamic data masking;
- context-aware database policies;
- Oracle Deep Data Security for agentic AI.

What this work contributes:

- row, column, identity, role, and context policy enforcement;
- mature database-side authorization mechanisms;
- database-enforced security close to the data.

SessionBoundDB relation:

- builds on the idea that the database should enforce access;
- uses safe views and runtime checks as prototype mechanisms.

Difference:

```text
Traditional database security centers identity, role, context, and policy.
SessionBoundDB centers task, capability, disclosure budget, and receipt.
```

## MCP and Tool Security

Representative area:

- MCP tool descriptors;
- tool permissions;
- tool isolation;
- tool execution security;
- prompt-injection-resistant tool use.

What this work contributes:

- agent-tool interoperability;
- structured tool invocation;
- tool isolation and permissioning.

SessionBoundDB relation:

- compatible with MCP as an integration layer;
- rejects MCP as the final enterprise data boundary.

Difference:

```text
MCP describes tools.
Task capabilities constrain data access.
```

## SaaS and Service Layers

Representative area:

- SaaS APIs;
- service-layer permission checks;
- internal enterprise dashboards;
- BI/reporting products.

What this work contributes:

- stable workflows;
- reviewed APIs;
- productized UX;
- predictable operational processes.

SessionBoundDB relation:

- does not replace all SaaS;
- targets read-heavy, analysis-heavy, temporary tasks that fixed screens do not cover well.

Difference:

```text
SaaS stabilizes recurring workflows.
SessionBoundDB bounds temporary analytical exploration.
```

## Claims To Preserve

Use:

```text
We explore a database-centered design point for task-scoped agent data access.
```

Use:

```text
SessionBoundDB combines task tokens, short-lived credentials, safe views,
disclosure budgets, and database-side receipts for open-ended SQL.
```

Avoid:

```text
We are the first task-scoped authorization system.
```

Avoid:

```text
We are the first database security system for agents.
```
