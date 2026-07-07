# Final TDSC Hardening Report

## Status

- Branch: `tdsc-hardening`
- Base commit at latest hook/evaluation run time: `c8b10b8`
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
- PostgreSQL hook status: implemented as experimental
  `post_parse_analyze_hook` enforcement with trusted SUSET GUC context and
  approved safe-view OID checks; 10 / 10 hook evaluation cases passed.
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
  `paper/tdsc/raw_results/ast_validation_20260707_163514.json`.
- PostgreSQL hook enforcement: 10 / 10 passed; latest raw result:
  `paper/tdsc/raw_results/sessionbound_guard_hook_20260707_164553.json`.
- Adversarial SQL: 22 blocked, 5 allowed but accounted, 1 known limitation;
  latest raw result:
  `paper/tdsc/raw_results/adversarial_sql_20260707_163408.json`.
- Canonical validation: 24 / 24 passed.
- Overhead: full SessionBound p50 on the default seed was 14.630 ms for SELECT,
  18.581 ms for JOIN, 15.024 ms for GROUP BY, 15.425 ms for CTE, and 15.854 ms
  for window-function queries; latest raw result:
  `paper/tdsc/raw_results/overhead_breakdown_20260707_084853.json`.
- Manuscript audits: no stale draft-language, placeholder, Codex, or prior
  author-name matches in the final manuscript and bibliography sweep.

## Known Remaining Blockers

- The database hook path is experimental parse/analyze enforcement, not yet a
  production-grade planner/executor hook with optimized accounting.
- Small-group aggregate inference remains a known limitation.
- The budget vector does not provide formal differential privacy guarantees.
- The PL/pgSQL reference runtime remains too slow for production-scale claims.
- RLS-only was not remeasured in the latest overhead breakdown.

## Recommendation

Not ready for final TDSC submission yet. The candidate is technically stronger
and more honest than the prior draft, and it now has a real database hook
prototype. The remaining production-hook, inference-control, and performance
gaps should be addressed or explicitly accepted before submission.
