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
- Updated the manuscript to report the measured credential-token binding
  gap instead of overclaiming strict binding.
- Switched the manuscript entrypoint to `\documentclass[journal]{IEEEtran}`.
- Added IEEE keywords.

## Still Required Before Formal Submission

- Author affiliation and email are confirmed for the current manuscript:
  Minmin Wu, China Telecom Global Limited, wuminmin@futurenetech.com.
- Confirm the latest TDSC author instructions, review mode, template
  requirements, open-access choice, and submission metadata.
- Confirm the current CAS / Chinese Academy of Sciences journal
  partition using the author's institution-approved list.
- Implement and re-test strict credential-id-to-token binding, token
  replay rejection across a second credential, and same-session rebind
  rejection.
- Implement and test safe-view version/hash invalidation for schema
  drift.
- Add a receipt-disabled SessionBound variant before claiming isolated
  receipt overhead.
- Run a larger dataset scale sweep before making scale or throughput
  claims.
- Re-run layout review after the official IEEE/TDSC template and
  submission metadata are finalized.
