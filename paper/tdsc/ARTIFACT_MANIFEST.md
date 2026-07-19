# TDSC Artifact Manifest

This manifest identifies the current artifact set for the TDSC-oriented
SessionBound submission candidate on branch
`high-standard-tdsc-pdsc-revision`.

## Canonical Version

- Working branch: `high-standard-tdsc-pdsc-revision`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Static-review closure audit: `paper/tdsc/STATIC_REVIEW_CLOSURE_AUDIT.md`
- Build command: `cd paper/tdsc && make`
- Current page count: 17 pages
- Current abstract length: 199 words
- Current status: repository-side static-review submission candidate for the
  narrowed claim; human submission steps and future-work boundaries are listed
  in `paper/tdsc/SUBMISSION_STATUS.md`.

Older raw-result files remain in the tree as historical records. Current paper
tables should cite the 2026-07-19 result files listed below.

## Current Claim Contract

The TDSC manuscript is the canonical claim contract for this artifact. It
states the current, narrowed claim: SessionBound composes signed task tokens,
short-lived credentials, safe views, SQL-shape policy, cumulative output-tuple
budgets, drift checks, fenced receipt/budget transitions, rollback-surviving
receipts, and global single-active binding into a database-enforced task
session. Its related-work section now includes the explicit nearest-neighbor
matrix against task/usage-control models, contextual credentials, database
policy mediators, agent tool-policy systems, and IFC-style agent runtimes.

Current limitations remain explicit:

- direct aggregate/window release is denied pending approved aggregate
  templates;
- `touched_views` receipt provenance is OID-derived for safely parseable bound
  database SQL, while API preflight and unsupported denied syntax use an
  approved-name fallback; it is audit context, not an independent universal
  relation-set attestation;
- the external-PEP baseline covers cumulative accounting and receipt semantics
  for the comparison workload, but still relies on complete mediation by the
  external PEP as part of its TCB;
- performance is diagnostic prototype evidence, not optimized production
  throughput.

Submission-format checks were refreshed against IEEE Computer Society/TDSC
pages on 2026-07-19. The current PDF is a 17-page regular-paper candidate with
a 199-word abstract, embedded Type 1 fonts, no LaTeX blocking warnings, and no
supplemental material embedded in the main PDF. The length should be treated as
MOPC-subject if accepted because it exceeds the 12-page regular-paper threshold;
ORCID/account metadata remain human submission steps.

## Current Results: 2026-07-19

- AST validation:
  `paper/tdsc/raw_results/ast_validation_20260719_162614.json` — 21 / 21.
- Adversarial SQL:
  `paper/tdsc/raw_results/adversarial_sql_20260719_162614.json` — 140 / 140;
  130 blocked and 10 allowed/accounted.
- Native hook/executor:
  `paper/tdsc/raw_results/sessionbound_guard_hook_20260719_230317.json` —
  19 / 19.
- Rollback audit:
  `paper/tdsc/raw_results/rollback_audit_20260719_162553.json` — 5 / 5,
  including API preflight touched-view provenance.
- Credential-token and safe-view drift:
  `paper/tdsc/experiments/raw_results/credential_token_1784444204.json` —
  11 / 11 observed denials.
- Safe-view scope completeness:
  `paper/tdsc/raw_results/scope_completeness_20260719_112307.json` —
  4 / 4 task scenarios and 14 / 14 non-vacuous safe-view checks. Each returned
  safe-view row is checked against raw-table tenant/month/department
  provenance.
- Dynamic token-denied field coverage:
  `paper/tdsc/raw_results/dynamic_denied_field_20260719_112651.json` —
  7 / 7 cases and 21 / 21 API, direct-wrapper, and direct-native path
  decisions. The control token can read `expenses.amount`; the dynamic
  `expenses.amount` denial is rejected at bind for projection, alias, `WHERE`,
  `ORDER BY`, aggregate-input, and window-expression shapes.
- Aggregate-template gating:
  `paper/tdsc/raw_results/aggregate_template_gating_20260719_113133.json` —
  12 / 12 cases and 36 / 36 API, direct-wrapper, and direct-native path
  decisions. The blocked cases cover forged cardinality aliases, a
  one-employee filtered aggregate, ordinary grouping, CTE/subquery aggregate
  release, and window partition release; each blocked decision emitted one
  denial receipt.
- Function side-effect/default-deny policy:
  `paper/tdsc/raw_results/function_side_effect_20260719_150354.json` —
  13 / 13 cases and 38 / 38 evaluated API, direct-wrapper, and direct-native
  path decisions. The side-effect oracles cover `pg_sleep`, session advisory
  locks, `pg_notify`, and temporary-operator/function mediation; the broader
  blocked set covers session/config functions, privilege/file introspection,
  SRFs, payload serialization, and an unlisted aggregate.
- Pre-bind runtime credential mediation:
  `paper/tdsc/raw_results/prebind_runtime_20260719_150119.json` — 7 / 7.
  The evaluator confirms runtime credentials cannot run ordinary `SELECT`,
  schema-qualified safe-view reads, side-effect functions, TEMP/utility SQL,
  prepared statements, or private runtime helpers before binding, while the
  public `taskbound.bind_task(...)` entrypoint still permits a legitimate
  bound safe-view query.
