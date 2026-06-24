# SessionBoundDB Runtime

SessionBoundDB is the database runtime for SessionBound. It is not the whole framework and not a new database. In this repository, SessionBoundDB is a PostgreSQL-based reference runtime.

SessionBoundDB binds approved task tokens to short-lived database sessions and enforces the approved boundary at SQL execution time.

## Runtime Contract

The runtime receives:

- a short-lived database credential from a credential broker;
- a signed SessionBound Token from the control plane;
- agent-generated SQL or a controlled command.

It enforces:

- token signature and expiration;
- allowed safe views;
- row scope;
- denied fields;
- read/write operation policy;
- query budget;
- disclosure budget;
- receipts for allowed and denied attempts.

## SQL Entry Point

The prototype uses controlled entry points:

```sql
SELECT taskbound.bind_task(:payload_text, :signature_hex);
SELECT * FROM taskbound.run(:sql);
SELECT * FROM taskbound.command(:command_name, :json_args);
```

The agent can generate SQL, but it cannot directly widen the database session's authority.

## Safe Views

Safe views are agent-facing business objects. They are not just SQL shortcuts.

A safe view should:

- use business object names;
- hide raw schemas and internal tables;
- exclude sensitive fields by default;
- include scope fields needed for enforcement;
- expose stable business semantics for joins and aggregation;
- be registered in the task template catalog.

The agent can join, aggregate, rank, and drill down over safe views. SessionBoundDB rejects raw table access and denied fields.

## Budgets

SessionBoundDB supports two budget families:

- query budget: how many attempts the session may make;
- disclosure budget: how much task-relevant information the session may reveal.

The current prototype approximates disclosure by tracking unique business row identifiers such as `expense_id`. Production systems may use richer units, sensitivity weights, aggregation thresholds, or entity-level accounting.

Disclosure budget is not differential privacy. It limits the authority of an approved task rather than primarily protecting a statistical population through noise.

## Receipts

Receipts are the audit unit of SessionBoundDB. A receipt should bind:

- task ID;
- delegator;
- actor;
- SQL hash or command name;
- views touched;
- decision;
- denial reason if any;
- returned row count;
- budget impact;
- timestamp.

Receipts make it possible to reconstruct what an agent attempted and why the database allowed or denied it.

## What SessionBoundDB Is Not

SessionBoundDB is not:

- a replacement for OAuth or agent identity;
- a replacement for PAuth-style service/tool authorization;
- a replacement for Oracle DDS or mature database security products;
- a data product marketplace;
- an MCP server by itself;
- an LLM safety classifier.

It is the database runtime for the SessionBound architecture:

```text
Business users approve tasks, not database policies.
Agents generate SQL, but databases enforce the approved boundary.
```
