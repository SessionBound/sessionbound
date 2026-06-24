# Citation Notes

- Use OAuth RFC 6749 as the baseline for delegated API authorization, not as a direct database-session system.
- Use Authenticated Delegation for verifiable agent delegation and accountability. SessionBound should be described as complementary, not competing.
- Use PAuth for precise task-scoped authorization over service/tool operations. SessionBound should not claim that PAuth solves the same database-session problem.
- Use Data Product MCP as the closest enterprise data governance/MCP comparison. Distinguish its data-product access governance from SessionBound's approved task session as the runtime object.
- Use Oracle Deep Data Security as the strongest public database-enforced authorization comparison. SessionBound must not claim that database-enforced access control for AI agents is new.
- Use Zanzibar for relationship-based authorization at scale. SessionBound should distinguish relationship/object permission checks from budgeted exploratory SQL sessions.
- Use PostgreSQL Row-Level Security as database-native prior art and mechanism context, not as the full SessionBound model.
- Use MCP specification for protocol background only.
- Use Dwork and Roth to clarify that SessionBound disclosure budgets are accounting controls, not differential privacy.
- Use Adam and Wortmann for historical database inference-control context.

