# SessionBound Evaluation

- Commit: `25e2373`
- Base URL: `http://localhost:8000`
- Passed: 24 / 24
- Failed: 0 / 24

| Paper ID | Paper Scenario | Script Scenario | Category | Expected | Actual | Pass | Evidence |
|---|---|---|---|---:|---:|---:|---|
| V01 | Safe view SELECT | safe_view_select | allowed | Allowed | Allowed | yes | rows=3 |
| V02 | Join safe views | join_safe_views | allowed | Allowed | Allowed | yes | rows=3 |
| V03 | CTE | cte | allowed | Allowed | Allowed | yes | rows=1 |
| V04 | Department totals | group_by | allowed | Allowed | Allowed | yes | rows=1 |
| V05 | Ranked expenses | window_function | allowed | Allowed | Allowed | yes | rows=5 |
| V06 | Scoped drill-down | scoped_drill_down | allowed | Allowed | Allowed | yes | rows=112 |
| V07 | Salary access | salary_access | denied_field | Denied | Denied | yes | AST preflight denied query: denied column salary is outside this task capability |
| V08 | Bank account access | bank_account_access | denied_field | Denied | Denied | yes | AST preflight denied query: denied column bank_account is outside this task capability |
| V09 | Raw table access | raw_table_access | schema_escape | Denied | Denied | yes | AST preflight denied query: direct access to schema app_data is not allowed; schema-qualified relation app_data.expenses is outside the safe-view registry |
| V10 | Mutation SQL | mutation_sql | write_attempt | Denied | Denied | yes | AST preflight denied query: statement type Delete is not allowed |
| V11 | DDL | ddl | destructive_operation | Denied | Denied | yes | AST preflight denied query: statement type Drop is not allowed |
| V12 | pg_catalog access | pg_catalog_access | schema_escape | Denied | Denied | yes | AST preflight denied query: catalog relation pg_tables is not allowed; direct access to schema pg_catalog is not allowed; relation pg_tables is not in the approved safe-view registry; schema-qualified relation pg_catalog.pg_tables is outside the safe-view registry |
| V13 | json_agg(e) payload | json_agg_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function j_s_o_n_array_agg is not allowed; payload aggregation function json_agg is not allowed |
| V14 | jsonb_agg(e) payload | jsonb_agg_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function jsonb_agg is not allowed |
| V15 | array_agg(e.expense_id) payload | array_agg_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function array_agg is not allowed |
| V16 | string_agg(employee_name, ',') payload | string_agg_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function group_concat is not allowed; payload aggregation function string_agg is not allowed |
| V17 | xmlagg(...) payload | xmlagg_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function xmlagg is not allowed; unknown function xmlelement is not allowed |
| V18 | row_to_json(e) payload | row_to_json_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function row_to_json is not allowed |
| V19 | json_build_object(...) payload | json_build_object_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function json_build_object is not allowed |
| V20 | jsonb_build_object(...) payload | jsonb_build_object_payload | payload_aggregation | Denied | Denied | yes | AST preflight denied query: payload aggregation function jsonb_build_object is not allowed |
| V21 | Other month | out_of_scope_month | transparent_scope_filtering | Filtered / 0 rows | Filtered / 0 rows | yes | Allowed query shape returned zero rows under task-bound safe-view scope. |
| V22 | Other department | out_of_scope_department | transparent_scope_filtering | Filtered / 0 rows | Filtered / 0 rows | yes | Allowed query shape returned zero rows under task-bound safe-view scope. |
| V23 | Query budget overflow | query_budget_overflow | budget | Denied | Denied | yes | SessionBoundDB denied query: query budget exhausted |
| V24 | Disclosure budget overflow | disclosure_budget_overflow | budget | Denied | Denied | yes | SessionBoundDB denied query: result tuple budget exceeded |

## Interpretation

This is the canonical public evaluation for the current SessionBound arXiv v1 prototype. It checks allowed analytical SQL, denied boundary violations, transparent safe-view scope filtering, payload aggregation blocking, and budget enforcement.