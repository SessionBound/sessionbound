# TDSC Novelty Review - 2026-07-10

Purpose: define the narrow contribution that can survive related-work scrutiny
after the latest evidence audit. This review assumes the current reference
monitor must be rebuilt before strong claims can be restored.

## Verdict

The paper should not claim novelty in task-scoped authorization, delegated agent
authority, database-side fine-grained authorization, data-product governance,
MCP-based enterprise querying, information-flow control, safe views, RLS/VPD,
query auditing, inference control, or audit logs.

The only defensible novelty route is a composition claim:

> A database-centered task-capability runtime for open-ended agent SQL that
> binds a signed task token, short-lived credential, approved safe-view
> registry, cumulative disclosure budget, drift invalidation, and
> receipt-bearing execution into one database-session contract.

This claim is currently conditional, because the native reference monitor and
cumulative-budget evidence are not yet sufficient.

## Adjacent Work That Narrows the Claim

| Area | Representative work | Effect on SessionBound positioning |
|---|---|---|
| Task-scoped agent operation authorization | PAuth / precise task-scoped authorization for agents | SessionBound cannot claim to introduce task-scoped authorization for agents. It may contrast operation/tool-call authorization with open-ended SQL disclosure control. |
| Database-enforced access for agentic AI | Oracle Deep Data Security and longstanding RLS/VPD/DDS-style controls | SessionBound cannot claim to be the first database-enforced agent security system. It must emphasize task-token/session composition, budget state, and receipts. |
| Governed enterprise data access via MCP/data products | Data Product MCP and enterprise data-product marketplaces | SessionBound cannot claim to be the first governed agentic enterprise data-access architecture. It may claim a lower-level PostgreSQL execution contract after approval/data-product selection. |
| Agent information-flow security | IFC/Fides-style agent planners | SessionBound cannot claim broad agent information-flow security. It is an operational database authority boundary, not a formal IFC planner. |
| Classical database privacy/security | Hippocratic/purpose-based DBs, RLS/VPD, query auditing, statistical inference control, differential privacy | SessionBound cannot claim complete inference control or formal privacy. Budgets are operational controls, not DP. |
| Capability and delegation systems | Capabilities, OAuth/delegated authorization, ABAC/XACML, Zanzibar | SessionBound cannot claim novelty in authorization foundations. The contribution must be the database-session state machine and evidence-backed enforcement. |

## Claims To Delete Or Avoid

- "first task-scoped authorization for agents"
- "first secure database for agents"
- "database-native agent security is new"
- "MCP/data products do not address governed enterprise data access"
- "safe views plus receipts are individually novel"
- "external PEP proves primitives are not enough"
- "reference monitor is complete" before the native bypass classes are fixed
- "10k-row materialization/disclosure bottleneck" before a large-output rerun
- "payload aggregation blocked" unless API and native coverage are unified
- "small-group/window inference handled" unless window variants are tested

## Claims That May Survive After Rebuild

The paper may be reframed around these narrower statements:

- Existing systems often authorize who/what/which operation; SessionBound studies
  how an approved task becomes a bounded SQL session.
- The design point is not a new primitive but a composition: task token,
  credential binding, safe-view registry, cumulative budget, drift checks, and
  receipts in one database execution contract.
- The database, not the prompt, is the enforcement boundary for open-ended SQL.
- The budget is an operational authority budget for disclosed result tuples or
  declared entities, not differential privacy.
- The reference monitor is a PostgreSQL research prototype and must be evaluated
  as such.

## Manuscript Rewrite Targets

1. Abstract: remove any implication that current results establish complete
   native enforcement or large-result scale behavior. State "research
   prototype" and "diagnostic experiments" until rebuilt evidence exists.
2. Introduction: keep the motivating problem, but narrow novelty to the
   database-session contract rather than task-scoped authorization itself.
3. Contributions: change from "we provide" language to evidence-scoped
   contributions. Any native reference-monitor contribution must be marked as
   blocked until bypass regressions pass.
4. Security invariants: split intended invariants from validated invariants.
   I3/I4/I6/I7/I9 are currently design goals, not established properties.
5. Evaluation: separate API/wrapper, native executor, hook-only, external PEP,
   and scale experiments. Each table must show returned rows and cumulative
   budget semantics.
6. Related work: explicitly acknowledge PAuth, Oracle DDS/RLS/VPD,
   Data Product MCP/MCP, IFC/Fides, purpose-based DBs, query auditing,
   inference control, and DP.
7. Conclusion: avoid broad "database decides" triumph language until the
   reference monitor has been repaired. End with a rebuild agenda if needed.

## Review-Resistant Positioning Paragraph

SessionBound should be described as follows after the rebuild:

> SessionBound explores a database-centered execution contract for approved
> agent tasks. Rather than claiming new authorization primitives, it composes a
> signed task token, short-lived credential binding, safe-view registry,
> cumulative disclosure budget, drift invalidation, and receipt-bearing
> execution around open-ended PostgreSQL queries. This complements
> task-scoped operation authorization, data-product governance, and
> database-native identity/context policies by focusing on what a delegated
> agent may disclose through a bounded SQL session.

This paragraph should not be used as a strong claim until the artifact supports
the cumulative external baseline and repaired native reference monitor.
