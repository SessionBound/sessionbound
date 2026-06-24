# SessionBound Evaluation

- Commit: `cadd555`
- Base URL: `http://localhost:8000`
- Passed: 24 / 24
- Failed: 0 / 24

| Scenario | Category | Expected | Actual | Pass | Evidence |
|---|---|---:|---:|---:|---|
| safe_view_select | allowed | Allowed | Allowed | yes | rows=3 |
| join_safe_views | allowed | Allowed | Allowed | yes | rows=3 |
| cte | allowed | Allowed | Allowed | yes | rows=1 |
| group_by | allowed | Allowed | Allowed | yes | rows=3 |
| window_function | allowed | Allowed | Allowed | yes | rows=5 |
| scoped_drill_down | allowed | Allowed | Allowed | yes | rows=3 |
| salary_access | denied_field | Denied | Denied | yes | SessionBoundDB denied query: sensitive column is outside this task capability |
| bank_account_access | denied_field | Denied | Denied | yes | SessionBoundDB denied query: sensitive column is outside this task capability |
| raw_table_access | schema_escape | Denied | Denied | yes | SessionBoundDB denied query: direct access to internal schemas or state tables is not allowed |
| mutation_sql | write_attempt | Denied | Denied | yes | SessionBoundDB denied query: only SELECT statements are allowed |
| ddl | destructive_operation | Denied | Denied | yes | SessionBoundDB denied query: only SELECT statements are allowed |
| pg_catalog_access | schema_escape | Denied | Denied | yes | SessionBoundDB denied query: direct access to internal schemas or state tables is not allowed |
| json_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| jsonb_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| array_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| string_agg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| xmlagg_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| row_to_json_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| json_build_object_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| jsonb_build_object_payload | payload_aggregation | Denied | Denied | yes | SessionBoundDB denied query: payload aggregation function is not allowed for this task. |
| out_of_scope_month | transparent_scope_filtering | Filtered / 0 rows | Filtered / 0 rows | yes | Allowed query shape returned zero rows under task-bound safe-view scope. |
| out_of_scope_department | transparent_scope_filtering | Filtered / 0 rows | Filtered / 0 rows | yes | Allowed query shape returned zero rows under task-bound safe-view scope. |
| query_budget_overflow | budget | Denied | Denied | yes | SessionBoundDB denied query: query budget exhausted |
| disclosure_budget_overflow | budget | Denied | Denied | yes | SessionBoundDB denied query: unique expense row budget exceeded |

## Interpretation

This is the canonical public evaluation for the current SessionBound arXiv v1 prototype. It checks allowed analytical SQL, denied boundary violations, transparent safe-view scope filtering, payload aggregation blocking, and budget enforcement.