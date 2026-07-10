# Global Single-Active Session Binding

## Security Goal

For one exact binding key:

```text
BindingKey = task_id + token_digest + credential_id
```

at most one PostgreSQL backend may be ACTIVE in one writable PostgreSQL
primary at any time. The guarantee is per exact key, not per task only and not
database-wide.

## Binding Key And Lock Key

`bind_task` validates the signed task token first, including the signed nonce,
credential id, actor, audience, TTL, revocation state, login role, and safe-view
registry hashes. The runtime uses the stable token digest as the token identity.
It computes a canonical binding string from `task_id`, `token_digest`, and
`credential_id`, then derives a signed 64-bit advisory lock key from the fixed
SHA-256 prefix.

Hash collisions fail closed: two different binding keys that collide on the
64-bit lock key can conservatively reject one another, but they cannot both own
the same exclusive advisory lock.

## Authoritative Lock

The authoritative mutex is a PostgreSQL session-level advisory lock acquired
with `pg_try_advisory_lock(lock_key)`. The call is non-blocking. Failure returns
`ACTIVE_BINDING_EXISTS` with SQLSTATE `55P03` and the API maps it to HTTP 409.

The lock is held for the binding lifetime by a trusted same-database dblink
lock connection owned by the agent backend. Normal `unbind_task` releases it.
Client disconnect, backend crash, `pg_terminate_backend`, and PostgreSQL
restart release it through PostgreSQL session cleanup.

Agent SQL cannot call `pg_advisory_unlock`, `pg_advisory_unlock_all`, `DISCARD
ALL`, untrusted `SET`/`RESET`, or the internal guard-clear function to continue
after dropping trusted state.

## Active Binding Table

`taskbound.active_sessions` is a protected current-state table. Ordinary agent
roles do not receive direct table privileges. The row records:

- `task_id`, `token_digest`, `token_nonce`, `credential_id`;
- `binding_id`, `fence_token`, `advisory_lock_key`;
- `database_oid`, `lock_backend_pid`;
- `owner_backend_pid`, `owner_backend_start`, `owner_postmaster_start`,
  `owner_session_user`;
- payload, acquisition timestamps, diagnostic `last_seen_at`, and token expiry.

The key constraints are:

- unique exact binding key: `(task_id, token_digest, credential_id)`;
- unique owner id: `binding_id`.

Released history is written to receipts and `taskbound.binding_events`; the
active table contains only current or recoverable state.

## Bind State Machine

1. Validate token, credential, task state, login role, actor, audience, nonce,
   and safe-view registry.
2. Reject if the current backend already owns any active binding.
3. Derive `BindingKey`, advisory lock key, new `binding_id`, and monotonic
   `fence_token`.
4. Acquire the session advisory lock with `pg_try_advisory_lock`.
5. Claim the exact active row through trusted autonomous SQL.
6. If an old row exists, recover it only after the new owner has the advisory
   lock and the recorded old owner is not live by PID, backend start,
   postmaster start, and database OID.
7. Install backend-local trusted C state containing task id, token digest,
   credential id, binding id, fence token, and advisory lock key.

Any intermediate failure clears only this attempted binding, releases the lock,
and fails closed.

## Query-Time Validation And Fencing

Statement entry validates that backend-local trusted state still matches the
protected active row and that the advisory lock remains held by the recorded
lock backend. Token expiry, task revocation, credential revocation/expiry, and
session-user matching are checked at runtime.

Budget writes, receipt writes, active-row release, and cleanup carry
`binding_id + fence_token`. Mutations use a predicate equivalent to:

```sql
WHERE binding_id = :binding_id
  AND fence_token = :fence_token
```

Fence mismatches fail closed and write `BINDING_FENCED` events. This prevents a
dead or delayed old owner from modifying a new owner after recovery.

## Unbind State Machine

`unbind_task` verifies the caller backend is the active owner, releases the
active row by `binding_id + fence_token` while the advisory lock is still held,
writes a binding event, releases the session advisory lock, and clears trusted
backend-local state. Old fences cannot delete a replacement row.

## Crash Recovery

Recovery is not based on time. `last_seen_at` is diagnostic only.

On the next bind for the same key, the new backend must first acquire the same
advisory lock. Only then may it inspect the old row. If the old owner is still
visible in `pg_stat_activity` with the same PID, backend start, postmaster
start, and database OID, recovery fails closed. If the owner is gone, the row is
updated with a new `binding_id` and higher `fence_token`, preserving task budget
and receipt history.

`taskbound.reap_stale_bindings()` applies the same owner-liveness rule and does
not evict a live owner solely because `last_seen_at` is old.

## Transaction Semantics

`bind_task` and `unbind_task` are session-control operations. Their active-row
and lock lifecycle is independent of ordinary agent transaction rollback.

```sql
BEGIN;
SELECT taskbound.bind_task(...);
ROLLBACK;
```

leaves the backend bound until explicit unbind or connection close. Transaction
control statements are allowed as session mechanics; the native utility hook
does not run SPI validation while processing rollback from an aborted
transaction.

## HA And Scope

The guarantee assumes one trusted writable PostgreSQL primary, the PostgreSQL
lock manager, the database runtime, and the database host. It is not a
distributed consensus protocol. PostgreSQL HA split-brain, cross-cluster global
locks, malicious DBAs, compromised hosts, and storage-level tampering require
external primary fencing and audit anchoring.

## Error Codes

- `ACTIVE_BINDING_EXISTS`: another backend holds the same binding key,
  SQLSTATE `55P03`, HTTP 409.
- `BINDING_FENCED`: local binding id/fence no longer matches active state.
- `BINDING_LOST`: active row exists but the advisory lock is no longer held.
- `BINDING_STATE_INCONSISTENT`: lock/table state contradicts live-owner checks.
- `STALE_BINDING_RECOVERED`: new owner recovered an abandoned row after lock
  acquisition and owner-death verification.

## Test Mapping

`paper/tdsc/scripts/single_active_binding_eval.py` covers:

- two-backend same-key race;
- 20-contender same-key race;
- 1000 repeated synchronized races;
- explicit unbind reacquisition;
- socket-close stale recovery;
- `pg_terminate_backend` recovery with budget continuity and receipt chain;
- live idle owner non-eviction;
- PID reuse fixture with backend-start mismatch;
- rollback-independent bind semantics;
- old-fence mutation rejection;
- lock/utility tamper resistance;
- different binding keys succeeding concurrently.
