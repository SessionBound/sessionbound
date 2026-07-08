# SDK Query Surface

## Status

- Implementation: `app/taskbound_sdk.py`
- Agent-facing method: `TaskboundSession.query(sql_text)`
- Database entrypoint used by the SDK: native safe-view SQL under
  `sessionbound_guard` hook/executor accounting
- Evaluation script: `paper/tdsc/scripts/sdk_query_eval.py`
- Latest smoke result: docker compose SDK smoke on 2026-07-08
- Result: native query, bound bare SELECT accounting, and unbound fail-closed
  behavior passed

## Contract

Agents write ordinary analytical SQL over approved safe-view names:

```sql
SELECT expense_id, amount
FROM expenses
ORDER BY amount DESC
LIMIT 2;
```

The SDK executes that SQL directly on the bound PostgreSQL session. The runtime
credential has `SELECT` on registered safe views, but every native statement is
guarded by trusted task GUCs, approved safe-view OIDs, executor accounting, and
receipt emission. Raw application tables remain ungranted for `SELECT`; schema
resolution is permitted so the hook can fail closed and emit a denial receipt.

This gives agents a native `query(sql)` interface while preserving the budget
and receipt chain inside the database runtime. `taskbound.run(sql_text)` remains
available as a compatibility wrapper.

## Evaluation Summary

| Case | Expected | Result |
|---|---:|---:|
| `TaskboundSession.query(sql)` over safe-view SQL | allowed, accounted | passed |
| Bare `SELECT` against `taskbound.expenses` in the same bound session | allowed, accounted | passed |
| Bare `SELECT` after `unbind_task()` | blocked fail-closed | passed |

The smoke run returned two rows through `TaskboundSession.query(sql)`, then one
row through a bound direct safe-view `SELECT`. The task state showed
`query_count = 2`, `returned_rows = 3`, and receipts for both native reads.
After `unbind_task()`, the same direct safe-view query failed with
`no trusted task binding is active`.
