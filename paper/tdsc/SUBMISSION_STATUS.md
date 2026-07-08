# Submission Status

This is a TDSC-oriented manuscript and artifact candidate, not a final
submission package until the IEEE Author Portal metadata and upload checks are
completed.

## Canonical Artifact

- Artifact tag: `tdsc-submission-2026-07-07`
- Working branch: `tdsc-hardening`
- Manifest: `paper/tdsc/ARTIFACT_MANIFEST.md`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Current compiled length: 14 pages

The TDSC manuscript is the canonical claim contract for this candidate. The
arXiv v1 workspace is an earlier preprint snapshot unless and until an arXiv v2
is prepared from the TDSC text.

## Current Improvements

- Removed manuscript-body wording that described the paper as a future
  journal plan instead of as the submitted work.
- Reframed the Introduction as the paper's own claim rather than a plan
  for a future version.
- Added measured security and performance baselines for raw PostgreSQL,
  role-only, RLS-only, safe-view-only, and full SessionBound.
- Added experiment reports and raw-result references under
  `experiments/`.
- Implemented and measured strict credential-token binding:
  credential-id matching, actor matching, audience validation,
  cross-credential replay rejection, expiration, revocation, and
  same-session rebind rejection.
- Implemented and measured safe-view drift invalidation using registry
  version, policy version, and view-definition hash checks.
- Added receipt/budget ablation and a 1k/10k/100k scoped-row scale sweep.
- Switched the manuscript entrypoint to `\documentclass[journal]{IEEEtran}`.
- Added IEEE keywords.
- Compressed the TDSC manuscript from an over-explanatory long draft into a
  14-page candidate by shortening motivation, discussion, related work, future
  work, and limitations.
- Moved the full canonical validation table out of the manuscript body and into
  the artifact evidence.

## Still Required Before Formal Submission

- Author affiliation and email are confirmed for the current manuscript:
  Minmin Wu, China Telecom Global Limited, wuminmin@chinatelecomglobal.com.
- Confirm the latest TDSC author instructions, review mode, template
  requirements, open-access choice, and submission metadata.
- Confirm the current CAS / Chinese Academy of Sciences journal
  partition using the author's institution-approved list.
- Treat current performance as a prototype limitation even though the
  experimental parse/analyze hook path is now implemented. The 100k scale sweep
  shows multi-second Full SessionBound p50 in the current PL/pgSQL
  implementation, so production-scale claims still require lower-level
  planner/executor integration and runtime optimization. The hook-only
  microbenchmark shows structural guard checks are not the multi-second
  bottleneck, but it is not a native executor-accounting scale benchmark.
- Re-run layout review after the official IEEE/TDSC template and
  submission metadata are finalized.
- Prepare arXiv v2 only after the TDSC text stabilizes; do not treat arXiv v1
  as the current artifact-backed claim contract.
