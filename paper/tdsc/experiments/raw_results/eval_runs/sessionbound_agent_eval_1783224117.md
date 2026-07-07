# SessionBound Evaluation

- Commit: `98a47eb`
- Base URL: `http://127.0.0.1:8000`
- Passed: 24 / 24
- Failed: 0 / 24

| Paper ID | Paper Scenario | Script Scenario | Category | Expected | Actual | Pass | Evidence |
|---|---|---|---|---:|---:|---:|---|
| V01 | Safe view SELECT | safe_view_select | allowed | Allowed | Allowed | yes | rows=3 |
| V02 | Join safe views | join_safe_views | allowed | Allowed | Allowed | yes | rows=3 |
| V03 | CTE | cte | allowed | Allowed | Allowed | yes | rows=1 |
| V04 | Department totals | group_by | allowed | Allowed | Allowed | yes | rows=24 |
| V05 | Ranked expenses | window_function | allowed | Allowed | Allowed | yes | rows=5 |
| V06 | Scoped drill-down | scoped_drill_down | allowed | Allowed | Allowed | yes | rows=112 |
| V07 | Salary access | salary_access | denied_field | Denied | Denied | yes | SessionBoundDB denied query: sensitive column is outside this task capability |
| V08 | Bank account access | bank_account_access | denied_field | Denied | Denied | yes | SessionBoundDB denied query: sensitive column is outside this task capability |
| V09 | Raw table access | raw_table_access | schema_escape | Denied | Denied | yes | SessionBoundDB denied query: direct access to internal schemas or state tables is not allowed |
| V10 | Mutation SQL | mutation_sql | write_attempt | Denied | Denied | yes | SessionBoundDB denied query: only SELECT statements are allowed |
| V11 | DDL | ddl | destructive_operation | Denied | Denied | yes | SessionBoundDB denied query: only SELECT statements are allowed |
| V12 | pg_catalog access | pg_catalog_access | schema_escape | Denied | Denied | yes | SessionBoundDB denied query: direct access to internal schemas or state tables is not allowed |
| V13 | json_agg(e) payload | json_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V14 | jsonb_agg(e) payload | jsonb_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V15 | array_agg(e.expense_id) payload | array_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V16 | string_agg(employee_name, ',') payload | string_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V17 | xmlagg(...) payload | xmlagg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V18 | row_to_json(e) payload | row_to_json_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V19 | json_build_object(...) payload | json_build_object_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V20 | jsonb_build_object(...) payload | jsonb_build_object_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| V21 | Other month | out_of_scope_month | transparent_scope_filtering | Filtered / 0 rows | Filtered / 0 rows | yes | Allowed query shape returned zero rows under task-bound safe-view scope. |
| V22 | Other department | out_of_scope_department | transparent_scope_filtering | Filtered / 0 rows | Filtered / 0 rows | yes | Allowed query shape returned zero rows under task-bound safe-view scope. |
| V23 | Query budget overflow | query_budget_overflow | budget | Denied | Denied | yes | SessionBoundDB denied query: query budget exhausted |
| V24 | Disclosure budget overflow | disclosure_budget_overflow | budget | Denied | Denied | yes | SessionBoundDB denied query: unique expense row budget exceeded |

## Interpretation

This is the canonical public evaluation for the current SessionBound arXiv v1 prototype. It checks allowed analytical SQL, denied boundary violations, transparent safe-view scope filtering, payload aggregation blocking, and budget enforcement.