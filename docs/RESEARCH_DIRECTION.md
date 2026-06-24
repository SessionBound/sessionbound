# Research Direction

SessionBound started from a question:

> If agents can operate enterprise data directly, how should approved enterprise tasks become bounded database sessions?

The reimbursement approval demo helped us test the hardest part of that question: high-value workflow actions. It also clarified an important boundary. Approval SaaS is not just a data viewer. It contains role responsibility, human judgment, state machines, todo routing, audit records, exception handling, and compliance semantics. SessionBound can surround these invariants with controlled commands, but a full approval product is not the best first claim for the research prototype.

The first paper and GitHub release should prioritize the area where SessionBound is strongest:

> approved enterprise tasks becoming budgeted database sessions, where an agent can create a temporary analytical workspace without raw database access or permanent SaaS screens.

For arXiv v1, the immediate goal is narrower:

> show how task templates, applications, approvals, budgets, signed tokens, safe views, and receipts create enforceable database sessions for open-ended agent-generated SQL.

The project should not expand product features until the first paper draft is coherent.

## Primary Claim

Approved tasks should become budgeted database sessions instead of permanent database accounts or another fixed SaaS screen.

This is most compelling for:

- open-ended analysis;
- temporary audit and compliance checks;
- cross-table exploration;
- dynamic reports that do not justify a permanent SaaS page;
- intranet agents that need to work over private data while remaining bounded by the database.

## Main Demo Direction

The main demo should move from approval-flow depth to analytical task depth.

Recommended first-class demos:

- **Travel reimbursement anomaly analysis**
  - department totals;
  - category concentration;
  - unusually large claims;
  - employee monthly and yearly totals;
  - outlier merchants and cities.

- **Temporary audit workspace**
  - possible split reimbursement;
  - repeated merchant patterns;
  - same employee, same month, many similar claims;
  - claims near approval thresholds;
  - high-risk departments or categories.

- **Dynamic drill-down**
  - the user asks a natural-language question;
  - the agent queries safe views with joins, windows, grouping, and filters;
  - the workspace renders tables, metrics, and follow-up buttons;
  - the database enforces scope, columns, row budgets, and SQL safety.

These demos show why fixed SaaS screens are too narrow for agents. They also avoid turning the prototype into a conventional workflow product.

## What To Preserve From Approval Flow

The approval-flow work should remain in the repository. It is valuable as a boundary case.

It demonstrates:

- controlled commands for high-value writes;
- human-in-the-loop approval;
- database-enforced state transitions;
- cross-role handoff;
- ledger writes in the same transaction as payment;
- separation between agent assistance and human responsibility.

However, it should be positioned as a secondary case study:

> SessionBound can safely surround workflow actions, but its first advantage is not rebuilding all SaaS workflow UX. Its first advantage is turning approved analytical tasks into budgeted database sessions.

## Paper Positioning

Suggested framing:

- SaaS is excellent for stable, recurring workflows.
- Agents are excellent for temporary, underspecified tasks.
- Databases are the natural enforcement point for data boundaries.
- SessionBound combines a SaaS-style control plane with SessionBoundDB runtime enforcement.
- The result is a database-native substrate for budgeted agent sessions.
- The strongest novelty angle is not task-scoped authorization alone, but the control-plane-to-database-runtime path for approved enterprise tasks.

The paper should not claim that SessionBoundDB immediately replaces all SaaS. A stronger and more defensible claim is:

> SessionBound turns approved enterprise tasks into budgeted database sessions for AI agents.

The paper should preserve this sentence:

> Business users approve tasks, not database policies. Agents generate SQL, but databases enforce the approved boundary.

## Product Boundary

For the demo:

- keep the admin/control-plane UI minimal;
- keep approval flow as a supporting example;
- invest in analysis tasks, safe view design, SQL capability, and workspace rendering;
- avoid spending too much effort on SaaS-grade workflow polish;
- document limitations clearly.

This keeps the project sharp enough for an arXiv paper and focused enough for a GitHub release.
