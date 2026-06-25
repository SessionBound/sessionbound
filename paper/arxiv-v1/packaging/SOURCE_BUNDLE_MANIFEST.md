# Source Bundle Manifest

Files needed for arXiv source upload:

- `paper/arxiv-v1/manuscript/arxiv.tex`
- `paper/arxiv-v1/manuscript/references.bib`
- `paper/arxiv-v1/manuscript/arxiv.bbl`

No external figure files required.

The PDF was generated locally for human review. The upload ZIP is:

- `paper/arxiv-v1/manuscript/sessionbound-arxiv-v1.zip`

ZIP contents:

- `arxiv.tex`
- `references.bib`
- `arxiv.bbl`

The ZIP intentionally excludes generated local build files such as `arxiv.pdf`,
`arxiv.aux`, `arxiv.log`, `arxiv.out`, `arxiv.blg`, `arxiv.fls`, and
`arxiv.fdb_latexmk`.

Final manuscript packaging status:

- Discussion section added.
- Paper manuscript no longer contains commit hashes or Git tag references.
- Code availability points only to `https://github.com/SessionBound/sessionbound`.
- Bibliography style changed to `unsrt`.
- First in-text citation starts at `[1]`.
- No unused references remain.
- No missing BibTeX keys remain.
- `arxiv.bbl` regenerated.
- PDF rebuilt.
- Upload ZIP regenerated.
- Formal architecture figure added.
- Major tables now have captions and labels.
