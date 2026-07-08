# SessionBound

SessionBound turns approved enterprise tasks into budgeted database sessions for
AI agents.

Business users approve tasks, not database policies. Agents generate SQL, but
databases enforce the approved boundary.

SessionBoundDB is the PostgreSQL runtime prototype. It binds signed task tokens
to safe views, query/disclosure budgets, and receipts.

> Note: the current PostgreSQL prototype keeps the `taskbound` SQL schema name
> for compatibility with the existing demo implementation.

Paper: https://arxiv.org/abs/2607.00751

Code and artifacts: the prototype source code, synthetic evaluation dataset,
validation reports, benchmark outputs, and LaTeX source for the paper are
available at https://github.com/SessionBound/sessionbound.

TDSC artifact status: the current TDSC-oriented submission candidate is anchored
by the `tdsc-submission-2026-07-07` tag and documented in
[paper/tdsc/ARTIFACT_MANIFEST.md](paper/tdsc/ARTIFACT_MANIFEST.md). The arXiv
v1 workspace is an earlier public preprint snapshot; use the TDSC tag for the
current artifact-backed claim contract.

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
  -> Agent SDK query(sql)
  -> SessionBoundDB Runtime
  -> Safe Views, Budgets, Receipts
  -> Enterprise Data
```

## What It Demonstrates

The prototype demonstrates:

- task templates, task applications, approvals, grants, budgets, and TTLs;
- signed task tokens that bind business intent to database execution;
- short-lived credentials for agent runtimes;
- a native agent SDK where `query(sql)` runs safe-view SQL through PostgreSQL
  hook and executor accounting;
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

In the prototype, the SDK submits that SQL through `TaskboundSession.query(sql)`,
which executes native safe-view `SELECT` on a task-bound PostgreSQL session.
PostgreSQL hooks validate the approved safe-view OIDs, executor accounting
counts returned rows and disclosed `expense_id` values before forwarding tuples,
and evaluated bound-runtime allowed receipts and hook/API denial receipts are
written through a rollback-surviving audit channel.

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

The current canonical evaluation passes all validation scenarios used by the
TDSC artifact:

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
python scripts/sessionbound_agent_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/evaluation/eval_runs
```

The validation covers allowed analytical SQL, denied sensitive-field access, denied raw-schema access, denied write/DDL attempts, payload-aggregation blocking, transparent scope filtering, query-budget enforcement, and disclosure-budget enforcement.

Detailed TDSC validation and hardening notes are in:

- [paper/tdsc/ARTIFACT_MANIFEST.md](paper/tdsc/ARTIFACT_MANIFEST.md)
- [paper/tdsc/evaluation/FUNCTIONAL_VALIDATION.md](paper/tdsc/evaluation/FUNCTIONAL_VALIDATION.md)
- [paper/tdsc/ADVERSARIAL_SQL_SUITE.md](paper/tdsc/ADVERSARIAL_SQL_SUITE.md)

## Benchmark

Benchmark and overhead data for the TDSC artifact are recorded in:

- [paper/tdsc/OVERHEAD_BREAKDOWN.md](paper/tdsc/OVERHEAD_BREAKDOWN.md)
- [paper/tdsc/experiments/SCALE_CONCURRENCY_RESULTS.md](paper/tdsc/experiments/SCALE_CONCURRENCY_RESULTS.md)

The benchmark compares equivalent SQL over raw `app_data` tables with the SessionBound path through signed task-token binding, SDK-style query execution, and the guarded safe-view runtime. Benchmark numbers are not summarized here so that the benchmark report remains the single source for measured results.

## Paper

TDSC-oriented current candidate:

- [paper/tdsc/sessionbound-tdsc.pdf](paper/tdsc/sessionbound-tdsc.pdf)
- [paper/tdsc/sessionbound-tdsc.tex](paper/tdsc/sessionbound-tdsc.tex)
- [paper/tdsc/ARTIFACT_MANIFEST.md](paper/tdsc/ARTIFACT_MANIFEST.md)

Earlier arXiv v1 files:

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
paper/tdsc/                Current TDSC-oriented manuscript and artifact manifest
paper/arxiv-v1/            Earlier arXiv v1 manuscript, validation, and packaging files
```

## Prototype Limitations

- SQL validation now includes AST-level preflight and an experimental
  PostgreSQL hook path, but production-grade enforcement should move closer to
  parser/analyzer, planner, or executor integration.
- Complex single-database `SELECT` queries are supported for the demo, including joins, CTEs, subqueries, and window functions.
- Unique-row accounting tracks detail rows that include `expense_id`.
- Denied queries are surfaced as database errors. In the evaluated bound runtime path, native hook/API denial receipts and allowed query receipts are written through an autonomous audit channel so they survive rollback of the agent transaction.
- HMAC keys are stored in the demo database for convenience.
- The Credential Broker is implemented inside the demo FastAPI service and uses the admin database URL.
- Cross-database federation is intentionally out of scope for this prototype.

## License

Apache-2.0. See [LICENSE](LICENSE).
