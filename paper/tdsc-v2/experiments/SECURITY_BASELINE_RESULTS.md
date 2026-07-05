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
| Credential-token binding | No | No | No | No | Partial |
| Receipts | No | No | No | No | Yes |
| Schema drift token invalidation | Not tested | Not tested | Not tested | Not tested | Not implemented |

`Partial` means the runtime validates signed tokens, expiration, and
revoked task state, but the separate credential-token tests show that it
does not yet enforce strict credential-id matching, actor matching,
audience validation, token replay blocking with a second credential, or
same-session rebind blocking.

## Summary

Role-only grants block writes but do not hide raw tables, denied fields,
or out-of-scope rows. Safe-view-only blocks raw table access, denied
fields, and row-scope escapes, but lacks budgets, receipts, and token
semantics. RLS-only enforces row scope and write blocking, but leaves raw
table structure and denied fields visible. Full SessionBound adds
database-enforced budgets, safe-view access, payload aggregation blocking,
and receipts; its measured prototype gap is strict credential-token
binding, including actor/audience validation and replay/rebind rejection.
