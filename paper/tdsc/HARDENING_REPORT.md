# TDSC Hardening Report

## Baseline State

- Date: 2026-07-08
- Branch at run time: `high-standard-tdsc-pdsc-revision`
- Commit at latest hook run time: current branch working tree
- Docker Compose: `Docker Compose version v2.40.3-desktop.1`
- Python: `Python 3.13.5`
- PostgreSQL: `PostgreSQL 16.14 (Debian 16.14-1.pgdg13+1)`
- Current TDSC manuscript path: `paper/tdsc/sessionbound-tdsc.tex`
- Current TDSC PDF path: `paper/tdsc/sessionbound-tdsc.pdf`

Initial branch setup:

```text
## high-standard-tdsc-pdsc-revision
```

## Docker Startup

Commands run from `/home/wmm/taskbound`:

```bash
docker compose down -v || true
docker compose up -d --build api
docker compose ps
sleep 10
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8000/
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```

Startup result:

```text
taskbound-api-1        taskbound-api   Up, 0.0.0.0:8000->8000/tcp
taskbounddb-postgres   taskbound-postgres:16-sessionbound     Up, healthy
GET /                  200
GET /docs              200
```

PostgreSQL version:

```text
PostgreSQL 16.14 (Debian 16.14-1.pgdg13+1) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
```

## Notes

- The repository now uses `paper/tdsc/` as the single active TDSC workspace.
- This hardening sprint records outputs and updates the current TDSC manuscript
  in that consolidated folder.
- This report has been updated to the 2026-07-08 high-standard revision; older
  raw result files in the repository are historical unless listed below.

## Experiment Results

AST validation:

- Script: `paper/tdsc/scripts/ast_validation_eval.py`
- Latest raw result: `paper/tdsc/raw_results/ast_validation_20260708_205921.json`
- Result: 20 / 20 cases passed
- Status: implemented as API-layer AST preflight before the SDK executes native
  safe-view SQL

SDK query surface:

- Implementation: `app/taskbound_sdk.py`
- Script: `paper/tdsc/scripts/sdk_query_eval.py`
- Latest smoke result: docker compose SDK smoke on 2026-07-08
- Result: native query, bound bare SELECT accounting, and unbound fail-closed
  behavior passed
- Status: `TaskboundSession.query(sql)` executes ordinary safe-view SQL
  natively under PostgreSQL hook/executor accounting; `taskbound.run(sql)`
  remains as a compatibility wrapper.

PostgreSQL hook enforcement:

- Extension: `postgres/sessionbound_guard/`
- Load path: `shared_preload_libraries=sessionbound_guard`
- Hooks: PostgreSQL `post_parse_analyze_hook`, `ProcessUtility_hook`, and
  executor hooks.
- Trusted context: SUSET GUCs populated by `taskbound.bind_task(...)`,
  including approved safe-view OIDs from `taskbound.safe_view_registry`.
- Script: `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`
- Latest raw result:
  `paper/tdsc/raw_results/sessionbound_guard_hook_20260708_205325.json`
- Result: 18 / 18 cases passed, including native bound safe-view SELECT,
  prepared EXECUTE, cursor/FETCH, COPY(SELECT), EXPLAIN, blocked
  EXPLAIN ANALYZE, direct entity group-by denial, and HAVING denial.
- Status: native database-resident structural enforcement and executor
  accounting path; API AST preflight remains defense in depth.

Hook-only microbenchmark:

- Script: `paper/tdsc/scripts/hook_microbenchmark.py`
- Latest raw result:
  `paper/tdsc/raw_results/hook_microbenchmark_20260708_205831.json`
- Result: 6 / 6 checks passed.
- Main finding: allowed structural guard checks were 0.125--0.138 ms p50,
  while raw-schema, UNION, direct entity group-by, and HAVING denial checks
  passed. This isolates parse/analyze structural guarding from wrapper
  row materialization/accounting.

Rollback audit:

- Script: `paper/tdsc/scripts/rollback_audit_eval.py`
- Latest raw result:
  `paper/tdsc/raw_results/rollback_audit_20260708_205904.json`
- Result: 2 / 2 cases passed.
- Status: evaluated bound-runtime allowed receipts/accounting and raw-schema
  denial receipts persist after agent-side `BEGIN ... ROLLBACK`.

Adversarial SQL:

- Script: `paper/tdsc/scripts/adversarial_sql_eval.py`
- Latest raw result: `paper/tdsc/raw_results/adversarial_sql_20260708_210059.json`
- Result: 34 / 34 expected classifications passed
- Classification counts: 27 blocked, 7 allowed but accounted, 0 known
  limitations in the tested direct-release suite

Overhead breakdown:

- Script: `paper/tdsc/scripts/overhead_breakdown.py`
- Latest raw JSON: `paper/tdsc/raw_results/overhead_breakdown_20260708_205527.json`
- Latest raw CSV: `paper/tdsc/raw_results/overhead_breakdown_20260708_205527.csv`
- Result: 0 errors across supported modes
- Main finding: raw/role/safe-view modes are sub-millisecond, the
  RLS+Safe View+Short Credential+Audit baseline is 0.976--1.119 ms p50, and
  the full SessionBound wrapper reference path is 17.397--18.616 ms p50.
  Receipt and budget switches do not dominate. The 100k scale sweep remains a
  wrapper-reference limitation, not a production-ready native-hook throughput
  claim.

RLS + Safe View + Short Credential + Audit:

- SQL setup:
  `paper/tdsc/experiments/sql/rls_safe_view_short_credential_audit_baseline.sql`
- Security baseline raw result:
  `paper/tdsc/raw_results/security_baseline_1783515117.json`
- Status: measured as a strong baseline that includes row scope, field-limited
  safe views, read-only grants, short-lived credential, and basic audit logging,
  but no SessionBound task token, cumulative disclosure budget, receipt hash
  chain, or drift-bound token invalidation.

Canonical validation:

- Command: `python3 scripts/sessionbound_agent_eval.py --base-url http://localhost:8000`
- Result: 24 / 24 scenarios passed
- Output: `paper/tdsc/raw_results/sessionbound_agent_eval_1783515659.json` and
  `.md`.
