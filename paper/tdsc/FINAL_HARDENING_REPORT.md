# Final TDSC Hardening Report

## Status

- Branch: `high-standard-tdsc-pdsc-revision`
- Base commit at latest hook/evaluation run time: current branch working tree
- Docker status: API up on port 8000; PostgreSQL healthy
- API health: `/` returned HTTP 200; `/docs` returned HTTP 200
- PostgreSQL: 16.14
- PostgreSQL hook extension: `sessionbound_guard` loaded through
  `shared_preload_libraries`
- Current manuscript path: `paper/tdsc/sessionbound-tdsc.tex`
- TDSC PDF path: `paper/tdsc/sessionbound-tdsc.pdf`
- PDF build status: `make` completed successfully; final PDF is 14 pages with
  no undefined citations, undefined references, fatal errors, or overfull boxes
  in the final log sweep.

## Component Status

- AST validation status: implemented as API-layer preflight with `sqlglot`; 20
  / 20 AST validation cases passed.
- SDK query surface status: implemented in `app/taskbound_sdk.py`; native SDK
  smoke passed, including accounted `query(sql)`, accounted bound bare
  safe-view `SELECT`, and unbound fail-closed behavior.
- PostgreSQL hook status: implemented as native `post_parse_analyze_hook`,
  `ProcessUtility_hook`, and executor accounting with trusted SUSET GUC context
  and approved safe-view OID checks; 18 / 18 hook evaluation cases passed,
  including prepared statements, cursor/FETCH, COPY(SELECT), EXPLAIN, and
  blocked EXPLAIN ANALYZE plus conservative aggregate-shape denials.
- Hook-only microbenchmark status: implemented as an artifact script; 6 / 6
  checks passed, with allowed structural guard checks at 0.125--0.138 ms p50.
- Rollback audit status: implemented as an artifact script; 2 / 2 cases passed,
  showing that evaluated bound-runtime allowed receipts/accounting and
  raw-schema denial receipts survive rollback of the agent transaction.
- Adversarial SQL suite status: implemented; 140 / 140 expected
  classifications passed, including minimum-group aggregate checks.
- Overhead breakdown status: implemented; latest supported modes ran with zero
  measurement errors.
- Scale status: 1k/10k/100k native end-to-end diagnostic benchmark rerun and
  discussed, alongside the historical wrapper reference scale sweep.
- Credential-token binding status: credential-token suite rerun and passed.
- Schema drift status: registry version, policy version, view-definition hash,
  and exposed-column hash drift checks rerun and passed.
- Related work expansion status: 29 verified references in
  `paper/tdsc/references.bib`, all cited.

## Key Results

- AST validation: 20 / 20 passed; latest raw result:
  `paper/tdsc/raw_results/ast_validation_20260708_205921.json`.
- SDK query surface: native SDK smoke passed on 2026-07-08.
- PostgreSQL hook enforcement: 18 / 18 passed; latest raw result:
  `paper/tdsc/raw_results/sessionbound_guard_hook_20260708_205325.json`.
- Hook-only microbenchmark: 6 / 6 passed; latest raw result:
  `paper/tdsc/raw_results/hook_microbenchmark_20260708_205831.json`.
- Rollback audit: 2 / 2 passed; latest raw result:
  `paper/tdsc/raw_results/rollback_audit_20260708_205904.json`.
- Adversarial SQL: 126 blocked, 14 allowed but accounted, and no
  expected-classification failures in the tested direct-release suite;
  latest raw result:
  `paper/tdsc/raw_results/adversarial_sql_20260708_235742.json`.
- Canonical validation: 24 / 24 passed.
- Credential-token and schema drift: 11 / 11 denied or accepted as expected;
  latest raw result:
  `paper/tdsc/raw_results/credential_token_1783515640.json`.
- Overhead: full SessionBound p50 on the default seed was 17.397 ms for SELECT,
  18.616 ms for JOIN, 18.289 ms for GROUP BY, 17.698 ms for CTE, and 17.639 ms
  for window-function queries; RLS+Safe View+Short Credential+Audit was
  0.976--1.119 ms p50; latest raw result:
  `paper/tdsc/raw_results/overhead_breakdown_20260708_205527.json`.
- Native end-to-end scale: at 100k scoped rows, SessionBound wrapper p50 was
  6136.37--6313.42 ms and SessionBound native hook/executor p50 was
  6242.66--6444.11 ms across SELECT/JOIN/GROUP BY/CTE/window query shapes;
  latest raw result:
  `paper/tdsc/raw_results/native_end_to_end_20260708_235456.json`.
- Manuscript audits: no stale draft-language, placeholder, Codex, or prior
  author-name matches in the final manuscript and bibliography sweep.

## Known Remaining Gaps

- The native hook path is still a research prototype: disclosure accounting is
  demo-specific (`expense_id`) and the rollback-surviving audit channel uses
  same-DB `dblink` rather than external WORM storage or managed audit retention.
- Minimum-group policy mitigates direct small-group aggregate release; arbitrary
  semantic inference remains out of scope.
- The budget vector does not provide formal differential privacy guarantees.
- The current wrapper and native hook/executor accounting paths remain too slow
  for production-scale claims. The hook-only microbenchmark shows structural
  guarding is not the multi-second bottleneck, but native accounting still needs
  optimization and a broader workload study.

## Recommendation

The candidate is substantially stronger: native SELECT, executor accounting,
rollback-surviving receipts, prepared/cursor/COPY/EXPLAIN coverage,
RLS+safe-view+short-credential+audit comparison, minimum-group aggregate policy,
and fail-closed behavior are implemented and validated. Before final submission,
rerun layout/BibTeX checks and either measure the native executor-accounting
scale path or keep the wrapper scale table explicitly labeled as reference-path
diagnosis.
