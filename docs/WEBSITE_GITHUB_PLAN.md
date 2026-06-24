# Website and GitHub Plan

This document summarizes the current public-facing website and GitHub positioning plan.

## Public Positioning

Project name:

```text
SessionBound
```

GitHub repo:

```text
https://github.com/SessionBound/sessionbound
```

Website options:

```text
sessionbound.dev
sessionbound.ai
sessionbound.org
```

Homepage tagline:

```text
Approved tasks become budgeted database sessions.
```

Primary sentence:

```text
SessionBound turns approved enterprise tasks into budgeted database sessions for AI agents.
```

Expanded explanation:

```text
Business users request and approve tasks. The SessionBound control plane issues signed task tokens. SessionBoundDB binds those tokens to short-lived database sessions and enforces safe views, denied fields, row scope, query budgets, disclosure budgets, and receipts.
```

## Naming

```text
SessionBound        = full framework / research project
SessionBoundDB      = database runtime, initially PostgreSQL
SessionBound Control Plane = SaaS/control-plane layer
SessionBound Token  = signed task token
SessionBound Safe Views = agent-facing business objects
SessionBound Receipts = task/query audit records
```

## Do Not Present It As

SessionBound should not be presented as:

- another database;
- another MCP server;
- another data marketplace;
- a generic access-control library;
- a claim that all SaaS disappears.

It should be presented as:

```text
an enterprise task control plane + database runtime for bounded AI-agent analysis.
```

## Homepage Structure

### Hero

Title:

```text
SessionBound
```

Subtitle:

```text
Turn approved enterprise tasks into budgeted database sessions for AI agents.
```

Short paragraph:

```text
SessionBound lets enterprise agents generate open-ended SQL while the database enforces approved task boundaries, safe views, denied fields, query budgets, disclosure budgets, and receipts.
```

### Architecture

Use this architecture:

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

### Related Work Boundary

Use these short contrasts:

```text
Authenticated Delegation establishes who may act for whom.
PAuth verifies task-implied service/tool operations.
Oracle DDS provides identity/context-aware database enforcement.
Data Product MCP governs access to enterprise data products.
SessionBound turns approved enterprise tasks into budgeted database sessions.
```

## GitHub README Priorities

The README should quickly answer:

- What is SessionBound?
- What is SessionBoundDB?
- What problem does it solve?
- What does the demo prove?
- How do I run it?
- How does it differ from OAuth, PAuth, Oracle DDS, MCP, and Data Product MCP?

The repo should optimize for research clarity before product completeness.

## Domain Options

Preferred:

```text
sessionbound.dev
```

Fallbacks:

```text
sessionbound.ai
sessionbound.org
```
