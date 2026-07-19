# Submission Status

This is now a repository-side TDSC static-review submission candidate for the
narrowed claim stated in `paper/tdsc/sessionbound-tdsc.tex`. The 2026-07-19
implementation pass addresses the static enforcement counterexamples, reruns
the main security/performance suites, refreshes manuscript tables from those
outputs, adds the requested nearest-neighbor related-work matrix, and narrows
the novelty claim. Official IEEE Computer Society/TDSC submission checks were
refreshed on 2026-07-19 and are summarized below. Remaining items are human
submission steps or future work if the paper later broadens its claim; they are
not unresolved evidence blockers for the current conservative claim contract.

## Canonical Artifact

- Artifact tag: `tdsc-resubmit-2026-07-10` (superseded by the 2026-07-19
  working tree and result set; do not submit this older tag as current
  evidence)
- Working branch: `high-standard-tdsc-pdsc-revision`
- Manifest: `paper/tdsc/ARTIFACT_MANIFEST.md`
- Static-review closure audit:
  `paper/tdsc/STATIC_REVIEW_CLOSURE_AUDIT.md`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Current compiled length: 17 pages
- Current abstract length: 199 words

## Official Submission-Format Check: 2026-07-19

Checked sources:

- TDSC CFP/submission landing page:
  `https://www.computer.org/digital-library/journals/tq/cfp-dependable-secure-computing`
- TDSC author-information page linked from IEEE Computer Society author
  resources:
  `https://www.computer.org/csdl/journal/tq/write-for-us/15068?periodical=IEEE+Transactions+on+Dependable+and+Secure+Computing&title=Author+Information`
- IEEE Computer Society author resources:
  `https://www.computer.org/publications/author-resources`

Current local checks:

- PDF builds cleanly with `make` from `paper/tdsc/`.
- Page count is 17 pages. For a TDSC regular paper this is above the 12-page
  regular-paper overlength threshold and therefore should be treated as
  MOPC-subject if accepted, but it is within the currently stated
  submission-length ceiling for regular papers.
- The abstract is 199 words, within the IEEE Computer Society journal guidance
  of 100--200 words for regular/special-issue papers.
- The current PDF is unencrypted, letter-sized, and all listed fonts are
  embedded Type 1 fonts.
- Supplemental material remains outside the main manuscript PDF.
- TDSC does not offer double-anonymous review; no anonymization pass is
  required for this manuscript.
- ORCID and author-account metadata remain human submission steps in
  ScholarOne / IEEE Author Portal and cannot be completed from this repository.

## Rebuild Pass: 2026-07-19

The current worktree addresses the static review's implementation-level
counterexamples for:

- tenant-only `employees`, `departments`, `approval_events`, and
  `ledger_entries` safe views;
- token-specific denied columns not reaching the database path;
- direct `fail_receipt`/`audit_append_receipt` forgery grants;
- catalog-default-allow function policy in the native guard;
- caller-forged `employee_count`/`entity_count` aggregate releases;
- one-second receipt de-duplication; and
- non-atomic allowed-release budget/receipt updates; and
- empty `touched_views` receipt provenance on API, wrapper, and native query
  receipt paths.

During verification, the pass also fixed native full-result `SELECT` delivery
after release-barrier accounting and hardened the Postgres Docker build so
host-compiled extension artifacts cannot be reused inside the PG16 image.

Broad validation on an isolated Docker database passed after these changes:

- AST validation: `ast_validation_20260719_162614.json`, 21 / 21.
- Adversarial SQL: `adversarial_sql_20260719_162614.json`, 140 / 140
  with 130 blocked and 10 allowed/accounted.
- Historical fail-receipt crash triage:
  `fail_receipt_segfault_triage.json`, 0 signal-11 triggers reproduced in the
  one-at-a-time adversarial endpoint probe.
- Native hook/executor: `sessionbound_guard_hook_20260719_230317.json`,
  19 / 19.
- Rollback audit: `rollback_audit_20260719_162553.json`, 5 / 5, including
  API preflight, wrapper, direct native, and rollback-surviving receipt paths.
- Credential-token and safe-view drift:
  `experiments/raw_results/credential_token_1784444204.json`, 11 / 11
  observed denials.
- Safe-view scope completeness:
  `scope_completeness_20260719_112307.json`, 4 / 4 task scenarios and
  14 / 14 non-vacuous safe-view checks.
- Dynamic token-denied field coverage:
  `dynamic_denied_field_20260719_112651.json`, 7 / 7 cases and 21 / 21
  API, direct-wrapper, and direct-native path decisions.
