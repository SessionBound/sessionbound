# TaskBound Name Audit

Raw grep output:

- `paper/arxiv-v1/audits/taskbound_name_audit.txt`

Command:

```bash
grep -RIn "TaskBound" . \
  --exclude=taskbound_name_audit.txt \
  --exclude=TASKBOUND_NAME_AUDIT.md \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=node_modules \
  --exclude-dir=__pycache__ \
  > paper/arxiv-v1/audits/taskbound_name_audit.txt || true
```

Current public arXiv v1 sources use `SessionBound` as the public framework name and `SessionBoundDB` as the PostgreSQL runtime prototype name.

| File | Line | Text | Keep / Replace | Reason |
|---|---:|---|---|---|
| Removed root `paper/` legacy notes and figures | n/a | Older positioning, related-work, figure, and one-sentence drafts used `TaskBound`. | Removed | Current public paper materials now live under `paper/arxiv-v1/`. |
| `paper/arxiv-v1/audits/ARXIV_READINESS_CHECKLIST.md`, `paper/arxiv-v1/audits/PLACEHOLDER_AUDIT.md`, `paper/arxiv-v1/packaging/FINAL_PACKAGING_REPORT.md` | one each | Audit/report text explains old naming cleanup and compatibility contexts. | Keep | These are explanatory audit records, not public brand usage in the manuscript/runtime. |
| Current public files: `README.md`, `docs/`, `paper/arxiv-v1/`, `app/`, `db/`, `scripts/` | n/a | No unclassified `TaskBound` / `TaskBoundDB` public-brand hits outside audit/report explanations. | Replace complete | Verified with `rg` excluding raw/classified name-audit files and targeted old-brand phrases. |

Compatibility note:

- The SQL schema and function identifiers remain lowercase `taskbound`, including `taskbound.run`, `taskbound.bind_task`, `taskbound.command`, `taskbound.inspect_task_state`, and `taskbound.receipts`.
- This is intentional compatibility for the current PostgreSQL demo implementation, not a public brand use.
