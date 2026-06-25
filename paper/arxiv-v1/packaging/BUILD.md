# Build Notes

LaTeX source, BibTeX references, the human-review PDF, and the arXiv source
upload ZIP were generated successfully in this environment.

Initial citation inspection:

```bash
cd paper/arxiv-v1/manuscript
grep -nE "\\cite|\\bibliography|thebibliography|CSLReferences|printbibliography" arxiv.tex || true
grep -n "^@" references.bib
```

Finding: `references.bib` existed, but the LaTeX source initially had no
inline `\cite{...}` commands and no `\bibliography{references}` block.

BibTeX keys used:

```text
rfc6749
south2025authenticateddelegation
sharma2026pauth
tonnarelli2026dataproductmcp
oracle2026deepdatasecuritydocs
oracle2026deepdatasecuritybrief
pang2019zanzibar
postgresql2026rowsecurity
mcp2025specification
dwork2014algorithmic
adam1989securitycontrol
```

Generated source:

```text
paper/arxiv-v1/manuscript/arxiv.tex
```

Generation command:

```bash
pandoc paper/arxiv-v1/manuscript/taskbound-arxiv-v1.md \
  -s \
  -o paper/arxiv-v1/manuscript/arxiv.tex \
  --bibliography=paper/arxiv-v1/manuscript/references.bib \
  --metadata title='SessionBound: Turning Enterprise Task Approval into Budgeted Database Sessions' \
  --metadata author='Minmin Wu'
```

The generated LaTeX author block was set to:

```latex
\author{Minmin Wu\\
Independent Researcher}
```

LaTeX tooling installed:

```bash
sudo apt update
sudo apt install -y texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra latexmk
```

PDF and bibliography build command:

```bash
cd paper/arxiv-v1/manuscript
rm -f arxiv.aux arxiv.bbl arxiv.blg arxiv.log arxiv.out arxiv.pdf arxiv.fls arxiv.fdb_latexmk
pdflatex arxiv.tex
bibtex arxiv
pdflatex arxiv.tex
pdflatex arxiv.tex
```

Result:

```text
arxiv.bbl generated.
Output written on arxiv.pdf (29 pages, 307723 bytes).
```

Generated PDF:

```text
paper/arxiv-v1/manuscript/arxiv.pdf
```

Generated BibTeX output:

```text
paper/arxiv-v1/manuscript/arxiv.bbl
```

Generated upload ZIP:

```text
paper/arxiv-v1/manuscript/sessionbound-arxiv-v1.zip
```

ZIP contents:

```text
arxiv.tex
references.bib
arxiv.bbl
```

Build log review:

- No fatal LaTeX errors.
- No undefined reference or undefined citation errors were found.
- No missing bibliography database errors were found.
- BibTeX integrated: yes.
- Bibliography style: `unsrt`.
- First in-text citation starts at `[1]`.
- No unused BibTeX references remain.
- No cited keys are missing from `references.bib`.
- arxiv.bbl generated: yes.
- PDF rebuilt: yes.
- Discussion section added.
- Paper manuscript no longer contains commit hashes or Git tag references.
- Code availability points only to `https://github.com/SessionBound/sessionbound`.
- Upload ZIP regenerated.
- The Section 7.3 payload-aggregation table, Section 8 budget-semantics paragraph, and performance table overflow issues were fixed.
- Remaining warnings are minor overfull `hbox` warnings in long verbatim/table lines outside the fixed layout issues; they do not block human PDF review.

Current status: ready for human PDF review and arXiv source upload.