- Aggregate-template gating:
  `aggregate_template_gating_20260719_113133.json`, 12 / 12 cases and
  36 / 36 API, direct-wrapper, and direct-native path decisions.
- Function side-effect/default-deny policy:
  `function_side_effect_20260719_150354.json`, 13 / 13 cases and 38 / 38
  evaluated API, direct-wrapper, and direct-native path decisions, with
  side-effect oracles confirming no `pg_sleep` delay, no held tested session
  advisory lock, no delivered `pg_notify` notification, and blocked temporary
  operator/function mediation.
- Pre-bind runtime mediation: `prebind_runtime_20260719_150119.json`, 7 / 7,
  confirming runtime credentials cannot run ordinary SQL, utility/TEMP SQL, or
  private helpers before binding while `taskbound.bind_task(...)` remains
  usable as the public binding entrypoint.
- Receipt fault/forgeability:
  `receipt_fault_20260719_135735.json`, 7 / 7, covering receipt hash
  recomputation, contiguous receipt sequence, previous-hash chaining, required
  hardened fields, tamper sensitivity, wrong-fence append rejection, direct
  agent forgery attempts, and controlled-command allow/deny receipts.
- Concurrent isolation: `concurrent_isolation_20260719_065546.json`, 6 / 6.
- Single-active binding:
  `single_active_binding_20260719_065728.json`, passed with 20 repeated
  races and 10 contenders.
- Native partial budget: `native_partial_budget_20260719_215902.json`, 1 / 1.
- SDK direct-query smoke: `sdk_query_20260719_082553.json`, 3 / 3.
- Functionally equivalent external-PEP cumulative baseline:
  `functional_equivalent_baseline_20260719_164723.json`, 8 / 8.
- Path consistency:
  `path_consistency_20260719_215758.json`, 13 / 13 across 39 API,
  direct-wrapper, and direct-native observations, with two direct-native
  PostgreSQL pre-analysis errors excluded from receipt equivalence and recorded
  as scoped observations.
- Binding lifecycle: `binding_lifecycle_20260719_142819.json`, 4 / 4,
  including snapshot metadata coverage and release-time view-option drift.
- Control-plane authentication: `control_plane_auth_20260719_140226.json`,
  8 / 8.
- Hook microbenchmark: `hook_microbenchmark_20260719_150550.json`, 7 / 7.
- Overhead breakdown: `overhead_breakdown_20260719_150951.json`.
- Native end-to-end diagnostic:
  `native_end_to_end_20260719_152051.json`, zero query errors across measured
  supported rows.

The current safe aggregate policy is intentionally narrower than earlier
drafts: all direct aggregate release, including ungrouped aggregates, ad-hoc
`GROUP BY`, `HAVING`, filtered aggregate release, and window release, is denied
pending trusted aggregate templates.

## Resolved Blocking Audit: 2026-07-10

Two experiment-validity problems are more serious than the manuscript's stated
"small experimental scale" limitation.

- The earlier functionally equivalent external-PEP baseline had only four
  cases (`EQ01`--`EQ04`) in
  `paper/tdsc/scripts/functional_equivalent_baseline_eval.py`. Its tuple budget
  check was per query (`len(rows) > max_rows`), not the cumulative disclosure
  budget emphasized by the paper. **Resolved 2026-07-19:** the refreshed
  baseline result `functional_equivalent_baseline_20260719_164723.json` has
  8 / 8 passing cases and uses persistent PEP state, cumulative query/tuple
  budgets, execution IDs, idempotent retry, chained decision receipts, and the
  same direct-role bypass TCB distinction.
- The diagnostic 10k scale benchmark prepares 10k scoped rows, but the measured
  SQL shapes return only 10 or 50 rows per query. In
  `native_end_to_end_20260710_110541.json`, the 10k `GROUP BY` and CTE cases
  return 10 rows, while SELECT/JOIN/window return 50 rows. These results should
  not be used to explain a 10k-row disclosure or materialization bottleneck.

The 2026-07-10 native reference-monitor path also had statically identifiable
bypass classes in `postgres/sessionbound_guard/sessionbound_guard.c`.

- Executor accounting skips statements when the original SQL text contains
  runtime-helper substrings such as `taskbound.run` or `taskbound.native_`.
  That makes accounting depend on raw SQL text rather than the analyzed plan.
  **Resolved 2026-07-17:** the raw-text gate (`source_is_runtime_helper_call`)
  is removed from `should_account_query`; it was redundant and exploitable. See
  `paper/revision_notes/native_refmon_text_bypass_fix_20260717.md`.
