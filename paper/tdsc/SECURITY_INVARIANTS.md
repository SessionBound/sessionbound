# SessionBoundDB Security Invariants

These invariants define the enforcement contract implemented by the prototype
and used to structure the adversarial evaluation. They are not a formal proof
of absence of semantic inference.

## State

SessionBoundDB models an active database execution state as:

```text
S = <T, C, V, B, R>
```

where:

- `T` = signed task token.
- `C` = credential/session binding.
- `V` = safe-view registry and policy version.
- `B` = remaining budget vector.
- `R` = receipt chain.

The decision function is:

```text
decide(q, S) -> allow(result, S') or deny(reason, receipt, S')
```

The prototype implements this through `taskbound.bind_task(...)`, API-layer AST
preflight, the experimental `sessionbound_guard` PostgreSQL hook path,
`taskbound.run(...)`, safe views, budget state, and query receipts.

## Invariants

I1. No execution without active task binding.

`taskbound.run(sql)` calls `taskbound.require_payload()` and fails if no
task is bound to the current backend.

I2. Bound token must match the session credential, actor, audience, expiry,
nonce/jti-equivalent token digest, and revocation state.

The prototype checks signature, expiry, audience, dynamic credential id,
session login role, actor, credential revocation, token digest, replay across
credentials, and same-session rebind.

I3. Referenced relations must belong to the approved safe-view registry.

The signed token carries `allowed_views`; the API-layer AST preflight extracts
referenced relations and rejects unapproved relations before invoking
`taskbound.run`. The database also computes approved safe-view OIDs during
`taskbound.bind_task(...)`, stores them in trusted SUSET GUCs, and the
`sessionbound_guard` hook rejects relation OIDs outside that registry.

I4. Direct access to denied fields, raw schemas, catalog escape, mutation,
DDL, and blocked payload aggregation is denied.

The prototype detects denied field names and aliases, `app_data`,
`pg_catalog`, `information_schema`, mutation/DDL/utility statements, recursive
CTEs, set-operation stacking, and high-risk payload aggregation functions such
as `json_agg`, `jsonb_agg`, `array_agg`, `string_agg`, `xmlagg`,
`row_to_json`, `json_build_object`, and `jsonb_build_object`. These checks run
through both the API-layer AST validator and the database-resident hook path for
the evaluated cases.

I5. Scope predicates or session-bound claims constrain visible rows.

Safe views call `taskbound.claim(...)` and constrain rows by tenant,
expense month, and optional department. Out-of-scope safe-view predicates
therefore return zero rows rather than raw out-of-scope data.

I6. Budget state is monotonic: an allowed query consumes budget, or the query
is denied.

The prototype increments query count and tracks unique exposed `expense_id`
values when budget accounting is enabled. The hardening overhead script also measures
the supported budget-disabled ablation to isolate this cost.

I7. Every allow/deny decision emits a receipt.

Allowed `taskbound.run` executions insert allow receipts. Database runtime
denials call `taskbound.fail_receipt`. API-layer AST preflight denials bind
the task first, then call `taskbound.fail_receipt` before returning denial.

I8. View registry, policy version, or definition-hash drift invalidates the
token or requires reapproval.

The prototype task token may carry a safe-view registry snapshot. During
binding, `taskbound.bind_task` recomputes the registry snapshot and rejects
stale safe-view registry version, policy version, or view-definition hash.

## Production Boundary

The hardening prototype now includes AST-level API preflight plus an
experimental PostgreSQL `post_parse_analyze_hook` extension path before dynamic
execution. The database also enforces no raw schema grants, safe views, budgets,
and receipts. A production implementation should harden this path into
always-on planner/executor integration, optimized accounting, and
out-of-transaction denial logging.
