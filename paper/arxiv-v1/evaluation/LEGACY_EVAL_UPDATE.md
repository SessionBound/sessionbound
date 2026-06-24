# Legacy Evaluation Update

The previous `scripts/sessionbound_agent_eval.py` returned 8 / 11 because three scenarios encoded older prototype semantics. The script has been updated in place so the public command now reflects the current SessionBound arXiv v1 runtime.

| Legacy Scenario | Old Expectation | Current Behavior | Required Update |
| --------------- | --------------- | ---------------- | --------------- |
| `safe_view_workflow_hints` | Query `can_manager_approve`, `requires_finance_review`, and `can_pay`; expect Allowed. | Current safe view uses `can_department_approve` and related workflow fields. `can_manager_approve` is not a current column. | Replace stale workflow-hint scenario with current safe-view analytical and security scenarios. |
| `small_approval_allowed` | Command `approve_expense` should be allowed. | Current task token does not expose `approve_expense`; current controlled commands include `department_approve`, `finance_approve`, `c_level_approve`, and `pay_expense`. | Remove stale command-name scenario from canonical public SQL validation. |
| `payment_ledger_allowed` | Alice can run `pay_expense`. | Current runtime allows payment only for the finance user `user:fiona`. | Remove role-mismatched command scenario from canonical public SQL validation. |

## Current Canonical Evaluation

The canonical script remains:

```bash
python scripts/sessionbound_agent_eval.py \
  --base-url http://localhost:8000 \
  --output-dir paper/arxiv-v1/evaluation/eval_runs
```

It now covers:

- allowed safe-view analysis;
- joins, CTEs, grouping, window functions, and scoped drill-down;
- denied sensitive fields;
- denied raw schema and catalog access;
- denied DML and DDL;
- denied payload aggregation functions;
- transparent scope filtering with zero rows;
- query budget overflow;
- unique-row disclosure budget overflow.

