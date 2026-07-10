CREATE OR REPLACE FUNCTION taskbound.audit_connection_name()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT 'sessionbound_audit_' || pg_backend_pid()::text
$$;

CREATE OR REPLACE FUNCTION taskbound.audit_exec(sql_text text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  v_conn text := taskbound.audit_connection_name();
  v_connections text[];
BEGIN
  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;
  PERFORM public.dblink_exec(v_conn, sql_text);
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.mutation_fence_ok(
  v_task_id text,
  v_binding_id uuid,
  v_fence_token bigint
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  ok boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM taskbound.active_sessions a
    WHERE a.task_id = v_task_id
      AND a.binding_id = v_binding_id
      AND a.fence_token = v_fence_token
  ) INTO ok;

  IF NOT ok THEN
    PERFORM taskbound.log_binding_event_local(
      'BINDING_FENCED',
      v_task_id,
      NULL,
      NULL,
      v_binding_id,
      v_fence_token,
      NULL,
      NULL,
      'budget or receipt mutation rejected by binding_id/fence_token mismatch'
    );
  END IF;

  RETURN ok;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.native_denied_receipt(
  v_task_id text,
  v_budget_account text,
  sql_text text,
  reason text,
  v_receipts_enabled boolean,
  v_binding_id uuid,
  v_fence_token bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
BEGIN
  IF NOT COALESCE(v_receipts_enabled, true) THEN
    RETURN;
  END IF;
  IF COALESCE(v_task_id, '') = '' THEN
    RETURN;
  END IF;

  PERFORM taskbound.audit_exec(format(
    'INSERT INTO taskbound.task_query_receipts ' ||
    '(task_id, budget_account, binding_id, fence_token, query_digest, decision, reason) ' ||
    'SELECT %L, %L, %L::uuid, %s, encode(public.digest(%L, ''sha256''), ''hex''), ''denied'', %L ' ||
    'WHERE taskbound.mutation_fence_ok(%L, %L::uuid, %s)',
    v_task_id,
    COALESCE(v_budget_account, v_task_id),
    v_binding_id,
    v_fence_token,
    COALESCE(sql_text, ''),
    reason,
    v_task_id,
    v_binding_id,
    v_fence_token
  ));
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.fail_receipt(sql_text text, reason text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  p jsonb;
  v_receipts_enabled boolean;
  active record;
  v_backend_start timestamptz;
  v_postmaster_start timestamptz;
BEGIN
  SELECT backend_start INTO v_backend_start
  FROM pg_catalog.pg_stat_activity
  WHERE pid = pg_backend_pid();
  SELECT pg_catalog.pg_postmaster_start_time() INTO v_postmaster_start;

  SELECT *
  INTO active
  FROM taskbound.active_sessions a
  WHERE a.owner_backend_pid = pg_backend_pid()
    AND a.owner_backend_start = v_backend_start
    AND a.owner_postmaster_start = v_postmaster_start
    AND a.database_oid = taskbound.current_database_oid()
    AND a.owner_session_user = session_user;

  IF NOT FOUND THEN
    RETURN;
  END IF;

  p := active.payload;
  v_receipts_enabled := COALESCE((p #>> ARRAY['runtime_options', 'receipts_enabled'])::boolean, true);
  IF NOT v_receipts_enabled THEN
    RETURN;
  END IF;

  INSERT INTO taskbound.task_query_receipts (
    task_id,
    budget_account,
    binding_id,
    fence_token,
    query_digest,
    decision,
    reason
  )
  SELECT
    active.task_id,
    COALESCE(p->>'budget_account', active.task_id),
    active.binding_id,
    active.fence_token,
    encode(public.digest(COALESCE(sql_text, ''), 'sha256'), 'hex'),
    'denied',
    COALESCE(reason, 'query denied')
  WHERE taskbound.mutation_fence_ok(active.task_id, active.binding_id, active.fence_token);
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.native_reserve_query(
  v_task_id text,
  v_budget_account text,
  sql_text text,
  v_max_queries int,
  v_budget_accounting_enabled boolean,
  v_receipts_enabled boolean,
  v_binding_id uuid,
  v_fence_token bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  v_conn text := taskbound.audit_connection_name();
  v_connections text[];
  v_status text;
BEGIN
  IF NOT COALESCE(v_budget_accounting_enabled, true) THEN
    RETURN;
  END IF;

  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  SELECT status INTO v_status
  FROM public.dblink(
    v_conn,
    format(
      $sql$
      WITH owner AS (
        SELECT taskbound.mutation_fence_ok(%L, %L::uuid, %s) AS ok
      ),
      updated AS (
        UPDATE taskbound.task_execution_state
        SET query_count = query_count + 1
        WHERE task_id = %L
          AND revoked = false
          AND query_count < %s
          AND (SELECT ok FROM owner)
        RETURNING task_id
      ),
      existing AS (
        SELECT query_count, revoked
        FROM taskbound.task_execution_state
        WHERE task_id = %L
      )
      SELECT CASE
        WHEN NOT (SELECT ok FROM owner) THEN 'BINDING_FENCED'
        WHEN EXISTS (SELECT 1 FROM updated) THEN 'ok'
        WHEN EXISTS (SELECT 1 FROM existing WHERE revoked) THEN 'task is revoked'
        WHEN EXISTS (SELECT 1 FROM existing WHERE query_count >= %s) THEN 'query budget exhausted'
        ELSE 'task execution state is missing'
      END AS status
      $sql$,
      v_task_id,
      v_binding_id,
      v_fence_token,
      v_task_id,
      GREATEST(COALESCE(v_max_queries, 0), 0),
      v_task_id,
      GREATEST(COALESCE(v_max_queries, 0), 0)
    )
  ) AS t(status text);

  IF v_status <> 'ok' THEN
    PERFORM taskbound.native_denied_receipt(
      v_task_id,
      v_budget_account,
      sql_text,
      v_status,
      v_receipts_enabled,
      v_binding_id,
      v_fence_token
    );
    RAISE EXCEPTION 'SessionBoundDB denied query: %', v_status;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.native_seen_expense_rows(v_budget_account text)
RETURNS TABLE(row_id text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  v_conn text := taskbound.audit_connection_name();
  v_connections text[];
BEGIN
  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  RETURN QUERY
  SELECT t.row_id
  FROM public.dblink(
    v_conn,
    format(
      'SELECT row_id FROM taskbound.task_rows_seen WHERE budget_account = %L AND row_kind = ''expense''',
      v_budget_account
    )
  ) AS t(row_id text);
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.native_finish_query(
  v_task_id text,
  v_budget_account text,
  sql_text text,
  v_rows_returned bigint,
  v_new_expense_ids text[],
  v_max_rows int,
  v_budget_accounting_enabled boolean,
  v_receipts_enabled boolean,
  v_binding_id uuid,
  v_fence_token bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  v_conn text := taskbound.audit_connection_name();
  v_connections text[];
  v_ids_expr text;
  v_unique_added bigint := 0;
  v_unique_after bigint;
  v_remaining_sql text := 'NULL';
  v_fence_ok boolean := true;
BEGIN
  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  IF COALESCE(v_budget_accounting_enabled, true) THEN
    IF COALESCE(cardinality(v_new_expense_ids), 0) = 0 THEN
      v_ids_expr := 'ARRAY[]::text[]';
    ELSE
      SELECT 'ARRAY[' || string_agg(format('%L', row_id), ',') || ']::text[]'
      INTO v_ids_expr
      FROM unnest(v_new_expense_ids) AS u(row_id);
    END IF;

    SELECT fence_ok, unique_added, unique_after
    INTO v_fence_ok, v_unique_added, v_unique_after
    FROM public.dblink(
      v_conn,
      format(
        $sql$
        WITH owner AS (
          SELECT taskbound.mutation_fence_ok(%L, %L::uuid, %s) AS ok
        ),
        before_count AS (
          SELECT count(*)::bigint AS n
          FROM taskbound.task_rows_seen
          WHERE budget_account = %L
            AND row_kind = 'expense'
            AND (SELECT ok FROM owner)
        ),
        input AS (
          SELECT DISTINCT row_id
          FROM unnest(%s) AS u(row_id)
          WHERE row_id IS NOT NULL AND row_id <> ''
        ),
        ins AS (
          INSERT INTO taskbound.task_rows_seen (budget_account, row_kind, row_id)
          SELECT %L, 'expense', row_id
          FROM input
          WHERE (SELECT ok FROM owner)
          ON CONFLICT DO NOTHING
          RETURNING 1
        ),
        after_count AS (
          SELECT (SELECT n FROM before_count) + (SELECT count(*)::bigint FROM ins) AS n
        ),
        updated AS (
          UPDATE taskbound.task_execution_state
          SET returned_rows = returned_rows + %s,
              unique_expense_rows = (SELECT n FROM after_count)
          WHERE task_id = %L
            AND (SELECT ok FROM owner)
          RETURNING 1
        )
        SELECT
          (SELECT ok FROM owner) AS fence_ok,
          (SELECT count(*)::bigint FROM ins) AS unique_added,
          (SELECT n FROM after_count) AS unique_after
        $sql$,
        v_task_id,
        v_binding_id,
        v_fence_token,
        v_budget_account,
        v_ids_expr,
        v_budget_account,
        GREATEST(COALESCE(v_rows_returned, 0), 0),
        v_task_id
      )
    ) AS t(fence_ok boolean, unique_added bigint, unique_after bigint);

    IF NOT COALESCE(v_fence_ok, false) THEN
      RAISE EXCEPTION 'SessionBoundDB denied query: BINDING_FENCED';
    END IF;

    IF v_unique_after > COALESCE(v_max_rows, 0) THEN
      PERFORM taskbound.native_denied_receipt(
        v_task_id,
        v_budget_account,
        sql_text,
        'unique expense row budget exceeded',
        v_receipts_enabled,
        v_binding_id,
        v_fence_token
      );
      RAISE EXCEPTION 'SessionBoundDB denied query: unique expense row budget exceeded';
    END IF;

    v_remaining_sql := (COALESCE(v_max_rows, 0) - COALESCE(v_unique_after, 0))::text;
  END IF;

  IF COALESCE(v_receipts_enabled, true) THEN
    PERFORM taskbound.audit_exec(format(
      'INSERT INTO taskbound.task_query_receipts ' ||
      '(task_id, budget_account, binding_id, fence_token, query_digest, decision, rows_returned, unique_rows_added, remaining_unique_row_budget) ' ||
      'SELECT %L, %L, %L::uuid, %s, encode(public.digest(%L, ''sha256''), ''hex''), ''allowed'', %s, %s, %s ' ||
      'WHERE taskbound.mutation_fence_ok(%L, %L::uuid, %s)',
      v_task_id,
      v_budget_account,
      v_binding_id,
      v_fence_token,
      COALESCE(sql_text, ''),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      GREATEST(COALESCE(v_unique_added, 0), 0),
      v_remaining_sql,
      v_task_id,
      v_binding_id,
      v_fence_token
    ));
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.native_partial_denied_receipt(
  v_task_id text,
  v_budget_account text,
  sql_text text,
  reason text,
  v_rows_returned bigint,
  v_new_expense_ids text[],
  v_max_rows int,
  v_budget_accounting_enabled boolean,
  v_receipts_enabled boolean,
  v_binding_id uuid,
  v_fence_token bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  v_conn text := taskbound.audit_connection_name();
  v_connections text[];
  v_ids_expr text;
  v_unique_added bigint := 0;
  v_unique_after bigint;
  v_remaining_sql text := 'NULL';
  v_fence_ok boolean := true;
BEGIN
  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  IF COALESCE(v_budget_accounting_enabled, true) THEN
    IF COALESCE(cardinality(v_new_expense_ids), 0) = 0 THEN
      v_ids_expr := 'ARRAY[]::text[]';
    ELSE
      SELECT 'ARRAY[' || string_agg(format('%L', row_id), ',') || ']::text[]'
      INTO v_ids_expr
      FROM unnest(v_new_expense_ids) AS u(row_id);
    END IF;

    SELECT fence_ok, unique_added, unique_after
    INTO v_fence_ok, v_unique_added, v_unique_after
    FROM public.dblink(
      v_conn,
      format(
        $sql$
        WITH owner AS (
          SELECT taskbound.mutation_fence_ok(%L, %L::uuid, %s) AS ok
        ),
        before_count AS (
          SELECT count(*)::bigint AS n
          FROM taskbound.task_rows_seen
          WHERE budget_account = %L
            AND row_kind = 'expense'
            AND (SELECT ok FROM owner)
        ),
        input AS (
          SELECT DISTINCT row_id
          FROM unnest(%s) AS u(row_id)
          WHERE row_id IS NOT NULL AND row_id <> ''
        ),
        ins AS (
          INSERT INTO taskbound.task_rows_seen (budget_account, row_kind, row_id)
          SELECT %L, 'expense', row_id
          FROM input
          WHERE (SELECT ok FROM owner)
          ON CONFLICT DO NOTHING
          RETURNING 1
        ),
        after_count AS (
          SELECT (SELECT n FROM before_count) + (SELECT count(*)::bigint FROM ins) AS n
        ),
        updated AS (
          UPDATE taskbound.task_execution_state
          SET returned_rows = returned_rows + %s,
              unique_expense_rows = (SELECT n FROM after_count)
          WHERE task_id = %L
            AND (SELECT ok FROM owner)
          RETURNING 1
        )
        SELECT
          (SELECT ok FROM owner) AS fence_ok,
          (SELECT count(*)::bigint FROM ins) AS unique_added,
          (SELECT n FROM after_count) AS unique_after
        $sql$,
        v_task_id,
        v_binding_id,
        v_fence_token,
        v_budget_account,
        v_ids_expr,
        v_budget_account,
        GREATEST(COALESCE(v_rows_returned, 0), 0),
        v_task_id
      )
    ) AS t(fence_ok boolean, unique_added bigint, unique_after bigint);

    IF NOT COALESCE(v_fence_ok, false) THEN
      RAISE EXCEPTION 'SessionBoundDB denied query: BINDING_FENCED';
    END IF;

    v_remaining_sql := (COALESCE(v_max_rows, 0) - COALESCE(v_unique_after, 0))::text;
  END IF;

  IF COALESCE(v_receipts_enabled, true) THEN
    PERFORM taskbound.audit_exec(format(
      'INSERT INTO taskbound.task_query_receipts ' ||
      '(task_id, budget_account, binding_id, fence_token, query_digest, decision, reason, rows_returned, unique_rows_added, remaining_unique_row_budget) ' ||
      'SELECT %L, %L, %L::uuid, %s, encode(public.digest(%L, ''sha256''), ''hex''), ''denied'', %L, %s, %s, %s ' ||
      'WHERE taskbound.mutation_fence_ok(%L, %L::uuid, %s)',
      v_task_id,
      v_budget_account,
      v_binding_id,
      v_fence_token,
      COALESCE(sql_text, ''),
      COALESCE(reason, 'query denied'),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      GREATEST(COALESCE(v_unique_added, 0), 0),
      v_remaining_sql,
      v_task_id,
      v_binding_id,
      v_fence_token
    ));
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.enforce_min_group_policy(
  sql_text text,
  rows jsonb[],
  v_min_group_size int
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  lowered text := lower(COALESCE(sql_text, ''));
  row_json jsonb;
  entity_count bigint;
  group_count bigint;
  has_aggregate boolean;
  has_group_by boolean;
  has_having boolean;
  has_where boolean;
  min_k int := GREATEST(COALESCE(v_min_group_size, 5), 1);
BEGIN
  has_aggregate := lowered ~ '\m(count|sum|avg|min|max)[[:space:]]*\(';
  has_group_by := lowered ~ '\mgroup[[:space:]]+by\M';
  has_having := lowered ~ '\mhaving\M';
  has_where := lowered ~ '\mwhere\M';

  IF NOT has_aggregate AND NOT has_group_by THEN
    RETURN;
  END IF;

  IF has_having THEN
    RAISE EXCEPTION 'minimum group-size policy denied HAVING predicates';
  END IF;

  IF lowered ~ '\mgroup[[:space:]]+by[[:space:][:alnum:]_.,"]*\m(employee_id|expense_id)\M' THEN
    RAISE EXCEPTION 'minimum group-size policy denied grouping by sensitive entity identifiers';
  END IF;

  IF NOT has_group_by THEN
    IF lowered ~ '\mwhere\M.*\m(employee_id|expense_id)\M' THEN
      RAISE EXCEPTION 'minimum group-size policy denied aggregate filtering on sensitive entity identifiers';
    END IF;

    SELECT count(DISTINCT employee_id)
    INTO group_count
    FROM taskbound.expenses;

    IF COALESCE(group_count, 0) < min_k THEN
      RAISE EXCEPTION 'minimum group-size policy denied aggregate over % distinct employee_id values; required %',
        COALESCE(group_count, 0), min_k;
    END IF;
    RETURN;
  END IF;

  FOREACH row_json IN ARRAY COALESCE(rows, ARRAY[]::jsonb[]) LOOP
    IF row_json ? 'employee_id' OR row_json ? 'expense_id' THEN
      RAISE EXCEPTION 'minimum group-size policy denied entity-identifying aggregate output';
    END IF;

    entity_count := NULL;
    BEGIN
      IF row_json ? 'employee_count' THEN
        entity_count := (row_json->>'employee_count')::numeric::bigint;
      ELSIF row_json ? 'entity_count' THEN
        entity_count := (row_json->>'entity_count')::numeric::bigint;
      ELSIF row_json ? 'distinct_employee_count' THEN
        entity_count := (row_json->>'distinct_employee_count')::numeric::bigint;
      ELSIF row_json ? 'employees_in_group' THEN
        entity_count := (row_json->>'employees_in_group')::numeric::bigint;
      ELSIF row_json ? 'distinct_employees' THEN
        entity_count := (row_json->>'distinct_employees')::numeric::bigint;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      entity_count := NULL;
    END;

    IF entity_count IS NOT NULL THEN
      IF entity_count < min_k THEN
        RAISE EXCEPTION 'minimum group-size policy denied output group with % distinct employee_id values; required %',
          entity_count, min_k;
      END IF;
      CONTINUE;
    END IF;

    IF has_where THEN
      RAISE EXCEPTION 'minimum group-size policy could not verify filtered GROUP BY cardinality';
    END IF;

    group_count := NULL;
    IF row_json ? 'department_id' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE department_id = row_json->>'department_id';
    ELSIF row_json ? 'department_name' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE department_name = row_json->>'department_name';
    ELSIF row_json ? 'category' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE category = row_json->>'category';
    ELSIF row_json ? 'merchant' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE merchant = row_json->>'merchant';
    ELSIF row_json ? 'city' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE city = row_json->>'city';
    ELSIF row_json ? 'status' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE status = row_json->>'status';
    ELSIF row_json ? 'employee_level' THEN
      SELECT count(DISTINCT employee_id) INTO group_count
      FROM taskbound.expenses
      WHERE employee_level = row_json->>'employee_level';
    ELSE
      RAISE EXCEPTION 'minimum group-size policy could not verify GROUP BY cardinality';
    END IF;

    IF COALESCE(group_count, 0) < min_k THEN
      RAISE EXCEPTION 'minimum group-size policy denied output group with % distinct employee_id values; required %',
        COALESCE(group_count, 0), min_k;
    END IF;
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.run(sql_text text)
RETURNS SETOF jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  p jsonb;
  lowered text;
  row_item record;
  row_json jsonb;
  rows jsonb[] := ARRAY[]::jsonb[];
  rows_returned bigint := 0;
  unique_before bigint;
  unique_after bigint;
  unique_added bigint;
  max_queries int;
  max_rows int;
  v_task_id text;
  v_budget_account text;
  v_receipts_enabled boolean;
  v_budget_accounting_enabled boolean;
  v_min_group_size int;
  receipt uuid;
  active record;
  v_collect_rows_for_policy boolean;
BEGIN
  p := taskbound.require_payload();
  v_task_id := p->>'task_id';
  v_budget_account := COALESCE(p->>'budget_account', v_task_id);
  v_receipts_enabled := COALESCE((p #>> ARRAY['runtime_options', 'receipts_enabled'])::boolean, true);
  v_budget_accounting_enabled := COALESCE((p #>> ARRAY['runtime_options', 'budget_accounting_enabled'])::boolean, true);
  v_min_group_size := COALESCE((p #>> ARRAY['aggregate_policy', 'min_group_size'])::int, 5);
  lowered := lower(sql_text);
  v_collect_rows_for_policy := lowered ~ '\mgroup[[:space:]]+by\M';

  SELECT *
  INTO active
  FROM taskbound.active_sessions a
  WHERE a.owner_backend_pid = pg_backend_pid()
    AND a.task_id = v_task_id;

  IF regexp_replace(sql_text, ';\s*$', '') ~ ';' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'multiple SQL statements are not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: multiple SQL statements are not allowed';
  END IF;

  IF NOT lowered ~ '^\s*(select|with)\s' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'only SELECT statements are allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: only SELECT statements are allowed';
  END IF;

  IF lowered ~ '\m(insert|update|delete|drop|alter|create|truncate|copy|call|grant|revoke)\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'mutating or administrative keyword is not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: mutating or administrative keyword is not allowed';
  END IF;

  IF lowered ~ '\m(app_data|pg_catalog|information_schema|signing_keys|active_sessions|binding_events|task_execution_state|task_rows_seen|safe_view_registry)\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'direct access to internal schemas or state tables is not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: direct access to internal schemas or state tables is not allowed';
  END IF;

  IF lowered ~ '\mtaskbound\s*\.' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'direct access to taskbound runtime functions or objects is not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: direct access to taskbound runtime functions or objects is not allowed';
  END IF;

  IF lowered ~ '\m(claim|current_payload|require_payload)\s*\(' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'direct access to taskbound runtime helper functions is not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: direct access to taskbound runtime helper functions is not allowed';
  END IF;

  IF lowered ~ '\m(bank_account|phone|salary)\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'sensitive column is outside this task capability');
    RAISE EXCEPTION 'SessionBoundDB denied query: sensitive column is outside this task capability';
  END IF;

  IF lowered ~ '\m(json_agg|jsonb_agg|array_agg|string_agg|xmlagg|row_to_json|json_build_object|jsonb_build_object)\s*\(' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'payload aggregation function is not allowed for this task');
    RAISE EXCEPTION 'SessionBoundDB denied query: payload aggregation function is not allowed for this task.';
  END IF;

  BEGIN
    PERFORM taskbound.enforce_min_group_policy(sql_text, ARRAY[]::jsonb[], v_min_group_size);
  EXCEPTION WHEN OTHERS THEN
    PERFORM taskbound.fail_receipt(sql_text, SQLERRM);
    RAISE EXCEPTION 'SessionBoundDB denied query: %', SQLERRM;
  END;

  SELECT revoked INTO STRICT row_item
  FROM taskbound.task_execution_state
  WHERE task_execution_state.task_id = v_task_id;

  IF row_item.revoked THEN
    PERFORM taskbound.fail_receipt(sql_text, 'task is revoked');
    RAISE EXCEPTION 'SessionBoundDB denied query: task is revoked';
  END IF;

  max_queries := COALESCE((p #>> ARRAY['budgets', 'max_queries'])::int, 100);
  max_rows := COALESCE((p #>> ARRAY['budgets', 'max_unique_expense_rows'])::int, 1000000);

  IF v_budget_accounting_enabled THEN
    IF (SELECT query_count FROM taskbound.task_execution_state WHERE task_execution_state.task_id = v_task_id) >= max_queries THEN
      PERFORM taskbound.fail_receipt(sql_text, 'query budget exhausted');
      RAISE EXCEPTION 'SessionBoundDB denied query: query budget exhausted';
    END IF;
  END IF;

  IF v_budget_accounting_enabled THEN
    SELECT count(*) INTO unique_before
    FROM taskbound.task_rows_seen
    WHERE task_rows_seen.budget_account = v_budget_account
      AND row_kind = 'expense';
  ELSE
    unique_before := 0;
  END IF;

  BEGIN
    PERFORM public.sessionbound_guard_check(sql_text);

    FOR row_item IN EXECUTE sql_text LOOP
      row_json := to_jsonb(row_item);
      IF v_collect_rows_for_policy THEN
        rows := array_append(rows, row_json);
      END IF;
      rows_returned := rows_returned + 1;

      IF v_budget_accounting_enabled AND row_json ? 'expense_id' THEN
        INSERT INTO taskbound.task_rows_seen (budget_account, row_kind, row_id)
        VALUES (v_budget_account, 'expense', row_json->>'expense_id')
        ON CONFLICT DO NOTHING;
      END IF;

      RETURN NEXT row_json;
    END LOOP;
  EXCEPTION WHEN OTHERS THEN
    PERFORM taskbound.fail_receipt(sql_text, SQLERRM);
    RAISE EXCEPTION 'SessionBoundDB denied query: %', SQLERRM;
  END;

  BEGIN
    PERFORM taskbound.enforce_min_group_policy(sql_text, rows, v_min_group_size);
  EXCEPTION WHEN OTHERS THEN
    PERFORM taskbound.fail_receipt(sql_text, SQLERRM);
    RAISE EXCEPTION 'SessionBoundDB denied query: %', SQLERRM;
  END;

  IF v_budget_accounting_enabled THEN
    SELECT count(*) INTO unique_after
    FROM taskbound.task_rows_seen
    WHERE task_rows_seen.budget_account = v_budget_account
      AND row_kind = 'expense';
  ELSE
    unique_after := 0;
  END IF;

  unique_added := unique_after - unique_before;

  IF v_budget_accounting_enabled THEN
    IF unique_after > max_rows THEN
      PERFORM taskbound.fail_receipt(sql_text, 'unique expense row budget exceeded');
      RAISE EXCEPTION 'SessionBoundDB denied query: unique expense row budget exceeded';
    END IF;
  END IF;

  IF v_budget_accounting_enabled THEN
    UPDATE taskbound.task_execution_state
    SET query_count = query_count + 1,
        returned_rows = returned_rows + rows_returned,
        unique_expense_rows = unique_after
    WHERE task_execution_state.task_id = v_task_id
      AND taskbound.mutation_fence_ok(v_task_id, active.binding_id, active.fence_token);
  END IF;

  IF v_receipts_enabled THEN
    INSERT INTO taskbound.task_query_receipts (
      task_id, budget_account, binding_id, fence_token, query_digest, decision, rows_returned,
      unique_rows_added, remaining_unique_row_budget
    )
    VALUES (
      v_task_id,
      v_budget_account,
      active.binding_id,
      active.fence_token,
      encode(public.digest(sql_text, 'sha256'), 'hex'),
      'allowed',
      rows_returned,
      unique_added,
      CASE WHEN v_budget_accounting_enabled THEN max_rows - unique_after ELSE NULL END
    )
    RETURNING receipt_id INTO receipt;
  END IF;
  RETURN;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.inspect_task_state()
RETURNS TABLE (
  task_id text,
  budget_account text,
  query_count int,
  returned_rows bigint,
  unique_expense_rows bigint,
  revoked boolean
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
  SELECT s.task_id, s.budget_account, s.query_count, s.returned_rows,
         s.unique_expense_rows, s.revoked
  FROM taskbound.task_execution_state s
  JOIN taskbound.active_sessions a ON a.task_id = s.task_id
  WHERE a.owner_backend_pid = pg_backend_pid()
    AND a.owner_backend_start = (
      SELECT backend_start FROM pg_catalog.pg_stat_activity WHERE pid = pg_backend_pid()
    )
    AND a.owner_postmaster_start = pg_catalog.pg_postmaster_start_time()
    AND a.database_oid = taskbound.current_database_oid()
$$;

CREATE OR REPLACE FUNCTION taskbound.receipts()
RETURNS SETOF taskbound.task_query_receipts
LANGUAGE sql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
  SELECT r.*
  FROM taskbound.task_query_receipts r
  JOIN taskbound.active_sessions a
    ON a.task_id = r.task_id
  WHERE a.owner_backend_pid = pg_backend_pid()
    AND a.owner_backend_start = (
      SELECT backend_start FROM pg_catalog.pg_stat_activity WHERE pid = pg_backend_pid()
    )
    AND a.owner_postmaster_start = pg_catalog.pg_postmaster_start_time()
    AND a.database_oid = taskbound.current_database_oid()
  ORDER BY r.created_at DESC
$$;
