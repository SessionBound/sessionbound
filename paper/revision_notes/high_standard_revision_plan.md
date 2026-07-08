# High-Standard TDSC/PDSC Revision Plan

Branch: `high-standard-tdsc-pdsc-revision`

## Current Artifact Map

- Current paper main file: `paper/tdsc/sessionbound-tdsc.tex`
- Bibliography: `paper/tdsc/references.bib`
- LaTeX build entry: `paper/tdsc/Makefile`
- Current primary raw results path: `paper/tdsc/raw_results/`
- Older baseline raw results path: `paper/tdsc/experiments/raw_results/`
- Canonical validation script: `scripts/sessionbound_agent_eval.py`
- AST validation script: `paper/tdsc/scripts/ast_validation_eval.py`
- Adversarial SQL suite: `paper/tdsc/scripts/adversarial_sql_eval.py`
- Native hook/direct DB suite: `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`
- Hook-only microbenchmark: `paper/tdsc/scripts/hook_microbenchmark.py`
- Current overhead benchmark: `paper/tdsc/scripts/overhead_breakdown.py`
- Older TDSC baseline/security/performance/scale scripts: `paper/tdsc/experiments/scripts/`
- Older baseline SQL: `paper/tdsc/experiments/sql/`
- PostgreSQL extension/hook code: `postgres/sessionbound_guard/sessionbound_guard.c`
- Extension SQL/control/Makefile: `postgres/sessionbound_guard/sessionbound_guard--0.1.sql`, `postgres/sessionbound_guard/sessionbound_guard.control`, `postgres/sessionbound_guard/Makefile`
- FastAPI control plane: `app/api.py`
- Task registry and token construction: `app/task_registry.py`
- SDK query path: `app/taskbound_sdk.py`
- API AST validator: `app/sql_ast_validator.py`
- Runtime SQL / PLpgSQL:
  - `db/001_schema.sql`
  - `db/003_runtime_core.sql`
  - `db/004_safe_views.sql`
  - `db/005_query_runtime.sql`
  - `db/006_commands_and_grants.sql`
- Docker Compose: `docker-compose.yml`

## Currently Supported Execution Paths

1. Wrapper/API reference path
   - API endpoint: `/agent-query`
   - API preflight: `app/sql_ast_validator.py`
   - DB execution: `TaskboundSession.query(sql)` uses native safe-view SQL; `TaskboundSession.query_via_runtime(sql)` and benchmark paths also use `taskbound.run(sql)` compatibility wrapper.
   - Accounting: `taskbound.run(sql)` materializes rows before release, accounts unique `expense_id`, debits query count, and emits receipts. Native SDK path uses executor hooks for row/accounting when direct `SELECT` is used.
   - Receipts: allow and denial receipts exist; native/hook/API denials use rollback-surviving dblink audit path.

2. Native PostgreSQL hook/executor path
   - Extension: `sessionbound_guard`
   - Hooks: `post_parse_analyze_hook`, `ProcessUtility_hook`, `ExecutorStart`, `ExecutorRun`, `ExecutorFinish`, `ExecutorEnd`
   - Enforcement: safe-view OID checks, raw/catalog denial, set-operation denial, payload aggregation denial, utility checks, sensitive output alias checks.
   - Accounting: executor-level rows and unique `expense_id` values are counted for direct native `SELECT`.
   - Tested by: `paper/tdsc/scripts/sessionbound_guard_hook_eval.py`

3. Hook-only structural microbenchmark
   - Entry: `public.sessionbound_guard_check(sql)`
   - Measures parse/analyze and structural guard only.
   - Does not execute rows, account disclosure, or emit receipts.
   - Current manuscript uses 0.130--0.169 ms p50 from `paper/tdsc/raw_results/hook_microbenchmark_20260708_181304.json`.

## Currently Supported Baselines

- Raw PostgreSQL:
  - Existing direct admin/raw-table execution in both new and old scripts.
- Role-only:
  - `paper/tdsc/scripts/overhead_breakdown.py` creates a read-only raw-table role.
  - Older SQL: `paper/tdsc/experiments/sql/role_only_baseline.sql`
- Safe-view-only:
  - Current overhead script simulates this by binding a task and disabling receipts/budget accounting, which still uses SessionBound binding and safe-view predicates.
  - Older SQL defines an independent `tdsc_safe_view_only` schema and role.
- RLS-only / RLS baseline:
  - Older `paper/tdsc/experiments/sql/rls_baseline.sql` defines raw-table RLS policies and a role.
  - Current overhead script reports RLS as unsupported/N/A.
  - Current paper simultaneously includes older RLS scale/security values, causing a measured-baseline contradiction.
- SessionBound ablations:
  - No receipts and no budget toggles are available through token `runtime_options`.

## Claims or Implied Capabilities Needing Fixes

- RLS baseline consistency:
  - The paper says current overhead run does not support RLS-only, but security and scale tables include RLS values.
  - Fix direction: implement a strong baseline named `RLS + Safe View + Short-Lived Credential + Audit` in current benchmark/security scripts and rerun, or mark RLS as mechanism-only/N/A everywhere.
- Small-group aggregate inference:
  - Current adversarial suite has one known limitation: group-by employee small-group aggregate is allowed.
  - Fix direction: implement configurable minimum-group-size policy, add tests, and update manuscript from future work to partial mitigation.
- Hook-only performance interpretation:
  - The abstract and evaluation need clearer separation between full wrapper/reference overhead, native executor-accounting path, and hook-only structural microbenchmark.
- Native executor-accounting scale:
  - Native executor accounting exists, but no fresh native scale overhead table exists.
  - Fix direction: do not claim native scale performance unless measured; state that native executor-accounting scale remains to be measured.
- Exposed-column hash drift:
  - Token snapshot validates registry version, policy version, and view-definition hash.
  - It does not currently include a separate exposed-column hash.
  - Fix direction: either implement and test, or mark as not implemented/N/A.
- Public artifact claim:
  - The abstract claims a public GitHub URL. Need verify actual repository/public status before keeping this claim; otherwise change to release-upon-submission/publication wording.
- Page count:
  - Existing artifact manifest notes 15 pages. Target is <=14 pages if possible without cutting core threat model/invariants/path matrix/evaluation diagnosis.

## Current Consistency Issues to Resolve

- Table II adversarial SQL reports 28 cases with one known limitation; after min-group policy this must become >=34 cases and no longer list the direct small-group case as unmitigated.
- Table III hook suite reports 16/16; after native min-group conservative checks this count will change and must be rerun.
- Table IV security-property comparison calls RLS a measured baseline while current overhead text says RLS is unsupported.
- Table V overhead table uses current V3 results without RLS, while text and related tables refer to RLS elsewhere.
- Table VI scale table includes old RLS numbers and old wrapper results; it must be regenerated or clearly marked historical.
- Abstract combines wrapper scale bottleneck and hook-only structural microbenchmark too tightly; must explicitly say hook-only is not end-to-end SessionBound performance.
- Limitations still state small-group aggregate inference remains a known limitation; this should become partial direct mitigation plus arbitrary semantic inference out of scope after implementation.
- Implementation text says native executor accounting is implemented; performance text must not imply native executor-accounting scale has been measured unless rerun.
