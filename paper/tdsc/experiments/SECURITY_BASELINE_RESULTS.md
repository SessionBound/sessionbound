# Security Baseline Results

Raw file: `../raw_results/security_baseline_1783515117.json`.

| Property | Raw PostgreSQL | Role-only | Safe-view-only | RLS+Safe View+Short Credential+Audit | Full SessionBound |
|---|---|---|---|---|---|
| Blocks writes | No | Yes | Yes | Yes | Yes |
| Blocks raw table access | No | No | Yes | Yes | Yes |
| Blocks denied fields | No | No | Yes | Yes | Yes |
| Enforces row scope | No | No | Yes | Yes | Yes |
| Query budget | No | No | No | No | Yes |
| Disclosure budget | No | No | No | No | Yes |
| Payload aggregation blocking | No | No | No | No | Yes |
| Credential-token binding | No | No | No | No | Yes |
| Basic audit log | No | No | No | Yes | Yes |
| Receipt hash chain | No | No | No | No | Yes |
| Schema drift token invalidation | Not tested | Not tested | Not tested | Not tested | Yes |

The updated credential-token tests in
`../raw_results/credential_token_1783515640.json` show denial for
credential-id mismatch, cross-credential replay, wrong audience, wrong
actor, expired tokens, revoked task state, same-session rebind to a
second active task, safe-view registry-version drift, safe-view
policy-version drift, view-definition hash mismatch, and exposed-column
hash mismatch.

## Summary

Role-only grants block writes but do not hide raw tables, denied fields,
or out-of-scope rows. Safe-view-only blocks raw table access, denied
fields, and row-scope escapes, but lacks budgets, receipts, audit, and
token semantics. The RLS+safe-view+short-credential+audit baseline is a
strong conventional configuration: it blocks writes, raw table access,
denied fields, and row-scope escapes while recording a basic audit log.
It still does not bind a credential to a signed task token, enforce a
cumulative disclosure budget, emit a decision-bound receipt hash chain, or
invalidate task tokens on safe-view registry/version/hash drift. Full
SessionBound adds those mechanisms as one task-bound session contract.
