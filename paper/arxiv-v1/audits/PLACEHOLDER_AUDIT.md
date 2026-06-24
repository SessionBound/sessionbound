# Placeholder Audit

Raw grep output:

- `paper/arxiv-v1/audits/placeholder_grep_raw.txt`

| File | Line | Placeholder | Action |
|---|---:|---|---|
| `app/api.py` | multiple | `placeholder` strings | Product UI/prompt handling for table placeholders and placeholder-value detection; not manuscript placeholder text. Keep. |
| `app/user_ui.py` | 406 | HTML `placeholder` attribute | UI input hint, not paper placeholder. Keep. |
| Removed root `paper/` historical files | n/a | historical placeholder/readiness language | Legacy paper notes were removed; current paper materials live under `paper/arxiv-v1/`. |
| `paper/arxiv-v1/README.md` | 14 | "placeholder" | Not a placeholder; describes the audit folder. Keep. |
| `paper/arxiv-v1/packaging/FINAL_PACKAGING_REPORT.md` | multiple | documents placeholder removal | Not a live manuscript placeholder; documents that it was removed. Keep. |

## GitHub URL Rule

The exact public SessionBound repository URL is known: `https://github.com/SessionBound/sessionbound`. The final manuscript copy does not include the old repository URL placeholder.

Replacement text used:

```text
The prototype source code and synthetic evaluation dataset are available at https://github.com/SessionBound/sessionbound.
```

Status: final manuscript copy is free of repository URL placeholders and unresolved draft/action markers.

## Brand Name Note

The public brand audit is recorded separately in `paper/arxiv-v1/audits/TASKBOUND_NAME_AUDIT.md`. Current public arXiv v1 sources use `SessionBound`; remaining `TaskBound` hits are explanatory audit text or internal compatibility identifiers.
