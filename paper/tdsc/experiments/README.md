# SessionBound TDSC Experiments

This directory contains the experiment workspace used to revise the
TDSC manuscript. Raw outputs are preserved under `raw_results/`; summary
reports in this directory cite those files.

## Environment

- Branch: `high-standard-tdsc-pdsc-revision`
- Base commit at run time: current branch working tree
- Date: 2026-07-08
- Host: Linux WSL2, x86_64
- Docker: 29.1.3
- Docker Compose: v2.40.3
- PostgreSQL image: `postgres:16`
- PostgreSQL version: 16.14
- Python: 3.13.5
- API endpoint: `http://localhost:8000`
- Seed data: 430 expenses, 240 employees, 124 departments

## Raw Results

- Functional validation: `../raw_results/sessionbound_agent_eval_1783658577.json`
- AST validation: `../raw_results/ast_validation_20260708_205921.json`
- Adversarial SQL: `../raw_results/adversarial_sql_20260708_235742.json`
- Hook/direct DB enforcement: `../raw_results/sessionbound_guard_hook_20260709_015110.json`
- Security baselines: `../raw_results/security_baseline_1783515117.json`
- Performance baselines: `../raw_results/overhead_breakdown_20260710_103837.json`
- Credential-token and schema drift tests: `../raw_results/credential_token_1783515640.json`
- Historical wrapper scale sweep: `../raw_results/scale_1783515366.json`
- Native end-to-end scale benchmark:
  `../raw_results/native_end_to_end_20260709_013634.json`
- Native projection-budget accounting:
  `../raw_results/native_partial_budget_20260710_124301.json`
- Hook-only microbenchmark: `../raw_results/hook_microbenchmark_20260708_205831.json`
- Rollback audit: `../raw_results/rollback_audit_20260710_124300.json`
- Functionally equivalent external-PEP baseline:
  `../raw_results/functional_equivalent_baseline_20260710_124301.json`
- Concurrency smoke test: `raw_results/concurrency_1783221968.json` (historical)

Older intermediate security, performance, and credential-token JSON files
are kept only as run history. The reports use the corrected result files
listed above.

## Reproduction Commands

```sh
docker compose up -d --build api
python scripts/sessionbound_agent_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
python paper/tdsc/scripts/ast_validation_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
python paper/tdsc/scripts/adversarial_sql_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
python paper/tdsc/scripts/sessionbound_guard_hook_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
python paper/tdsc/scripts/rollback_audit_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
docker compose exec -T postgres psql -U postgres -d travel < paper/tdsc/experiments/sql/role_only_baseline.sql
docker compose exec -T postgres psql -U postgres -d travel < paper/tdsc/experiments/sql/safe_view_only_baseline.sql
docker compose exec -T postgres psql -U postgres -d travel < paper/tdsc/experiments/sql/rls_safe_view_short_credential_audit_baseline.sql
TDSC_OUT_DIR=paper/tdsc/raw_results TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_BASE_URL=http://localhost:8000 python paper/tdsc/experiments/scripts/tdsc_security_eval.py
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 python paper/tdsc/scripts/overhead_breakdown.py --output-dir paper/tdsc/raw_results
TDSC_OUT_DIR=paper/tdsc/raw_results TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_BASE_URL=http://localhost:8000 python paper/tdsc/experiments/scripts/tdsc_credential_token_tests.py
TDSC_OUT_DIR=paper/tdsc/raw_results TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_BASE_URL=http://localhost:8000 python paper/tdsc/experiments/scripts/tdsc_scale_eval.py
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_BASE_URL=http://localhost:8000 TDSC_NATIVE_ROWS=1000,10000,100000 TDSC_NATIVE_WARMUP=10 TDSC_NATIVE_MEASURED_BY_ROWS=1000:100,10000:100,100000:30 python paper/tdsc/scripts/native_end_to_end_benchmark.py --output-dir paper/tdsc/raw_results
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 python paper/tdsc/scripts/native_partial_budget_eval.py --output-dir paper/tdsc/raw_results
python paper/tdsc/scripts/functional_equivalent_baseline_eval.py --output-dir paper/tdsc/raw_results
docker compose exec -T api sh -lc 'TDSC_OUT_DIR=/tmp/tdsc_experiments/raw_results TDSC_BASE_URL=http://127.0.0.1:8000 python /tmp/tdsc_experiments/scripts/tdsc_concurrency_eval.py'
python paper/tdsc/scripts/hook_microbenchmark.py --output-dir paper/tdsc/raw_results
```

The SQL baseline setup files are experiment fixtures, not production
migrations.
