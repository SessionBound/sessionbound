# SDK Query Surface

## Status

- Implementation: `app/taskbound_sdk.py`
- Agent-facing method: `TaskboundSession.query(sql_text)`
- Database entrypoint used by the SDK: `SELECT * FROM taskbound.run(%s)`
- Evaluation script: `paper/tdsc/scripts/sdk_query_eval.py`
- Latest raw result: `paper/tdsc/raw_results/sdk_query_20260707_102212.json`
- Result: 2 / 2 cases passed

## Contract

Agents write ordinary analytical SQL over approved safe-view names:

```sql
SELECT expense_id, amount
FROM expenses
ORDER BY amount DESC
LIMIT 2;
```

The SDK submits that SQL as a parameter to `taskbound.run(sql_text)`. The
runtime credential still has no bare `SELECT` grant on raw tables or safe views,
so bypassing the SDK with `SELECT ... FROM taskbound.expenses` is denied by
PostgreSQL privileges.

This gives agents a native-feeling `query(sql)` interface while preserving the
budget and receipt chain inside the database runtime.

## Evaluation Summary

| Case | Expected | Result |
|---|---:|---:|
| `TaskboundSession.query(sql)` over safe-view SQL | allowed, accounted | passed |
| Bare `SELECT` against `taskbound.expenses` in the same bound session | blocked | passed |

The allowed SDK query returned two rows, incremented `query_count` to 1,
recorded two unique expense rows, and emitted an allow receipt with
`rows_returned = 2`.
