# SessionBound arXiv v1 Packaging Workspace

This workspace packages the human-reviewed v5.4 SessionBound manuscript for the
original arXiv v1 review pass. It is now an earlier public preprint snapshot,
not the current TDSC artifact-backed claim contract.

For the current TDSC-oriented candidate, use:

- `paper/tdsc/sessionbound-tdsc.tex`
- `paper/tdsc/sessionbound-tdsc.pdf`
- `paper/tdsc/ARTIFACT_MANIFEST.md`
- Git tag `tdsc-submission-2026-07-07`

If an arXiv v2 is prepared, it should be generated from the TDSC candidate after
the submission text stabilizes.

Primary source:

- `manuscript/taskbound-arxiv-v1.md`

Supporting material:

- `references/` records verified related-work sources and citation notes.
- `evaluation/` records functional validation status.
- `benchmarks/` records performance benchmark status and scripts.
- `audits/` records claims, placeholder, and readiness audits.
- `packaging/` records environment and final packaging status.

Current readiness status: archived as arXiv v1 / earlier preprint workspace.

Validation status:

- Canonical SessionBound evaluation passes 24 / 24 scenarios.
- Performance benchmark results are recorded in `benchmarks/PERFORMANCE_BENCHMARK.md`.
- Public brand is SessionBound; the PostgreSQL prototype keeps the `taskbound` SQL schema name for compatibility with the existing demo implementation.
- PDF generation completed successfully at `manuscript/arxiv.pdf`.
