# SessionBound TDSC v2 Draft

This directory contains the second TDSC-oriented manuscript draft.
Compared with `paper/tdsc-v1/`, this version removes internal planning
language from the manuscript body and uses an IEEE journal-style LaTeX
entrypoint.

## Main Files

- `sessionbound-tdsc.tex`: main manuscript source.
- `references.bib`: bibliography.
- `IEEEtran.cls`: local IEEEtran class file from CTAN, included because
  the current machine does not provide it globally.
- `IEEEtran.bst`: local IEEEtran BibTeX style from CTAN.
- `SUBMISSION_STATUS.md`: remaining checks before any formal submission.

## Build

```sh
make
```

The output PDF is `sessionbound-tdsc.pdf`.

