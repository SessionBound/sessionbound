# SessionBound arXiv v1 Packaging Workspace

This workspace packages the human-reviewed v5.4 SessionBound manuscript for an honest arXiv v1 review pass.

Primary source:

- `manuscript/taskbound-arxiv-v1.md`

Supporting material:

- `references/` records verified related-work sources and citation notes.
- `evaluation/` records functional validation status.
- `benchmarks/` records performance benchmark status and scripts.
- `audits/` records claims, placeholder, and readiness audits.
- `packaging/` records environment and final packaging status.

Current readiness status: ready for human review.

Validation status:

- Canonical SessionBound evaluation passes 24 / 24 scenarios.
- Performance benchmark results are recorded in `benchmarks/PERFORMANCE_BENCHMARK.md`.
- Public brand is SessionBound; the PostgreSQL prototype keeps the `taskbound` SQL schema name for compatibility with the existing demo implementation.
- PDF generation completed successfully at `manuscript/arxiv.pdf`.
