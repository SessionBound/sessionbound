# Novelty Positioning

This note records the current literature-review positioning for the first arXiv paper.

The project should not claim novelty merely from:

- task-scoped authorization;
- delegated agent authority;
- database-side authorization;
- row-level or column-level database security;
- tool permissions for agents.

All of those areas already have active related work.

## Strongest Positioning

SessionBoundDB should be positioned as:

```text
A database-centered task capability substrate that lets agents perform
open-ended relational exploration while the database runtime enforces
task scope, safe views, sensitive-field policies, disclosure budgets,
and audit receipts.
```

The central contrast is:

```text
Existing work scopes what an agent may call.
SessionBoundDB scopes what an agent may discover through open-ended database queries.
```

This distinction should be repeated in the paper, README, and talks.

## Related Work Categories

### Agent Identity and Delegated Authorization

Systems such as agent identity providers and delegated OAuth-style authorization recognize that agents need their own identity and delegated authority.

They validate the assumption that agent authority should not be treated as ordinary user authority.

However, these systems generally focus on:

- authentication;
- delegated API authorization;
- service permissions;
- audit of who delegated and who executed.

They usually do not define a database execution model for:

- open SQL;
- relational discovery;
- safe views;
- query budgets;
- cumulative disclosure;
- database-side receipts.

### Authenticated Delegation Research

Recent delegation work focuses on authority chains:

```text
Who delegated?
Who executed?
Can the delegation be audited?
```

SessionBoundDB should cite this work as motivation. It should not claim that delegated authority itself is new.

The distinction is that SessionBoundDB asks:

```text
What information can the delegated agent discover through database exploration?
```

### Task-Scoped Authorization Systems

Task-scoped authorization is already an active idea. In particular, systems such as PAuth argue that ordinary OAuth-style authority is too coarse for agents and that agents need task-scoped authorization.

SessionBoundDB should not claim:

```text
We are the first to propose task-scoped authorization for agents.
```

Instead, the paper should say:

```text
Existing task-scoped authorization systems primarily constrain
what service operations an agent may invoke. SessionBoundDB constrains
what information an agent may discover through open-ended relational queries.
```

### Database Security Vendors

Database vendors are already extending database security for agentic AI. Oracle Deep Data Security is an important example category: database-side policies, row-level controls, column-level controls, context-aware access, and agent-aware enforcement.

SessionBoundDB should not claim:

```text
We are the first database-enforced authorization system for agents.
```

The distinction is the center of gravity:

```text
Traditional database security: Identity -> Role -> Context -> Policy
SessionBoundDB: Task -> Capability -> Database Discovery -> Receipt
```

SessionBoundDB combines database-side enforcement with task tokens, safe views, disclosure budgets, task TTL, and agent-generated analytical workspaces.

### MCP Security

MCP and tool-security discussions focus on:

- tool permissions;
- tool isolation;
- tool execution;
- tool descriptions.

SessionBoundDB is compatible with MCP, but it rejects MCP as the final data boundary.

Recommended wording:

```text
MCP describes tools.
Task capabilities constrain data access.
```

## Claims To Avoid

Avoid:

```text
We are the first task-scoped authorization model.
```

Avoid:

```text
We are the first secure database for agents.
```

Avoid:

```text
SessionBoundDB replaces SaaS.
```

## Claims To Prefer

Prefer:

```text
We argue that the agent era requires a shift from user permissions
to task capabilities.
```

Prefer:

```text
We present SessionBoundDB, a database-centered reference architecture
that enforces task capabilities over open-ended agent-generated
database queries.
```

Prefer:

```text
SessionBoundDB scopes what an agent may discover, not only what it may call.
```

## One-Sentence Memory Hook

```text
Existing work scopes what an agent may call; SessionBoundDB scopes
what an agent may discover through open-ended database queries.
```