- `should_account_query` returns false when `GetUserId() != GetSessionUserId()`.
  Pre-bound role switching can therefore avoid executor accounting.
  **Clarified 2026-07-17:** this check is retained as the trusted
  wrapper/native accounting discriminator (`taskbound.run`/`bind_task` are
  SECURITY DEFINER and account themselves in PL/pgSQL; the native bare-SELECT
  path runs as the session user). It is not an exploitable bypass because the
  utility precheck refuses SET ROLE once a binding is active. See
  `paper/revision_notes/native_refmon_text_bypass_fix_20260717.md`.
- Payload aggregation/function policy and aggregate/window release issues are
  addressed in the 2026-07-19 implementation by switching to function
  allowlisting and denying ad-hoc aggregate/window release shapes until
  trusted aggregate templates exist.

The current manuscript resolves this by narrowing the claim. It does not claim
aggregate/window release support before approved aggregate templates exist, and
it states the external PEP as part of the baseline TCB rather than as
database-local complete mediation. Current receipt provenance derives touched
safe views from PostgreSQL parse/analyze safe-view OIDs for safely parseable
bound database SQL and maps those OIDs back to registry names for hashing in
receipts. API preflight receipt writes and unsupported shapes rejected before
parse/analyze, such as set operations or `TABLESAMPLE`, retain an approved-name
fallback as audit context and are not claimed as full relation-set attestation.
The path-consistency result checks the API, direct-wrapper, and direct-native
acceptance predicate over 12 cases; it explicitly records two direct-native
parser/analyzer rejections that occur before native receipt emission rather
than treating them as receipt-equivalent task decisions.

Summary-experiment review: the 2026-07-19 tables now use rerun evidence. The
native end-to-end benchmark was relabeled as a diagnostic detail-release stress
test and now returns 1k/10k detail rows for supported SessionBound shapes. It
does establish a current prototype bottleneck for large detail releases, but
not optimized production throughput. The external-PEP baseline now uses the
same cumulative budget and receipt semantics for the comparison workload, while
still documenting that direct database use of the baseline role bypasses the
external PEP unless complete mediation is part of the deployment TCB.

Novelty review: the defensible contribution is narrow. The manuscript now
includes a nearest-neighbor matrix against TBAC/UCON, Macaroons, Qapla,
Blockaid, Sieve, ShillDB, Estrela, PICACHV,
Progent/AgentSpec/Task Shield, PAuth, and CaMeL/IFC agents. The positioned
contribution is database-centered task-session composition for open-ended SQL
with credential binding, safe-view drift checks, cumulative budgets, and
receipts, not a new standalone authorization model.

Canonical audit notes:

