# TDSC Artifact Manifest

This manifest identifies the artifact set for the TDSC-oriented
SessionBound submission candidate.

## Canonical Version

- Artifact tag: `tdsc-submission-2026-07-07`
- Submission branch: `tdsc-hardening`
- Manuscript source: `paper/tdsc/sessionbound-tdsc.tex`
- Manuscript PDF: `paper/tdsc/sessionbound-tdsc.pdf`
- Build command: `cd paper/tdsc && make`
- Page count after compression pass: 14 pages

The Git tag is the stable artifact anchor. The `main` branch may continue to
evolve after submission work.

## Claim Contract

The TDSC manuscript is the canonical claim contract for this artifact. It states
the compressed paper argument and should be used instead of the earlier arXiv v1
text when checking current claims.

Core validated claims in the TDSC candidate:

- canonical functional validation: 24 / 24 scenarios passed;
- adversarial SQL suite: 28 cases, with 22 blocked cases, 5 allowed safe-view
  analytical cases, and 1 known limitation;
- default-seed overhead: full SessionBound p50 latency is 14.6--18.6 ms across
  representative query patterns;
- 100k synthetic scale sweep: the PL/pgSQL wrapper path reaches multi-second
  latency, motivating planner- or executor-hook enforcement for production;
- security guarantees are limited to the stated prototype SQL fragment and do
  not claim arbitrary semantic inference prevention or differential privacy.

## Reproducibility Map

- Canonical validation script: `scripts/sessionbound_agent_eval.py`
- TDSC hardening scripts: `paper/tdsc/scripts/`
- TDSC raw results: `paper/tdsc/raw_results/`
- TDSC experiment reports: `paper/tdsc/experiments/`
- Functional validation summary: `paper/tdsc/evaluation/FUNCTIONAL_VALIDATION.md`
- Adversarial SQL report: `paper/tdsc/ADVERSARIAL_SQL_SUITE.md`
- AST validation report: `paper/tdsc/AST_VALIDATION.md`
- PostgreSQL hook report: `paper/tdsc/POSTGRES_HOOK_ENFORCEMENT.md`
- Overhead breakdown report: `paper/tdsc/OVERHEAD_BREAKDOWN.md`
- Security invariants companion: `paper/tdsc/SECURITY_INVARIANTS.md`

## Relationship to arXiv v1

`paper/arxiv-v1/` is an earlier preprint workspace. It remains useful as a
public historical snapshot, but the TDSC candidate has a compressed structure,
updated security argument, updated hardening evidence, and a different page
budget. If an arXiv v2 is prepared, it should be generated from the TDSC
candidate after submission text stabilizes.
