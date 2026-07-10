# SessionBound TDSC Draft

This directory contains the current TDSC-oriented manuscript, build files,
measured experiment artifacts, and hardening reports.

## Main Files

- `sessionbound-tdsc.tex`: main manuscript source.
- `references.bib`: bibliography.
- `IEEEtran.cls`: local IEEEtran class file from CTAN, included because
  the current machine does not provide it globally.
- `IEEEtran.bst`: local IEEEtran BibTeX style from CTAN.
- `SUBMISSION_STATUS.md`: remaining checks before any formal submission.
- `experiments/`: baseline experiment scripts, raw results, and reports.
- `raw_results/`: latest hardening raw JSON/CSV outputs.
- `scripts/`: latest hardening evaluation scripts.
- `POSTGRES_HOOK_ENFORCEMENT.md`: PostgreSQL hook prototype notes and
  evaluation summary.
- `SDK_QUERY_SURFACE.md`: agent-facing `query(sql)` SDK notes and evaluation
  summary.
- `FINAL_HARDENING_REPORT.md`: current hardening status and blockers.

## Build

```sh
make
```

The output PDF is `sessionbound-tdsc.pdf`.

## Current Reproduction Commands

Start the stack:

```sh
docker compose up -d --build postgres api
```

Run the July 10 hardening suite:

```sh
python scripts/sessionbound_agent_eval.py --base-url http://localhost:8000 --output-dir paper/tdsc/raw_results
python paper/tdsc/scripts/ast_validation_eval.py
python paper/tdsc/scripts/adversarial_sql_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/sessionbound_guard_hook_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/rollback_audit_eval.py --base-url http://localhost:8000
TDSC_AGENT_DSN=postgresql://agent_app:agentpass@localhost:15432/travel python paper/tdsc/scripts/native_partial_budget_eval.py
python paper/tdsc/scripts/concurrent_isolation_eval.py --db-host localhost --db-port 15432 --db-name travel
python paper/tdsc/scripts/single_active_binding_eval.py --db-host localhost --db-port 15432 --full
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_DB_NAME=travel python paper/tdsc/scripts/overhead_breakdown.py
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_DB_NAME=travel python paper/tdsc/scripts/hook_microbenchmark.py
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_DB_NAME=travel TDSC_NATIVE_ROWS=1000,10000 TDSC_NATIVE_WARMUP=1 TDSC_NATIVE_MEASURED=3 python paper/tdsc/scripts/native_end_to_end_benchmark.py
```

The current global single-active binding result is
`raw_results/single_active_binding_20260710_030846.json`.
