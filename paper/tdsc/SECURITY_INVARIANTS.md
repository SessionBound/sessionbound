# SessionBoundDB Security Invariants

These invariants define the intended enforcement contract and were used to
structure the adversarial evaluation. They are not a formal proof of absence of
semantic inference.

After the 2026-07-19 hardening pass, the implementation closes the specific
static bypass classes reviewed for tenant-only safe views, token-denied exposed
columns, public denial-receipt forgery, side-effect catalog functions,
cardinality-alias group release, and non-atomic allowed-release
budget/receipt updates. The evidence is targeted, not a full proof: the
2026-07-19 rerun covers the 140-case adversarial suite, native hook suite,
rollback audit, credential-token drift, concurrent isolation, single-active
binding, native partial-budget, SDK smoke, path consistency, overhead, and
native end-to-end diagnostics, but does not prove absence of semantic inference
or all possible SQL/parser variants.

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

## Invariants and Current Status

I1. No arbitrary runtime-credential SQL execution without active task binding.

Native safe-view `SELECT` checks trusted task GUCs installed by
`taskbound.bind_task(...)`; the compatibility `taskbound.run(sql)` path calls
`taskbound.require_payload()`. For sessions that inherit `agent_runtime`, the
PostgreSQL hook denies unbound ordinary `SELECT` and utility commands; only
public SessionBound runtime entrypoints such as `bind_task` are allowed before a
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

Current status: supported by the native relation-OID guard for bound
safe-view SQL. The 2026-07-19 rerun confirmed native direct SQL over approved
views, helper/private denials, and the 140-case adversarial classifications.

I4. Direct access to denied fields, raw schemas, catalog escape, mutation,
DDL, and blocked payload aggregation is denied.

The prototype detects denied field names and aliases, `app_data`,
`pg_catalog`, `information_schema`, mutation/DDL/utility statements, recursive
CTEs, set-operation stacking, and high-risk payload aggregation functions such
as `json_agg`, `jsonb_agg`, `array_agg`, `string_agg`, `xmlagg`,
`row_to_json`, `json_build_object`, and `jsonb_build_object`. These checks run
through both the API-layer AST validator and the database-resident hook path for
the evaluated cases.

Current status: strengthened. Token-denied columns are checked at bind time
against approved view exposure, and the native hook stores the normalized
denied-column set for parse-tree column/alias checks. Catalog functions are now
default-deny except for a small audited allowlist of scalar/aggregate/cast
functions. JSON aggregate aliases remain a test-suite item because PostgreSQL
version support differs by function name. The 2026-07-19 dynamic denied-field
evaluator confirmed that a token-specific `expenses.amount` denial is rejected
at bind across API, direct-wrapper, and direct-native paths for projection,
alias, predicate, ordering, aggregate-input, and window-expression query
shapes. These pre-bind denials occur before a task decision receipt exists.
The 2026-07-19 function side-effect evaluator passed 12 of 12 cases across
36 API, direct-wrapper, and direct-native path decisions. It covers the allowed
scalar-function control plus blocked `pg_sleep`, session advisory lock,
`pg_notify`, `set_config`, `current_setting`, privilege/file introspection,
SRFs, payload serialization, and unlisted aggregate cases. The side-effect
oracles confirmed no one-second sleep delay, no held tested session advisory
lock, and no delivered notification.

I5. Scope predicates or session-bound claims constrain visible rows.

Safe views call `taskbound.claim(...)` and constrain rows by tenant,
expense month, and optional department. Out-of-scope safe-view predicates
therefore return zero rows rather than raw out-of-scope data. The
2026-07-19 scope-completeness evaluator passed 4 of 4 task scenarios and
14 of 14 non-vacuous safe-view checks, comparing returned safe-view rows with
raw-table tenant/month/department provenance for expense, dimension, workflow,
and ledger views.

I6. Budget state is monotonic: an allowed query consumes budget, or the query
is denied.

The prototype increments query count and charges every candidate output tuple
when budget accounting is enabled; the legacy `unique_expense_rows` column is
retained as the tuple counter. Projection aliases, joins, and non-aggregate
detail CTEs do not depend on an `expense_id` column; direct aggregate/window
release is denied pending approved templates. The hardening overhead script
also measures the supported budget-disabled ablation to isolate this cost.

