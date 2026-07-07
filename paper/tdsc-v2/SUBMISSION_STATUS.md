# Submission Status

This is a TDSC-oriented manuscript draft, not a final submission package.

## Current v2 Improvements

- Removed manuscript-body wording that described the paper as a future
  journal plan instead of as the submitted work.
- Reframed the Introduction as the paper's own claim rather than a plan
  for a future version.
- Added measured security and performance baselines for raw PostgreSQL,
  role-only, RLS-only, safe-view-only, and full SessionBound.
- Added v2 experiment reports and raw-result references under
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

## Still Required Before Formal Submission

- Author affiliation and email are confirmed for the current manuscript:
  Minmin Wu, China Telecom Global Limited, wuminmin@chinatelecomglobal.com.
- Confirm the latest TDSC author instructions, review mode, template
  requirements, open-access choice, and submission metadata.
- Confirm the current CAS / Chinese Academy of Sciences journal
  partition using the author's institution-approved list.
- Treat current performance as a prototype limitation unless a lower-level
  parser-hook/runtime path is implemented and measured. The 100k scale
  sweep shows multi-second Full SessionBound p50 in the current PL/pgSQL
  implementation.
- Re-run layout review after the official IEEE/TDSC template and
  submission metadata are finalized.
