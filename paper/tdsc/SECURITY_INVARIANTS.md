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
preflight, the experimental `sessionbound_guard` PostgreSQL hook/executor path,
native safe-view `SELECT`, the compatibility `taskbound.run(...)` wrapper, safe
views, budget state, and query receipts.

## Invariants

I1. No execution without active task binding.

Native safe-view `SELECT` checks trusted task GUCs installed by
`taskbound.bind_task(...)`; the compatibility `taskbound.run(sql)` path calls
`taskbound.require_payload()`. Both paths fail if no task is bound to the
current backend.

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

## Security Guarantees for the Prototype SQL Fragment

The TDSC draft states the invariant contract over a restricted SQL fragment,
`SELECT-F`. This fragment contains single-statement, read-only `SELECT` SQL with
projection, predicates, joins, `GROUP BY`/`HAVING`, ordering and limits,
non-recursive CTEs, and window functions over registered safe-view names.

`SELECT-F` excludes stacked statements, DDL, DML, `COPY`, `DO`, `CALL`,
`CREATE FUNCTION`, temporary object creation, recursive CTEs, set-operation
stacking, table functions, raw-schema or catalog references, and high-risk
payload aggregation such as `json_agg`, `jsonb_agg`, `array_agg`,
`string_agg`, `xmlagg`, `row_to_json`, `json_build_object`, and
`jsonb_build_object`.

Under a well-formed state `S=<T,C,V,B,R>` with a valid token/session binding,
matching safe-view registry and policy hashes, nonnegative budget vector, and
verifiable receipt chain, the prototype contract is:

**Proposition 1: Safe-surface confinement.** If `q in SELECT-F` and
`decide(q,S)=allow`, every resolved relation named by `q` is an approved safe
view in `V`. Raw base tables may be reached only through trusted safe-view
definitions, not by direct agent-supplied SQL.

**Proposition 2: Denied-field non-disclosure for direct references.** If a
column is absent from the approved safe-view column lists or appears in the
token's denied-field set, no allowed query can directly project that column.
This is a direct syntactic guarantee and does not rule out semantic inference
from permitted columns or aggregates.

**Proposition 3: Monotonic budget accounting.** For every allowed query, the
remaining budget vector is componentwise non-increasing. If the required
query-count or disclosure charge exceeds the remaining budget, the query is
denied and no result is released.

**Proposition 4: Receipt-chain tamper evidence under trusted DB assumptions.**
Given collision-resistant hashing and append-only receipt storage inside the
trusted database boundary, removing or modifying an interior receipt changes
that receipt's hash or breaks the downstream previous-hash pointer. This is not
a Byzantine storage guarantee against malicious DBAs or compromised hosts.

## Production Boundary

The hardening prototype now includes AST-level API preflight plus an
experimental PostgreSQL `post_parse_analyze_hook` extension path before dynamic
execution. The generated runtime credential can connect directly to PostgreSQL,
but it has no bare `SELECT` grant on raw tables; approved SQL over safe views is
exposed through `TaskboundSession.query(sql)` and native safe-view `SELECT`,
where trusted GUCs, approved safe-view OIDs, executor accounting, and receipts
are applied. Evaluated allowed receipts and hook/API denial receipts are emitted
through a rollback-surviving same-database audit channel. Production deployments
still need always-on planner/executor integration, optimized result accounting
before client release, external audit retention/WORM storage, credential
lifecycle cleanup, and hardened key management.