Current status: strengthened. Query count and output-tuple debits for allowed
wrapper/native releases are performed in the same fenced release-barrier
transition that appends the allow receipt. The native full-result `SELECT` path
now replays buffered tuples to the client only after that transition succeeds.
The 2026-07-19 Docker smoke confirmed direct native detail-row release and
accounting for an in-scope task. Denials do not release result tuples. Broader
production accounting dimensions remain future work.

I7. Every allow/deny decision emits a receipt.

Allowed and denied runtime decisions append hash-chained receipts through the
autonomous same-database audit channel. API-layer AST preflight denials bind
the task first and use the same runtime append function; evaluation scripts do
not write receipts themselves.

Current status: strengthened for bound runtime decisions. Receipts now have a
unique `execution_id`; the one-second de-duplication window has been removed;
direct agent grants on `fail_receipt` and `audit_append_receipt` are revoked;
and receipt hashes cover execution id, binding/fence identity, budget
transition fields, actor, touched-view list, timestamp, and previous hash.
`touched_views` is populated from PostgreSQL parse/analyze safe-view OIDs for
safely parseable bound database SQL and then mapped back to approved registry
names for receipts. API preflight receipt writes and unsupported denied syntax
forms rejected before parse/analyze use an approved-name fallback. This is
hashed audit context; enforcement remains the AST/OID safe-view predicate. The
2026-07-19 adversarial, hook, rollback, SDK, native partial-budget, and
path-consistency reruns exercise denial and allow receipts across the main
tested paths. Path consistency passed 12 of 12 cases across 36 API,
direct-wrapper, and direct-native observations, with two direct-native
PostgreSQL pre-analysis errors recorded as scoped observations rather than
receipt-equivalent task decisions. Path-exhaustive denial-receipt coverage for
all parser/analyzer variants remains future test work. The 2026-07-19 receipt
fault evaluator passed 5 of 5 cases: it recomputed receipt hashes from raw
database rows, verified previous-hash chaining, required hardened fields,
unique execution ids, and tamper sensitivity, confirmed a wrong-fence trusted
append inserts no receipt, and denied direct agent attempts to call
`fail_receipt`, `audit_append_receipt`, `native_finish_query`, or insert into
the receipt table.

I8. View registry, policy version, database/view identity, dependency, option,
or definition-hash drift invalidates the token or requires reapproval.

The prototype task token may carry a safe-view registry snapshot. During
binding, `taskbound.bind_task` recomputes the registry snapshot and rejects
stale safe-view registry version, policy version, database/view identity,
view-dependency hash, view-option hash, or view-definition hash.

I9. Direct aggregate and window release is denied unless an approved aggregate
template supplies trusted provenance/cardinality logic.

Current status: conservative. Direct aggregate and window release is denied
across API, wrapper, and native paths until an approved aggregate-template
mechanism can provide trusted source-entity provenance/cardinality. This includes ungrouped
aggregates, ad-hoc `GROUP BY`, `HAVING`, filtered aggregate release, and
window-function release. The 2026-07-19 aggregate-template gating evaluator
passed 12 of 12 cases and 36 of 36 path decisions, including forged
cardinality aliases, a raw-provenance one-employee filtered aggregate,
ordinary grouping, CTE/subquery aggregate release, and window partition release.

## Security Guarantees for the Prototype SQL Fragment

The TDSC draft states the invariant contract over a restricted SQL fragment,
`SELECT-F`. This fragment contains single-statement, read-only `SELECT` SQL with
projection, predicates, joins, ordering and limits, and non-recursive CTEs over
registered safe-view names.

`SELECT-F` excludes direct aggregate release unless an approved aggregate
template is used, ad-hoc `GROUP BY`, `HAVING`, filtered aggregate release,
window functions, stacked statements, DDL, DML, `COPY`, `DO`, `CALL`, `CREATE
FUNCTION`, temporary object creation, recursive CTEs, set-operation stacking,
`TABLESAMPLE`, table functions, raw-schema or catalog references, and high-risk
payload aggregation such as `json_agg`, `jsonb_agg`, `array_agg`,
`string_agg`, `xmlagg`, `row_to_json`, `json_build_object`, and
`jsonb_build_object`.

Under a well-formed state `S=<A,T,C,G,V,L,B,R>` with approved task `A`,
signed task token `T`, credential/session binding `C`, global active-binding
ownership `G`, matching safe-view registry and policy hashes `V`, permitted SQL
surface `L`, nonnegative budget vector `B`, and verifiable receipt chain `R`,
the prototype contract is:

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
