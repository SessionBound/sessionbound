# Submission Status

This is a TDSC-oriented manuscript and artifact candidate, but it is currently
blocked for formal submission. The latest independent evidence review found
substantive experiment and enforcement issues that invalidate several current
claims. The recommended editorial status is reject-and-rebuild, not minor
revision.

## Canonical Artifact

- Artifact tag: `tdsc-resubmit-2026-07-10` (now superseded by the blocking
  audit below; do not submit this tag as claim-complete evidence)
- Working branch: `high-standard-tdsc-pdsc-revision`
- Manifest: `paper/tdsc/ARTIFACT_MANIFEST.md`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Current compiled length: 16 pages

## Blocking Audit: 2026-07-10

Two experiment-validity problems are more serious than the manuscript's stated
"small experimental scale" limitation.

- The functionally equivalent external-PEP baseline has only four cases
  (`EQ01`--`EQ04`) in
  `paper/tdsc/scripts/functional_equivalent_baseline_eval.py`. Its tuple budget
  check is per query (`len(rows) > max_rows`), not the cumulative disclosure
  budget emphasized by the paper. It therefore cannot support a fairness claim
  against SessionBound's cumulative accounting.
- The diagnostic 10k scale benchmark prepares 10k scoped rows, but the measured
  SQL shapes return only 10 or 50 rows per query. In
  `native_end_to_end_20260710_110541.json`, the 10k `GROUP BY` and CTE cases
  return 10 rows, while SELECT/JOIN/window return 50 rows. These results should
  not be used to explain a 10k-row disclosure or materialization bottleneck.

The native reference-monitor path also has statically identifiable bypass
classes in `postgres/sessionbound_guard/sessionbound_guard.c`.

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
- The native payload-aggregation denylist omits JSON aggregate spellings covered
  by the API validator, including `json_array_agg` and `json_arrayagg`.
- Window partitions are not checked with the same completeness as GROUP BY and
  HAVING shapes, leaving small-group/inference variants outside the native
  structural check.

Together these issues break the current evidence for I3, I4, I6, I7, and I9.
The safe path should be treated as independently refuted until the native
reference monitor is redesigned and retested.

Summary-experiment review: existing summary tables must be downgraded to
diagnostic smoke evidence. The artifact currently demonstrates some bounded
behaviors, but it does not establish a fair external-PEP comparison, a 10k-row
detail-release bottleneck, or complete native enforcement.

Novelty review: the defensible contribution is narrow. Current related work
already covers task-scoped operation authorization, database-enforced
identity/context access for agentic AI, governed data-product/MCP querying, and
agent information-flow control. SessionBound may still be positioned as a
database-centered task-capability composition for open-ended SQL with budgets
and receipts, but only after the reference-monitor and cumulative-budget
evidence are rebuilt.

Canonical blocking notes:

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
- Added minimum-group aggregate policy in the API preflight and wrapper runtime,
  with conservative native hook shape checks.
- Added experiment reports and raw-result references under `raw_results/` and
  `experiments/`.
- Replaced the test-side denial receipt write with parser, wrapper/hook, and
  executor denial cases that verify autonomous receipts after rollback.
- Unified wrapper/native disclosure accounting as an atomic conservative
  output-tuple budget; projection aliases, joins, and aggregates are covered
  by the projection attack suite.
- Added receipt-chain fields and autonomous append serialization for the audit
  channel.
- Implemented and measured strict credential-token binding:
  credential-id matching, actor matching, audience validation,
  cross-credential replay rejection, expiration, revocation, and
  same-session rebind rejection.
- Implemented and measured safe-view drift invalidation using registry version,
  policy version, view-definition hash, and exposed-column hash checks.
- Added receipt/budget ablation and a 1k/10k/100k scoped-row scale sweep.
- Switched the manuscript entrypoint to `\documentclass[journal]{IEEEtran}`.
- Added IEEE keywords.
- Compressed the TDSC manuscript from an over-explanatory long draft into a
  16-page candidate by shortening motivation, discussion, related work, future
  work, and limitations.
- Moved the full canonical validation table out of the manuscript body and into
  the artifact evidence.

## Still Required Before Formal Submission

- Rebuild the native reference monitor around analyzed plans/relations rather
  than raw SQL substring exemptions; remove role-switch accounting bypasses.
- Extend native checks and tests for JSON aggregation aliases and window
  partition/small-group variants.
- Replace the four-case external PEP smoke test with a cumulative-budget,
  multi-query baseline that shares the same workload and accounting semantics as
  SessionBound.
- Replace or relabel the 10k scale experiment. If the claim is large-result
  disclosure/materialization, measured queries must actually disclose large
  results; otherwise present the current run only as scoped-data smoke evidence.
- Rewrite manuscript tables, invariants, limitations, and conclusion to match
  the rebuilt artifact, or explicitly state that the current prototype fails
  these paths.

- Author affiliation and email are confirmed for the current manuscript:
  Minmin Wu, China Telecom Global Limited, wuminmin@chinatelecomglobal.com.
- Confirm the latest TDSC author instructions, review mode, template
  requirements, open-access choice, and submission metadata.
- Confirm the current CAS / Chinese Academy of Sciences journal
  partition using the author's institution-approved list.
- Treat current performance as a prototype limitation. The native end-to-end
  benchmark shows multi-second SessionBound p50 at 100k rows in both current
  wrapper and native hook/executor accounting paths, so production-scale claims
  still require runtime optimization and broader workload study. The hook-only
  microbenchmark shows structural guard checks are not the multi-second
  bottleneck.
- Re-run layout review after the official IEEE/TDSC template and
  submission metadata are finalized.
- Prepare arXiv v2 only after the TDSC text stabilizes; do not treat arXiv v1
  as the current artifact-backed claim contract.
