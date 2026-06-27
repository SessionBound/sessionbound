# SessionBound Short Paper

This directory contains the shortened SessionBound paper version.

Files:

- `sessionbound-short.tex`: short paper source.
- `references.bib`: BibTeX entries reused from the verified arXiv manuscript bibliography.
- `sessionbound-short.pdf`: locally built human-review PDF.
- `sessionbound-short.zip`: source upload bundle containing the TeX source, BibTeX file, and generated `.bbl`.

Build:

```sh
latexmk -pdf sessionbound-short.tex
```

Manual build:

```sh
pdflatex sessionbound-short.tex
bibtex sessionbound-short
pdflatex sessionbound-short.tex
pdflatex sessionbound-short.tex
```

Author block:

```text
Minmin Wu
Independent Researcher
```
