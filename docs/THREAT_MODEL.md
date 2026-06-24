# Threat Model

SessionBoundDB assumes the upper-layer agent is useful but not fully trustworthy.

The agent may be intelligent, helpful, confused, overconfident, prompt-injected, or operating with incomplete context. The database boundary must remain meaningful even when the agent tries the wrong thing.

For the first arXiv paper, the threat model is intentionally scoped to database access by task-bound agents. It does not claim to solve all enterprise security problems.

## Protected Assets

- raw business tables;
- sensitive fields such as salary, phone, bank account, identity number, and private notes;
- tenant and department boundaries;
- cumulative disclosure budget;
- high-value business state transitions;
- ledger entries and financial integrity;
- audit evidence.

## Actors

### Delegating User

The user asks an agent to perform a business task. The user may have broad application access, but the delegated task should still be narrower than the user's total authority.

### Agent Runtime

The agent runtime receives a short-lived database credential and a signed task token. It can generate SQL and request controlled commands.

### Control Plane

The control plane defines task templates and grants users permission to request those tasks.

### Database Runtime

The database runtime is the final authority. It verifies task tokens, enforces budgets, exposes safe views, and executes controlled commands.

## In-Scope Threats

The first paper treats the following as in scope:

- sensitive field exfiltration;
- raw table access;
- tenant escape;
- task scope escape;
- mutation attempts;
- budget evasion;
- unsafe SQL generation;
- prompt injection effects.

### Dangerous SQL Generation

The agent may generate SQL that accesses sensitive columns, raw internal tables, internal schemas, or mutating operations.

Mitigation:

- only expose runtime entry functions to the agent role;
- deny raw schemas and internal state tables;
- block sensitive columns;
- reject mutating SQL in `taskbound.run`;
- route writes through controlled commands.

### Prompt Injection

Data or user text may instruct the agent to ignore policy, reveal secrets, query hidden fields, or perform unauthorized actions.

Mitigation:

- do not rely on prompts as the security boundary;
- enforce task scope in the database;
- require user confirmation for proposed high-value writes;
- enforce command invariants in database logic.

### Scope Escape

The agent may try to query other tenants, months, departments, or users.

Mitigation:

- task-bound safe views apply task scope;
- task tokens include scope and budgets;
- the runtime rejects access outside allowed views.

### Budget Evasion

The agent may paginate, repeat queries, or use alternate SQL forms to disclose more detail rows than allowed.

Mitigation:

- maintain cumulative task state;
- track unique business rows where possible;
- enforce query count and row budget.

### Sensitive Field Exfiltration

The agent may directly ask for fields such as salary or bank account.

Mitigation:

- exclude sensitive fields from safe views by default;
- deny known sensitive columns in the runtime;
- document sensitive-field exclusions in the safe view registry.

### High-Value Workflow Abuse

The agent may try to approve business-risk expenses without manager review, skip finance compliance review, pay before approval, or pay twice.

Mitigation:

- use controlled commands;
- enforce state transitions in the database;
- write ledger entries in the same transaction as payment state changes.

### Permanent Credential Exposure

The agent may leak or retain credentials.

Mitigation:

- issue short-lived runtime database users;
- grant only `agent_runtime` privileges;
- separate runtime authentication from task authorization.

## Out of Scope for the Prototype

- full SQL AST proof of safety;
- cross-database federation;
- production-grade identity provider integration;
- cryptographic key management through KMS/JWKS;
- denial logging outside rolled-back PostgreSQL statements;
- malicious database administrators;
- compromised host operating systems;
- compromised KMS/JWKS infrastructure;
- side-channel attacks;
- complete formal verification of business workflows.

## Security Principle

```text
An agent may decide what to attempt.
The database must decide what is allowed.
```

This is why SessionBoundDB treats prompts, tool descriptions, and generated UI as product layers, not security boundaries.
