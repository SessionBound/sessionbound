# Claims Audit

| Claim | Risk Level | Safer Version | Related Work Concern | Status |
|---|---:|---|---|---|
| "SessionBound bridges this trust gap by turning a business approval into a short-lived, budgeted, and auditable database session." | Low | Keep. It states the paper's design point without claiming priority. | None. | Accepted. |
| "SessionBoundDB ... enforces safe views, row scope, denied fields, operation limits, query budgets, disclosure budgets, and query receipts at SQL execution time." | Medium | SessionBoundDB is intended to enforce these boundaries in the prototype; final claims require functional validation receipts. | Needs real validation before final arXiv submission. | Keep with validation blocker. |
| "SessionBound targets a setting distinct from authenticated delegation, task-scoped service-operation authorization, and data-product marketplace access." | Low | Keep. | Must cite and distinguish Authenticated Delegation, PAuth, and Data Product MCP. | Accepted with references. |
| "SessionBoundDB is our PostgreSQL-based reference runtime..." | Medium | Keep only if prototype source and validation are available before final submission. | Implementation claim requires runnable source or clear availability limitation. | Blocked pending prototype. |
| "We implement SessionBoundDB as a PostgreSQL-based runtime..." | Medium | We prototype SessionBoundDB as a PostgreSQL-based runtime, with validation status reported separately. | Implementation claim requires code and validation evidence. | Blocked pending prototype. |
| "SessionBound reduces and accounts for what an agent can discover. It does not claim to eliminate all inference." | Low | Keep. | Correctly avoids overclaiming against inference attacks. | Accepted. |
| "The phrase disclosure budget can be misleading if interpreted as differential privacy." | Low | Keep. | Correctly distinguishes accounting budgets from DP. | Accepted. |
| "Oracle Deep Data Security and related systems show that database-enforced access control ... is an important industry direction." | Low | Keep. | Avoids claiming database-enforced access control is novel. | Accepted with Oracle citation. |
| "SessionBound does not claim that this broad direction is new." | Low | Keep. | Required safer framing. | Accepted. |
| "SessionBound is not a replacement for OAuth, authenticated delegation, PAuth, Data Product MCP, Oracle DDS, Zanzibar, or differential privacy." | Low | Keep. | Required non-replacement framing. | Accepted. |

## Required Safer Formulations

The manuscript already aligns with these safer formulations:

- SessionBound explores a task-control-plane design point for turning approved enterprise analytical tasks into budgeted database sessions.
- SessionBound complements database security products by adding task templates, task applications, approvals, signed task tokens, budgets, and task receipts.
- SessionBoundDB is a PostgreSQL runtime that enforces approved task boundaries over safe views and query/disclosure budgets.
- SessionBound reduces and accounts for the discoverable surface; it does not claim to eliminate all inference.

## Unsupported Claim Search

No final-manuscript claim was found that says SessionBound is:

- the first database-enforced access control system for agents;
- the first safe agent-generated SQL system;
- the first task-scoped authorization system;
- a replacement for Oracle DDS, PAuth, Data Product MCP, OAuth, Zanzibar, or SaaS;
- a system that prevents all inference attacks;
- differential privacy.

Main remaining risk: implementation and evaluation claims need real code and receipts before arXiv submission.

