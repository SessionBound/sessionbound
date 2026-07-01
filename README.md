# SessionBound

SessionBound turns approved enterprise tasks into budgeted database sessions for
AI agents.

Business users approve tasks, not database policies. Agents generate SQL, but
databases enforce the approved boundary.

SessionBoundDB is the PostgreSQL runtime prototype. It binds signed task tokens
to safe views, query/disclosure budgets, and receipts.

> Note: the current PostgreSQL prototype keeps the `taskbound` SQL schema name
> for compatibility with the existing demo implementation.

Code availability: the prototype source code and synthetic evaluation dataset
are available at https://github.com/SessionBound/sessionbound.

Hosted demo: https://www.sessionbound.org/

## Why SessionBound

Enterprise analysis often sits between two bad choices:

- Fixed SaaS screens are too rigid for temporary, exploratory, task-specific analysis.
- Raw database access is too dangerous for AI agents that generate open-ended SQL.
- Application-layer approval does not automatically become a database execution boundary.

SessionBound addresses this gap by turning an approved business task into a
short-lived database session with scoped safe views, denied fields, budgets, and
receipts.

## Architecture

```text
Task Template
  -> Task Application
  -> Task Approval, Grants, Budgets
  -> Signed Task Token
  -> Agent-generated SQL
  -> SessionBoundDB Runtime
  -> Safe Views, Budgets, Receipts
  -> Enterprise Data
```

## What It Demonstrates

The prototype demonstrates:

- task templates, task applications, approvals, grants, budgets, and TTLs;
- signed task tokens that bind business intent to database execution;
- short-lived credentials for agent runtimes;
- safe views that expose business objects without exposing raw tables;
- denied fields such as salary, phone, and bank account;
- query and disclosure budgets;
- receipts for auditable query execution;
- controlled commands for high-value workflow writes;
- SessionBoundDB as a PostgreSQL runtime boundary.

An agent can run open-ended SQL inside the approved task boundary:

```sql
WITH ranked AS (
  SELECT expense_id, department_name, category, amount,
         row_number() OVER (PARTITION BY department_name ORDER BY amount DESC) AS rn
  FROM expenses
  WHERE expense_month = '2026-06'
)
SELECT department_name, expense_id, category, amount
FROM ranked
WHERE rn = 1;
```

But the database rejects access outside the task:

```sql
SELECT employee_name, salary FROM employees;
-- SessionBoundDB denied query: sensitive column is outside this task capability
```

## Quickstart

Requirements:

- Docker
- Python 3.11+ if you want to run the evaluation script from the host

Start the FastAPI demo:

```bash
git clone https://github.com/SessionBound/sessionbound.git
cd sessionbound
docker compose up -d --build api
```

Open:

```text
http://localhost:8000
```

Public demo:

```text
https://www.sessionbound.org/
```

Useful pages:

- User-facing demo: `http://localhost:8000/`
- Policy console for task templates, grants, and safe-view registry: `http://localhost:8000/admin`
- FastAPI docs: `http://localhost:8000/docs`

The demo UI follows the SessionBound paper model: task application -> approval -> budgeted database session -> agent analysis -> receipts.

Optional DeepSeek workspace test:

```bash
cp .env.example .env
# put your own DEEPSEEK_API_KEY in .env
docker compose up -d --build api
```

The local `.env` file is ignored by git.

## Evaluation

The current canonical evaluation passes all public arXiv validation scenarios:

```text
SessionBound evaluation
Passed: 24 / 24
Failed: 0 / 24
```

Reset the database and start the API:

```bash
docker compose down -v
docker compose up -d --build api
```

Run:

```bash
python scripts/sessionbound_agent_eval.py --base-url http://localhost:8000 --output-dir paper/arxiv-v1/evaluation/eval_runs
```

The validation covers allowed analytical SQL, denied sensitive-field access, denied raw-schema access, denied write/DDL attempts, payload-aggregation blocking, transparent scope filtering, query-budget enforcement, and disclosure-budget enforcement.

Detailed validation notes are in [paper/arxiv-v1/evaluation/FUNCTIONAL_VALIDATION.md](paper/arxiv-v1/evaluation/FUNCTIONAL_VALIDATION.md).

## Benchmark

Benchmark data is recorded in [paper/arxiv-v1/benchmarks/PERFORMANCE_BENCHMARK.md](paper/arxiv-v1/benchmarks/PERFORMANCE_BENCHMARK.md).

The benchmark compares equivalent SQL over raw `app_data` tables with the SessionBound path through signed task-token binding and `taskbound.run(sql)`. Benchmark numbers are not summarized here so that the benchmark report remains the single source for measured results.

## Paper

Current arXiv v1 files:

- [paper/arxiv-v1/manuscript/arxiv.pdf](paper/arxiv-v1/manuscript/arxiv.pdf)
- [paper/arxiv-v1/manuscript/arxiv.tex](paper/arxiv-v1/manuscript/arxiv.tex)
- [paper/arxiv-v1/manuscript/references.bib](paper/arxiv-v1/manuscript/references.bib)

## Core Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/TASK_CONTROL_PLANE.md](docs/TASK_CONTROL_PLANE.md)
- [docs/TASKBOUNDDB_RUNTIME.md](docs/TASKBOUNDDB_RUNTIME.md)
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)
- [docs/COMPARISON.md](docs/COMPARISON.md)

## Project Map

```text
app/                       FastAPI app, demo UI, task registry, runtime client
db/001_schema.sql          PostgreSQL schemas, raw tables, runtime state tables
db/002_seed.sql            travel reimbursement demo data
db/003_runtime_core.sql    task token binding and session helpers
db/004_safe_views.sql      SessionBound safe views and view registry rows
db/005_query_runtime.sql   SQL execution boundary, budgets, receipts
db/006_commands_and_grants.sql
                           controlled commands and runtime grants
docs/                      Architecture, runtime, threat model, and comparison docs
scripts/sessionbound_agent_eval.py
                           Agent-agnostic evaluation harness
paper/arxiv-v1/            Current arXiv v1 manuscript, validation, and packaging files
```

## Prototype Limitations

- SQL validation uses conservative keyword checks, not a full SQL parser.
- Complex single-database `SELECT` queries are supported for the demo, including joins, CTEs, subqueries, and window functions.
- A production version should validate SQL with an AST parser, planner hooks, or a PostgreSQL extension.
- Unique-row accounting tracks detail rows that include `expense_id`.
- Denied queries are surfaced as database errors. In this PL/pgSQL-only demo, denied receipts are not persisted because PostgreSQL rolls back writes in the failing statement.
- HMAC keys are stored in the demo database for convenience.
- The Credential Broker is implemented inside the demo FastAPI service and uses the admin database URL.
- Cross-database federation is intentionally out of scope for this prototype.

## License

Apache-2.0. See [LICENSE](LICENSE).
