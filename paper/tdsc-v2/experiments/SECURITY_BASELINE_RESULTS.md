# Security Baseline Results

Raw file: `raw_results/security_baseline_1783221673.json`.

| Property | Raw PostgreSQL | Role-only | Safe-view-only | RLS-only | Full SessionBound |
|---|---|---|---|---|---|
| Blocks writes | No | Yes | Yes | Yes | Yes |
| Blocks raw table access | No | No | Yes | No | Yes |
| Blocks denied fields | No | No | Yes | No | Yes |
| Enforces row scope | No | No | Yes | Yes | Yes |
| Query budget | No | No | No | No | Yes |
| Disclosure budget | No | No | No | No | Yes |
| Payload aggregation blocking | No | No | No | No | Yes |
| Credential-token binding | No | No | No | No | Yes |
| Receipts | No | No | No | No | Yes |
| Schema drift token invalidation | Not tested | Not tested | Not tested | Not tested | Yes |

The updated credential-token tests in
`raw_results/credential_token_1783224156.json` show denial for
credential-id mismatch, cross-credential replay, wrong audience, wrong
actor, expired tokens, revoked task state, same-session rebind to a
second active task, safe-view registry-version drift, safe-view
policy-version drift, and view-definition hash mismatch.

## Summary

Role-only grants block writes but do not hide raw tables, denied fields,
or out-of-scope rows. Safe-view-only blocks raw table access, denied
fields, and row-scope escapes, but lacks budgets, receipts, and token
semantics. RLS-only enforces row scope and write blocking, but leaves raw
table structure and denied fields visible. Full SessionBound adds
database-enforced budgets, safe-view access, payload aggregation blocking,
receipts, credential-token binding, and safe-view drift invalidation.
