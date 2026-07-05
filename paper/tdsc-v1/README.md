# SessionBound TDSC Journal Draft

This directory is an independent journal-preparation tree for targeting
IEEE Transactions on Dependable and Secure Computing (TDSC). It is
derived from `paper/arxiv-v1/` and should not overwrite the public arXiv
source.

## Files

- `manuscript/tdsc.tex`: journal-oriented draft derived from the long
  arXiv manuscript.
- `manuscript/references.bib`: copied bibliography for this draft.
- `TDSC_PREPARATION.md`: submission positioning, required checks, and
  remaining expansion work.
- `evaluation/FUNCTIONAL_VALIDATION.md`: copied 24-scenario validation
  evidence from the arXiv tree.
- `benchmarks/PERFORMANCE_BENCHMARK.md`: copied microbenchmark evidence
  from the arXiv tree.

## Build

From this directory:

```sh
make
```

The current local TeX installation does not provide `IEEEtran.cls`, so
`tdsc.tex` stays in a compact two-column `article` layout for now. Before
formal submission, move the body into the current IEEE/TDSC author
template and re-run the checklist in `TDSC_PREPARATION.md`.