- `paper/revision_notes/tdsc_blocking_audit_20260710.md`
- `paper/revision_notes/tdsc_summary_experiment_review_20260710.md`
- `paper/revision_notes/tdsc_novelty_review_20260710.md`
- `paper/revision_notes/native_refmon_text_bypass_fix_20260717.md` (resolves
  audit finding #3, text-matching gate)

The TDSC manuscript is the canonical claim contract for this candidate. The
arXiv v1 workspace is an earlier preprint snapshot unless and until an arXiv v2
is prepared from the TDSC text.

## Current Improvements

- Removed manuscript-body wording that described the paper as a future
  journal plan instead of as the submitted work.
- Reframed the Introduction as the paper's own claim rather than a plan
  for a future version.
- Added measured security and performance baselines for raw PostgreSQL,
  role-only, safe-view-only, RLS+Safe View+Short Credential+Audit, and
  SessionBound variants.
- Replaced minimum-group alias acceptance with conservative aggregate-template
  gating across API, wrapper, and native paths.
- Added experiment reports and raw-result references under `raw_results/` and
  `experiments/`.
- Replaced the test-side denial receipt write with parser, wrapper/hook, and
  executor denial cases that verify autonomous receipts after rollback.
- Unified wrapper/native disclosure accounting as an atomic conservative
  output-tuple budget; projection aliases, joins, and non-aggregate detail CTEs
  are covered by the projection attack suite, while direct aggregate/window
  release is denied pending approved templates.
- Added receipt-chain fields and autonomous append serialization for the audit
  channel.
- Populated `touched_views` receipt provenance for API preflight denials,
  wrapper receipts, and native query receipts. Safely parseable bound database
  SQL uses OID-derived provenance from PostgreSQL parse/analyze; API preflight
  and unsupported denied syntax use an approved-name fallback. This is audit
  context, not the enforcement predicate.
- Implemented and measured strict credential-token binding:
  credential-id matching, actor matching, audience validation,
  cross-credential replay rejection, expiration, revocation, and
  same-session rebind rejection.
- Implemented safe-view drift invalidation using registry version, policy
  version, database/view identity, dependency hash, option hash,
  view-definition hash, and exposed-column hash checks. The current lifecycle
  run directly checks snapshot metadata coverage and release-time view-option
  drift denial.
- Added a scope-completeness evaluator that checks every task-allowed safe view
  in monthly, finance workflow, and payment ledger task shapes against
  raw-table tenant/month/department provenance.
- Added a dynamic denied-field evaluator showing that a token-specific
  `expenses.amount` denial fails at bind across API, direct-wrapper, and
  direct-native paths before any query shape can bypass it.
- Added an aggregate-template gating evaluator for the minimum-group bypass
  family: forged cardinality aliases, one-employee filtered aggregate,
  ordinary grouping, CTE/subquery aggregate release, and window partition
  release are denied consistently across API, direct-wrapper, and native paths.
- Added a function side-effect/default-deny evaluator covering `pg_sleep`,
  advisory locks, notifications, session/config probes, catalog and file
  introspection, SRFs, payload serialization, and an unlisted aggregate across
  API, direct-wrapper, and direct-native paths.
- Retained a historical fail-receipt backend-crash triage probe and recorded a
  zero-trigger raw result for the one-at-a-time adversarial endpoint run.
- Added a receipt fault/forgeability evaluator that recomputes the receipt hash
  chain from raw database rows, verifies hardened fields and tamper
  sensitivity, rejects wrong-fence append attempts, and confirms direct agent
  calls to receipt/native helper surfaces cannot forge receipts.
- Added receipt/budget ablation and a 1k/10k scoped-row detail-release
  diagnostic.
- Added the related-work nearest-neighbor matrix requested by the novelty
  review, and narrowed the novelty wording around PostgreSQL task-session
  composition.
- Replaced the four-case external-PEP smoke with an 8-case cumulative baseline
  covering persistent external-PEP state, cumulative query/tuple budgets,
  execution-id idempotence, chained receipts, credential replay denial, and the
  direct-role bypass TCB distinction.
- Added a path-consistency evaluator covering allowed detail projection, join,
  non-aggregate CTE, denied columns, raw schema, catalog access, set
  operations, aggregate/window release, side-effect functions, and
  `TABLESAMPLE` across API, direct-wrapper, and direct-native paths.
- Switched the manuscript entrypoint to `\documentclass[journal]{IEEEtran}`.
- Added IEEE keywords.
- Compressed the TDSC manuscript from an over-explanatory long draft into a
  shorter regular-paper candidate by shortening motivation, discussion, related
  work, and limitations.
- Moved the full canonical validation table out of the manuscript body and into
  the artifact evidence.

## Remaining Human Submission Steps and Future Work

- Re-run the refreshed full suite after any further implementation, table, or
  claim-text changes, and keep only successful current-result files in the
  submission manifest.
- Complete human submission metadata: ORCID/account checks, author profile,
  open-access choice, cover-letter/supporting-file upload, and any institution
  or funder disclosures required by the author.
- Confirm the current CAS / Chinese Academy of Sciences journal partition using
  the author's institution-approved list if that classification matters for
  internal approval.
- Treat the current 17-page regular-paper candidate as MOPC-subject if
  accepted, because it exceeds the 12-page regular-paper threshold.
- Do not broaden the manuscript to claim positive aggregate/window release
  until aggregate templates are designed and validated. The current artifact
  validates conservative denial.
- Do not claim universal relation-set attestation until exact OID-derived
  `touched_views` coverage extends to every unsupported denied syntax form. The
  current claim treats fallback provenance as audit context.
- Extend tests for JSON aggregation aliases, additional window partition
  variants, cursor lifecycle variants, and broader denial-receipt coverage only
  if a future manuscript revision needs those stronger claims.
- Extend the external PEP comparison only if future manuscript claims require
  broader workloads; the current 8-case baseline covers the cumulative-budget
  and receipt semantics needed for the present comparison workload.

- Author affiliation and email are confirmed for the current manuscript:
  Minmin Wu, China Telecom Global Limited, wuminmin@chinatelecomglobal.com.
- Treat current performance as a prototype limitation. The native end-to-end
  benchmark shows multi-second SessionBound p50 at 10k detail rows in both
  current wrapper and native hook/executor accounting paths, so
  production-scale claims still require runtime optimization and broader
  workload study. The hook-only microbenchmark shows structural guard checks
  are not the multi-second bottleneck.
- Prepare arXiv v2 only after the TDSC text stabilizes; do not treat arXiv v1
  as the current artifact-backed claim contract.
