# SessionBound TDSC Draft

This directory contains the current TDSC-oriented manuscript, build files,
measured experiment artifacts, and hardening reports.

## Main Files

- `sessionbound-tdsc.tex`: main manuscript source.
- `references.bib`: bibliography.
- `IEEEtran.cls`: local IEEEtran class file from CTAN, included because
  the current machine does not provide it globally.
- `IEEEtran.bst`: local IEEEtran BibTeX style from CTAN.
- `SUBMISSION_STATUS.md`: remaining checks before any formal submission.
- `experiments/`: baseline experiment scripts, raw results, and reports.
- `raw_results/`: latest hardening raw JSON/CSV outputs.
- `scripts/`: latest hardening evaluation scripts.
- `POSTGRES_HOOK_ENFORCEMENT.md`: PostgreSQL hook prototype notes and
  evaluation summary.
- `SDK_QUERY_SURFACE.md`: agent-facing `query(sql)` SDK notes and evaluation
  summary.
- `FINAL_HARDENING_REPORT.md`: current hardening status and blockers.

## Build

```sh
make
```

The output PDF is `sessionbound-tdsc.pdf`.
