# arXiv Preparation Guide

Working title:

```text
From User Permissions to Task Capabilities for AI Agents
```

Possible subtitles:

```text
Rethinking Database Authorization in the Agent Era
A Database-Centered Substrate for Task-Scoped Agent Data Access
```

## Goal

Prepare the first arXiv paper. The goal is not to finish a product. The goal is to publish a clear research position and establish the Task Capability abstraction in the context of database-centered agent data access.

## Core Thesis

```text
Traditional database authorization is user-centric.
AI agents require task-centric authorization.
Task Capability is the missing abstraction.
```

SessionBoundDB should be presented as:

```text
a PostgreSQL-based reference implementation of Task Capabilities for AI agents
```

The strongest novelty angle is:

```text
Existing work scopes what an agent may call.
SessionBoundDB scopes what an agent may discover through open-ended database queries.
```

## arXiv v1 Should Achieve

The first version should:

1. Define the problem clearly.
2. Explain why user permissions are too coarse for agents.
3. Define Task Capability.
4. Show how a database can enforce Task Capability.
5. Present SessionBoundDB as a working prototype.
6. Include a threat model.
7. Include related work and avoid overclaiming.
8. Include a research agenda.

It does not need to be a full SIGMOD/VLDB-quality system paper.

## Paper Length

Target:

```text
8-12 pages
```

Best first version:

```text
around 10 pages
```

Avoid very short position notes or very long unfocused system manuals.

## Required Structure

```text
1. Introduction
2. Motivation
3. Threat Model
4. Why User Permissions Fail for Agents
5. Task Capability Model
6. SessionBoundDB Design
7. Prototype
8. Evaluation
9. Related Work
10. Research Agenda
11. Limitations
12. Conclusion
```

## Required Figures

### Figure 1: Permission vs Capability

```text
Traditional:
User -> Role -> Permission -> Database

Agent Era:
User -> Task -> Capability -> Agent -> Database
```

Caption:

```text
Permissions authorize users. Capabilities authorize tasks.
```

### Figure 2: SessionBoundDB Architecture

```text
User -> Agent -> Credential Broker -> Task Token API
     -> Database Runtime -> Safe Views -> Audit Receipts
```

### Figure 3: Allowed vs Denied Query Examples

Allowed:

```sql
SELECT department_name, sum(amount)
FROM expenses
GROUP BY department_name;
```

Denied:

```sql
SELECT employee_name, salary
FROM employees;
```

## Positioning Rules

Avoid marketing language:

```text
SessionBoundDB is revolutionary.
SessionBoundDB replaces SaaS.
SessionBoundDB changes enterprise software.
```

Prefer:

```text
We argue that...
We propose...
We explore...
We present a reference architecture...
```

Avoid first claims:

```text
We are the first...
```

Prefer:

```text
To our knowledge...
We present one possible architecture...
We explore a database-centered design point...
```

Do not over-focus on UI. DeepSeek UI, dynamic workspace polish, full approval products, SaaS replacement, and complex workflow screens are motivation or future work, not the paper's core.

## Related Work Must Cover

- agent identity and delegated permissions;
- authenticated delegation;
- task-scoped authorization such as PAuth;
- PostgreSQL RLS;
- Oracle VPD;
- Snowflake row access policies;
- Oracle Deep Data Security / agentic database security;
- MCP and tool security;
- SaaS and service-layer APIs.

## arXiv Category

Primary:

```text
cs.DB
```

Secondary:

```text
cs.CR
```

The project is primarily about database authorization and enforcement, with security implications.

## Submission Checklist

```text
[ ] Paper is PDF, readable, and under arXiv size limits.
[ ] Title does not overclaim.
[ ] Abstract is academic, not marketing.
[ ] Related Work includes task-scoped authorization, delegation, and database security.
[ ] Threat Model is included.
[ ] Limitations are included.
[ ] Figures are clear.
[ ] No confidential company data.
[ ] No claim of "first" unless heavily qualified.
[ ] GitHub repo is clean.
[ ] README matches the paper's positioning.
[ ] Demo data is synthetic.
[ ] License is clear.
[ ] Author affiliation is approved.
```
