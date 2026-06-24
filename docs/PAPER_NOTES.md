# Paper Notes

Working title:

```text
From SaaS to Task-Bound Databases for AI Agents
```

GitHub headline:

```text
From SaaS to Task-Bound Databases for Your Agents
```

## Core Claim

Enterprise agents should receive task-scoped database capabilities, not permanent database accounts or another fixed SaaS screen.

## Thesis

Traditional SaaS applications made enterprise data safe by wrapping databases in fixed user interfaces and service APIs. AI agents challenge that assumption: they can generate queries, reports, forms, and temporary workspaces on demand. However, giving agents raw database access is unsafe.

SessionBoundDB proposes a database-centered substrate for agent-native enterprise software. A control plane grants a signed task token, a credential broker issues a short-lived runtime credential, and the database enforces safe views, disclosure budgets, and controlled commands.

For high-value enterprise workflows, SessionBoundDB also needs cross-role handoff. When the current task discovers a required next step that belongs to another role, the system should create a durable todo item rather than letting the current agent overreach. This todo should be an agent-readable handoff capsule, not just a human reminder: it carries intent, business object, reason, target role, recommended task type, scope, suggested queries, and allowed next commands. Claiming the handoff wakes the target user's agent through a structured event rather than a vague chat prompt.

Handoff is not only escalation. It also supports branch-and-return workflows. A finance reviewer may return a reimbursement for more information, creating an employee handoff. The employee then resubmits under a fresh task token, producing a `resubmitted` state and a new finance-review round. This distinction is important: the business object persists, but each role's work is a separate task round with its own authority, receipts, and audit trail.

The reimbursement policy also demonstrates sequential, policy-generated task rounds. Finance compliance review applies first. Department-manager approval follows for every compliant expense. C-level approval is generated only when high-value signals such as a large single claim, monthly employee reimbursement total, or yearly employee reimbursement total cross policy thresholds. This shows that SessionBoundDB is not limited to row-level access control or per-row workflow flags; safe views and controlled commands can share aggregate policy signals and generate the next role-specific todo in a sequential chain.

The demo uses four visible identities for clarity: employee, finance reviewer, department manager, and C-level approver. The framework remains role-parametric. Roles grant eligibility to request task tokens; they do not grant direct database access. Additional enterprise roles such as budget owner, cashier, accountant, director, or CEO should be modeled through registries for roles, task templates, command policies, handoff policies, approval chain policies, and state transitions.

## Contributions

1. A task-bound database capability model for AI agents.
2. A Safe View Specification for exposing business objects instead of raw tables.
3. A short-lived credential and signed task-token flow.
4. A controlled command model for high-value SaaS workflows.
5. An aggregate workflow-hint model for policies such as monthly/yearly employee totals.
6. A role-parametric workflow model based on task grants and sequential policy-generated task rounds rather than direct database roles.
7. A Task Handoff Queue and Handoff Capsule wake path for cross-role workflow continuation.
8. A task-round model for branch workflows such as return, supplement, resubmit, and re-review.
9. A dynamic workspace prototype using an intranet LLM such as DeepSeek.
10. An agent-agnostic evaluation harness using the same HTTP contract.

## Paper Structure

1. Introduction
2. Motivation: travel reimbursement and SaaS replacement
3. SessionBoundDB model
4. SessionBound Safe Views
5. Controlled commands for high-value workflows
6. Role-parametric workflow model
7. Cross-role task handoff, todo queues, and agent wake paths
8. Prototype implementation
9. Evaluation
10. Comparison with SaaS, MCP, RLS, views, and stored procedures
11. Limitations and future work
12. Conclusion

## Evaluation Plan

The current prototype has 11 scenarios. A stronger arXiv version should expand this to 30-50 scenarios:

- legal analysis queries;
- JOIN, CTE, window function, and subquery patterns;
- sensitive field attacks;
- raw table and schema escape attempts;
- tenant and scope violations;
- row budget and pagination attacks;
- controlled command state transitions;
- aggregate workflow constraints such as monthly/yearly reimbursement totals;
- cross-role handoff creation and claiming;
- return-for-more-info, resubmission, and re-review loops;
- duplicate and out-of-order financial actions;
- multiple upper-layer agents replaying the same task suite.

## Product Angle

The dynamic workspace is not the security boundary, but it may be the commercial entry point.

Private models such as DeepSeek can run inside enterprise networks and generate task-specific workspaces from safe views. This may replace many low-frequency SaaS screens while keeping data inside the enterprise boundary.
