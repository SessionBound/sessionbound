# Final TDSC Hardening Report

## Status

- Branch: `tdsc-hardening`
- Base commit at latest hook/evaluation run time: `5dbece5`
- Docker status: API up on port 8000; PostgreSQL healthy
- API health: `/` returned HTTP 200; `/docs` returned HTTP 200
- PostgreSQL: 16.14
- PostgreSQL hook extension: `sessionbound_guard` loaded through
  `shared_preload_libraries`
- Current manuscript path: `paper/tdsc/sessionbound-tdsc.tex`
- TDSC PDF path: `paper/tdsc/sessionbound-tdsc.pdf`
- PDF build status: clean BibTeX/PDF build with no undefined citations,
  undefined references, fatal errors, or overfull boxes reported in the final
  log sweep.

## Component Status

- AST validation status: implemented as API-layer preflight with `sqlglot`; 17
  / 17 AST validation cases passed.
- SDK query surface status: implemented in `app/taskbound_sdk.py`; native SDK
  smoke passed, including accounted `query(sql)`, accounted bound bare
  safe-view `SELECT`, and unbound fail-closed behavior.
- PostgreSQL hook status: implemented as native `post_parse_analyze_hook`,
  `ProcessUtility_hook`, and executor accounting with trusted SUSET GUC context
  and approved safe-view OID checks; 16 / 16 hook evaluation cases passed,
  including prepared statements, cursor/FETCH, COPY(SELECT), EXPLAIN, and
  blocked EXPLAIN ANALYZE.
- Rollback audit status: implemented as an artifact script; 2 / 2 cases passed,
  showing that evaluated bound-runtime allowed receipts/accounting and
  raw-schema denial receipts survive rollback of the agent transaction.
- Adversarial SQL suite status: implemented; 28 / 28 expected classifications
  passed.
- Overhead breakdown status: implemented; latest supported modes ran with zero
  measurement errors.
- Scale/concurrency status: prior scale/concurrency results retained and
  discussed; the latest overhead run isolates small-dataset ablation costs.
- Credential-token binding status: prior credential-token suite retained in
  manuscript; canonical validation still passes after AST integration.
- Schema drift status: prior schema drift checks retained in manuscript.
- Related work expansion status: 29 verified references in
  `paper/tdsc/references.bib`, all cited.

## Key Results

- AST validation: 17 / 17 passed; latest raw result:
  `paper/tdsc/raw_results/ast_validation_20260707_182706.json`.
- SDK query surface: native SDK smoke passed on 2026-07-08.
- PostgreSQL hook enforcement: 16 / 16 passed; latest raw result:
  `paper/tdsc/raw_results/sessionbound_guard_hook_20260708_122824.json`.
- Rollback audit: 2 / 2 passed; latest raw result:
  `paper/tdsc/raw_results/rollback_audit_20260708_141120.json`.
- Adversarial SQL: 22 blocked, 5 allowed but accounted, 1 known limitation;
  latest raw result:
  `paper/tdsc/raw_results/adversarial_sql_20260707_182701.json`.
- Canonical validation: 24 / 24 passed.
- Overhead: full SessionBound p50 on the default seed was 14.630 ms for SELECT,
  18.581 ms for JOIN, 15.024 ms for GROUP BY, 15.425 ms for CTE, and 15.854 ms
  for window-function queries; latest raw result:
  `paper/tdsc/raw_results/overhead_breakdown_20260707_084853.json`.
- Manuscript audits: no stale draft-language, placeholder, Codex, or prior
  author-name matches in the final manuscript and bibliography sweep.

## Known Remaining Gaps

- The native hook path is still a research prototype: disclosure accounting is
  demo-specific (`expense_id`) and the rollback-surviving audit channel uses
  same-DB `dblink` rather than external WORM storage or managed audit retention.
- Small-group aggregate inference remains a known limitation.
- The budget vector does not provide formal differential privacy guarantees.
- The old PL/pgSQL wrapper baseline remains too slow for production-scale
  claims; the new native path needs a fresh overhead sweep before replacing the
  existing benchmark table.
- RLS-only was not remeasured in the latest overhead breakdown.

## Recommendation

The candidate is substantially stronger: native SELECT, executor accounting,
rollback-surviving receipts, prepared/cursor/COPY/EXPLAIN coverage, and
fail-closed behavior are implemented and validated. Before final submission,
rerun the overhead/scale tables on the native path or clearly label older
wrapper measurements as historical.
