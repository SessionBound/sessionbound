# TDSC Preparation Notes

## Positioning

Primary target: IEEE Transactions on Dependable and Secure Computing.

Core framing:

- AI-agent data-access security
- database-enforced authorization
- task-bound execution
- credential-token binding
- query receipts and auditability
- safe SQL exploration over governed business views

The manuscript should be presented as a security and dependable-systems
paper, not as a multi-agent planning, reinforcement learning, or agent
coordination paper.

## Before Submission

- Confirm the current-year CAS / Chinese Academy of Sciences journal
  partition in the version recognized by the author's institution.
- Confirm the latest TDSC author instructions, page limits, template,
  review mode, and open-access choice in the IEEE submission system.
- Decide whether the submitted manuscript should be anonymized according
  to the current TDSC review policy.
- Port `manuscript/tdsc.tex` into the current official IEEE template if
  required by the submission system.

## Required Journal Expansion

- Strengthen the threat model with explicit attacker capabilities,
  trusted computing base, non-goals, and per-threat assumptions.
- Formalize the system model: task template, approval record, token,
  credential, session, safe view, budget vector, receipt, and decision
  function.
- Expand anti-evasion evaluation beyond the 24 functional scenarios:
  catalog access, utility commands, nested CTEs, view aliasing,
  SQL-function payload construction, expensive predicates, and aggregate
  inference attempts.
- Add baseline comparisons:
  - raw PostgreSQL
  - role-only
  - RLS-only
  - safe-view-only
  - SessionBound full
- Run larger-scale benchmarks with at least three dataset sizes and
  report p50, p95, mean, standard deviation, rows returned, and overhead.
- Add PostgreSQL enforcement details for parser/AST validation,
  permission boundaries, credential issuance, task binding, receipt
  insertion, and cleanup of expired roles.
- Expand deployment hardening: KMS/JWKS signing, key rotation,
  out-of-transaction denial logging, schema drift invalidation,
  statement timeout, resource governance, and operational recovery.
- Tighten limitations so the paper is candid about inference risks,
  prototype SQL checking, single-database scope, and safe-view authoring.

## Suggested Journal Structure

1. Introduction
2. Background and Threat Model
3. SessionBound System Model
4. SessionBoundDB Runtime
5. Security Analysis and Anti-Evasion
6. PostgreSQL Implementation
7. Evaluation
8. Related Work
9. Deployment, Limitations, and Production Hardening
10. Conclusion

## Current Draft Status

Already present in the long manuscript:

- threat model
- task template/application/approval/token model
- credential-token binding
- safe-view runtime boundary
- operation policy and query decision procedure
- budget semantics
- schema drift and safe-view versioning
- hash-chained receipt structure
- 24-scenario functional validation
- raw PostgreSQL versus SessionBound microbenchmark
- threat-to-defense matrix
- limitations and future work

Still needed before a serious TDSC submission:

- full baseline matrix
- larger benchmarks
- stronger formal model
- deeper anti-evasion test suite
- official IEEE/TDSC template pass
- institutional CAS partition confirmation

