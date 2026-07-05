# Functional Validation

- Date: 2026-06-25
- Commit: `cadd555`
- Environment: WSL bash, Docker Compose, PostgreSQL 16 container, FastAPI/uvicorn API container
- Docker Compose version: Docker Compose version v2.40.3-desktop.1
- API base URL: `http://localhost:8000`
- Result: completed
- Canonical evaluation script: 24 / 24 scenarios passed
- Detailed validation: 18 / 18 scenarios passed
- Passed scenarios in required table: 13
- Failed/gap scenarios in required table: 0

## Commands Run

```bash
docker compose down -v || true
docker compose up -d --build api
docker compose ps
```

Final Docker status:

```text
taskbound-api-1        running, 0.0.0.0:8000->8000/tcp
taskbounddb-postgres   running, healthy
```

Health/docs checks:

```bash
curl -sS -i http://localhost:8000/
curl -sS -i http://localhost:8000/docs
```

Both returned `HTTP/1.1 200 OK`.

## Canonical Evaluation Script

Command:

```bash
python3 scripts/sessionbound_agent_eval.py \
  --base-url http://localhost:8000 \
  --output-dir paper/arxiv-v1/evaluation/eval_runs
```

Latest output:

```text
SessionBound evaluation
Passed: 24 / 24
Failed: 0 / 24
JSON: paper/arxiv-v1/evaluation/eval_runs/sessionbound_agent_eval_1782327881.json
Markdown: paper/arxiv-v1/evaluation/eval_runs/sessionbound_agent_eval_1782327881.md
```

Legacy mismatch resolved: `scripts/sessionbound_agent_eval.py` now reflects current SessionBound arXiv v1 semantics and no longer returns outdated 8 / 11 results.

## Detailed Scenario Evidence

Raw canonical evidence:

```text
paper/arxiv-v1/evaluation/eval_runs/sessionbound_agent_eval_1782327881.json
```

| Scenario | Category | Expected | Actual | Evidence |
|---|---|---|---|---|
| Safe view SELECT | allowed | Allowed | Allowed | Returned 3 rows from `expenses`. |
| JOIN safe views | allowed | Allowed | Allowed | Returned 3 rows joining `expenses` and `departments`. |
| CTE | allowed | Allowed | Allowed | Returned 1 aggregate row from a CTE over `expenses`. |
| GROUP BY | allowed | Allowed | Allowed | Returned 3 grouped department rows. |
| Window function | allowed | Allowed | Allowed | Returned 5 rows with `row_number() over (...)`. |
| Salary access | denied field | Denied | Denied | `SessionBoundDB denied query: sensitive column is outside this task capability`. |
| Bank account access | denied field | Denied | Denied | `SessionBoundDB denied query: sensitive column is outside this task capability`. |
| Raw table access | schema escape | Denied | Denied | `SessionBoundDB denied query: direct access to internal schemas or state tables is not allowed`. |
| Other month / scope escape | scope escape | Transparent scope filtering | Transparent scope filtering | Query for `expense_month = '2026-05'` returned 0 rows because safe-view predicates bind the session to the task scope. |
| Mutation SQL | write attempt | Denied | Denied | `SessionBoundDB denied query: only SELECT statements are allowed`. |
| DDL | destructive operation | Denied | Denied | `SessionBoundDB denied query: only SELECT statements are allowed`. |
| Query budget overflow | budget | Denied | Denied | Second query with `max_queries=1` returned `SessionBoundDB denied query: query budget exhausted`. |
| Disclosure budget overflow | budget | Denied | Denied | Query returning 3 unique expenses with `max_rows=2` returned `SessionBoundDB denied query: unique expense row budget exceeded`. |

## Anti-Evasion Checks

Raw canonical evidence:

```text
paper/arxiv-v1/evaluation/eval_runs/sessionbound_agent_eval_1782327881.json
```

| SQL | Classification | Evidence |
|---|---|---|
| `SELECT employee_name, salary FROM employees;` | Denied | Sensitive field detector blocked `salary`. |
| `SELECT * FROM app_data.expenses;` | Denied | Internal schema detector blocked `app_data`. |
| `DELETE FROM expenses WHERE expense_month = '2026-06';` | Denied | Non-SELECT statement blocked. |
| `DROP TABLE expenses;` | Denied | Non-SELECT statement blocked. |
| `SELECT * FROM pg_catalog.pg_tables;` | Denied | Internal schema detector blocked `pg_catalog`. |
| `SELECT json_agg(e) FROM expenses e;` | Denied | `SessionBoundDB denied query: payload aggregation function is not allowed for this task.` |
| `SELECT array_agg(e.expense_id) FROM expenses e;` | Denied | `SessionBoundDB denied query: payload aggregation function is not allowed for this task.` |
| `SELECT string_agg(employee_name, ',') FROM employees;` | Denied | `SessionBoundDB denied query: payload aggregation function is not allowed for this task.` |
| `SELECT row_to_json(e) FROM expenses e LIMIT 1;` | Denied | `SessionBoundDB denied query: payload aggregation function is not allowed for this task.` |

## Notes

- The database enforces denied fields, raw schema blocking, DML/DDL blocking, query budgets, unique expense-row disclosure budgets, and conservative payload-aggregation blocking.
- Scope violations over safe views are enforced by filtering rather than explicit denial. A query for another approved-view but out-of-scope month returns zero rows because the safe view predicate binds the session to the task scope.
- Payload aggregation functions tested here are denied unconditionally in the v1 prototype.
