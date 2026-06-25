# arXiv Readiness Checklist

- [x] Thesis matches SessionBound v5.4 direction.
- [x] SessionBound is framework; SessionBoundDB is runtime.
- [x] Public brand is SessionBound.
- [x] TaskBound only remains in historical/internal compatibility contexts.
- [x] arXiv title uses SessionBound.
- [x] README uses SessionBound.
- [x] No "position paper" language in final title/abstract.
- [x] No unsupported "first" claims.
- [x] Data Product MCP is cited and distinguished.
- [x] Oracle DDS is cited and distinguished.
- [x] PAuth is cited and distinguished.
- [x] Authenticated Delegation is cited and distinguished.
- [x] Zanzibar is cited and distinguished.
- [x] Functional validation results are real.
- [x] Canonical public evaluation script passes.
- [x] Performance benchmark results are real or gap is documented in final-submission form.
- [x] No unresolved draft tokens remain in final manuscript.
- [x] No unresolved action markers remain in final manuscript.
- [x] No repository URL placeholder remains.
- [x] Exact public GitHub repository URL is used.
- [x] Secret/sensitive-file check completed before release reporting.
- [x] references.bib contains only verified references.
- [x] BibTeX citations are integrated into `arxiv.tex`.
- [x] `arxiv.bbl` is generated for arXiv source upload.
- [x] No company confidential data found in manuscript review.
- [x] Demo data is described as synthetic.
- [x] PDF builds from source or build gap documented.
- [x] Recommended category documented: primary cs.DB, secondary cs.CR, optional cs.AI.
- [x] Discussion section added.
- [x] Paper manuscript no longer contains commit hashes or Git tag references.
- [x] Code availability points only to `https://github.com/SessionBound/sessionbound`.
- [x] Bibliography style changed to `unsrt`.
- [x] First in-text citation starts at `[1]`.
- [x] No unused references remain.
- [x] No missing BibTeX keys remain.
- [x] `arxiv.bbl` regenerated.
- [x] PDF rebuilt.
- [x] Upload ZIP regenerated.

## Current Recommendation

Ready for human PDF review.

Known review notes:

- Scope violations over safe views are enforced by transparent filtering rather than explicit denial.
- Payload aggregation functions are now conservatively blocked in the v1 prototype.
- PDF and BibTeX packaging completed and is documented in `paper/arxiv-v1/packaging/BUILD.md`.