- Receipt fault/forgeability:
  `paper/tdsc/raw_results/receipt_fault_20260719_135735.json` — 7 / 7.
  The evaluator recomputes receipt hashes from hardened fields, verifies
  contiguous receipt sequences, previous-hash chaining, and tamper sensitivity,
  confirms wrong-fence append rejection, checks direct agent forgery attempts
  against receipt/runtime helper surfaces, and covers controlled-command
  allow/deny receipts.
- Concurrent isolation:
  `paper/tdsc/raw_results/concurrent_isolation_20260719_065546.json` — 6 / 6.
- Global single-active binding:
  `paper/tdsc/raw_results/single_active_binding_20260719_065728.json` —
  passed; 20 repeated races, 10 contenders, zero dual-success rounds, zero
  zero-winner rounds, zero unexpected errors.
- Native partial budget:
  `paper/tdsc/raw_results/native_partial_budget_20260719_215902.json` —
  1 / 1.
- SDK query surface:
  `paper/tdsc/raw_results/sdk_query_20260719_082553.json` — 3 / 3.
- Functionally equivalent external-PEP cumulative baseline:
  `paper/tdsc/raw_results/functional_equivalent_baseline_20260719_164723.json`
  — 8 / 8; covers persistent PEP state, cumulative query/tuple budgets,
  execution-id idempotence, chained receipts, credential replay denial, and the
  direct-role bypass TCB distinction.
- Path consistency:
  `paper/tdsc/raw_results/path_consistency_20260719_215758.json` — 13 / 13
  across 39 API, direct-wrapper, and direct-native observations. Two
  direct-native PostgreSQL pre-analysis errors are recorded as scoped
  observations rather than receipt-equivalent task decisions.
- Binding lifecycle:
  `paper/tdsc/raw_results/binding_lifecycle_20260719_142819.json` — 4 / 4,
  including snapshot metadata coverage and release-time view-option drift.
- Control-plane authentication:
  `paper/tdsc/raw_results/control_plane_auth_20260719_140226.json` — 8 / 8.
- Overhead breakdown:
  `paper/tdsc/raw_results/overhead_breakdown_20260719_150951.json` and
  `paper/tdsc/raw_results/overhead_breakdown_20260719_150951.csv`.
- Hook-only microbenchmark:
  `paper/tdsc/raw_results/hook_microbenchmark_20260719_150550.json` — 7 / 7.
- Native end-to-end diagnostic:
  `paper/tdsc/raw_results/native_end_to_end_20260719_152051.json` and
  `paper/tdsc/raw_results/native_end_to_end_20260719_152051.csv`; zero query
  errors across measured supported rows.

## Reproducibility Map

- Main hardening scripts: `paper/tdsc/scripts/`
- Current raw results: `paper/tdsc/raw_results/`
- Current credential-token experiment result:
  `paper/tdsc/experiments/raw_results/credential_token_1784444204.json`
- Functional validation summary:
  `paper/tdsc/evaluation/FUNCTIONAL_VALIDATION.md`
- Security invariants companion: `paper/tdsc/SECURITY_INVARIANTS.md`
- Submission status, human submission steps, and future-work boundaries:
  `paper/tdsc/SUBMISSION_STATUS.md`
- Changelog: `CHANGELOG_TDSC_HARDENING.md`

Useful reproduction commands after `docker compose up -d --build`:

```bash
python paper/tdsc/scripts/ast_validation_eval.py
python paper/tdsc/scripts/adversarial_sql_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/sessionbound_guard_hook_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/rollback_audit_eval.py --base-url http://localhost:8000
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_DB_NAME=travel python paper/tdsc/experiments/scripts/tdsc_credential_token_tests.py
python paper/tdsc/scripts/scope_completeness_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/dynamic_denied_field_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/aggregate_template_gating_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/function_side_effect_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/prebind_runtime_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/receipt_fault_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/concurrent_isolation_eval.py --db-host localhost --db-port 15432 --db-name travel
python paper/tdsc/scripts/single_active_binding_eval.py --db-host localhost --db-port 15432 --db-name travel --rounds 20 --contenders 10
python paper/tdsc/scripts/native_partial_budget_eval.py --dsn postgresql://agent_app:agentpass@localhost:15432/travel
python paper/tdsc/scripts/sdk_query_eval.py --database-url postgresql://agent_app:agentpass@localhost:15432/travel
python paper/tdsc/scripts/functional_equivalent_baseline_eval.py --base-url http://localhost:8000
python paper/tdsc/scripts/path_consistency_eval.py --base-url http://localhost:8000
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_DB_NAME=travel TDSC_BASE_URL=http://localhost:8000 python paper/tdsc/scripts/overhead_breakdown.py
python paper/tdsc/scripts/hook_microbenchmark.py --warmup 20 --measured 200
TDSC_DB_HOST=localhost TDSC_DB_PORT=15432 TDSC_DB_NAME=travel TDSC_BASE_URL=http://localhost:8000 TDSC_NATIVE_ROWS=1000,10000 TDSC_NATIVE_WARMUP=1 TDSC_NATIVE_MEASURED=3 TDSC_NATIVE_MAX_QUERIES_PER_TASK=20 python paper/tdsc/scripts/native_end_to_end_benchmark.py
```

Do not run the credential-token drift test concurrently with suites that assume
stable safe-view registry metadata.

## Relationship to arXiv v1

`paper/arxiv-v1/` is an earlier preprint workspace. It remains useful as a
public historical snapshot, but the TDSC candidate has a compressed structure,
updated security argument, updated hardening evidence, and a different page
budget. If an arXiv v2 is prepared, it should be generated from the TDSC
candidate after submission text stabilizes.
