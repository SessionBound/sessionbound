# SessionBound TDSC v2 Experiments

This directory contains the v2 experiment workspace used to revise the
TDSC manuscript. Raw outputs are preserved under `raw_results/`; summary
reports in this directory cite those files.

## Environment

- Branch: `tdsc-baseline-experiments`
- Base commit at run time: `5dce85f`
- Date: 2026-07-05
- Host: Linux WSL2, x86_64
- Docker: 29.1.3
- Docker Compose: v2.40.3
- PostgreSQL image: `postgres:16`
- PostgreSQL version: 16.14
- Python: 3.13.5
- API endpoint: `http://localhost:8000`
- Seed data: 430 expenses, 240 employees, 124 departments

## Raw Results

- Functional validation: `raw_results/eval_runs/sessionbound_agent_eval_1783224117.json`
- Security baselines: `raw_results/security_baseline_1783221673.json`
- Performance baselines: `raw_results/performance_1783221625.json`
- Credential-token and schema drift tests: `raw_results/credential_token_1783224156.json`
- Receipt/budget ablation: `raw_results/ablation_1783224624.json`
- Scale sweep: `raw_results/scale_1783224892.json`
- Concurrency smoke test: `raw_results/concurrency_1783221968.json`

Older intermediate security, performance, and credential-token JSON files
are kept only as run history. The reports use the corrected result files
listed above.

## Reproduction Commands

```sh
docker compose up -d --build api
python scripts/sessionbound_agent_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc-v2/experiments/raw_results/eval_runs
docker compose exec -T postgres psql -U postgres -d travel -f /tmp/tdsc_experiments/sql/role_only_baseline.sql
docker compose exec -T postgres psql -U postgres -d travel -f /tmp/tdsc_experiments/sql/safe_view_only_baseline.sql
docker compose exec -T postgres psql -U postgres -d travel -f /tmp/tdsc_experiments/sql/rls_baseline.sql
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_security_eval.py'
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_performance_eval.py'
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_credential_token_tests.py'
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_ablation_eval.py'
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_scale_eval.py'
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_concurrency_eval.py'
```

The SQL baseline setup files are experiment fixtures, not production
migrations.
