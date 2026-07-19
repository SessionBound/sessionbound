# Native Reference-Monitor Text-Bypass Fix - 2026-07-17

Addresses P0 finding #3 in `tdsc_blocking_audit_20260710.md`: "Native reference
monitor has bypassable enforcement gates."

## Scope of the change

Only `postgres/sessionbound_guard/sessionbound_guard.c`, in the
`should_account_query` accounting-eligibility gate.

## What was removed (the real bypass)

`source_is_runtime_helper_call(query_desc->sourceText)` and its call site.
That helper performed a case-insensitive raw-substring match for runtime-helper
strings (`taskbound.native_`, `taskbound.run`, `public.sessionbound_guard_check`,
...). `should_account_query` returned false (skip accounting) whenever any
matched.

This was exploitable: `query_desc->sourceText` is the caller-controlled raw SQL
including comments, string literals, and quoted aliases. A task SELECT could
exempt itself from row/query counting by embedding any helper substring, e.g.:

```sql
SELECT expense_id, amount FROM expenses LIMIT 1 /* taskbound.native_x */
```

It was also redundant:

- Direct calls to private `taskbound.*` helpers are already denied at
  plan-analysis time by `function_is_taskbound_private()` via
  `guard_check_function()`, before any executor accounting runs.
- Guard-internal bookkeeping SELECTs (`taskbound.native_reserve_query`, receipt
  writes) all run through `spi_call_void()`, which holds `guard_in_internal_spi`
  true for the duration; that flag is checked first in `should_account_query` and
  excludes them. They are parameterized SPI, so an attacker cannot inject SQL
  into them.
- Public entrypoint wrappers (`SELECT taskbound.run / bind_task`) execute before
  `guard_task_bound` is installed, so they are excluded by that flag.

## What was retained (load-bearing, not a bypass)

The `GetUserId() != GetSessionUserId()` test. This is the trusted discriminator
between the two accounting paths, and removing it breaks the wrapper:

- `taskbound.run` and `bind_task` are `SECURITY DEFINER` (owned by `postgres`),
  so while they run, `GetUserId()` is the definer and differs from the session
  user. Their internal SELECTs -- and the user SQL they `EXECUTE` -- are
  accounted by the PL/pgSQL wrapper itself (`taskbound.run` counts returned rows
  and records them via `native_finish_query`). If the C hook also accounted
  them, PL/pgSQL's own SPI tuple accounting is corrupted
  ("consistency check on SPI tuple count failed").
- A bare SELECT issued directly by the session after binding runs as the session
  user, so `GetUserId() == GetSessionUserId()`; that is exactly the native path
  the C hook must account.

The check cannot be abused in the native path: the utility precheck
(`guard_utility_precheck`, `T_VariableSetStmt`) refuses `SET ROLE` once a binding
is active, so a bound native session cannot move to a different effective user to
trigger the discriminator. Pre-binding role changes are irrelevant because
nothing is accounted before `guard_task_bound` is set.

Note: the parallel `!guard_enabled && GetUserId() != GetSessionUserId()` early
return in the *structural* post-parse-analyze hook is a separate concern, not
touched here.

## Evidence

- Guard hook eval: 18/18 unchanged before and after the change (covers native
  bare-SELECT, native prepared/cursor/COPY/EXPLAIN, wrapper runtime, and
  structural denials). Result artifact:
  `paper/tdsc/raw_results/sessionbound_guard_hook_*.json`.
- Regression probe: `paper/tdsc/scripts/native_text_bypass_probe.py`. It binds a
  `max_queries = 1` task and, in one session, issues a safe-view SELECT embedding
  helper substrings (Q1, the attacker's attempted free query) then a clean
  safe-view SELECT (Q2). Native accounting is made observable via the budget:
  - Original code: Q1 is exempt (text match) -> counter stays 0 -> Q2 accounted
    and allowed. `bypass_closed = false`. Confirmed Q1 returned a real row
    unaccounted.
  - Fixed code: Q1 is accounted -> counter 1 -> Q2 denied as
    "query budget exhausted". `bypass_closed = true`.

## Separate finding (not introduced here)

The full 140-case adversarial SQL eval could not complete because of a
pre-existing backend segfault (signal 11) inside `SELECT taskbound.fail_receipt`,
which takes down the postmaster. `fail_receipt` is only ever called from
`taskbound.run` (SECURITY DEFINER, owner `postgres`), so it always runs with
`GetUserId() != GetSessionUserId()` and is skipped by the retained discriminator
identically in the old and new code; the change here is therefore provably
uninvolved (the 18/18 guard-hook run, with the fix applied, exercised
`fail_receipt` for its own denied cases without fault). The segfault is a
separate item to triage.
