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

CREATE OR REPLACE FUNCTION taskbound.native_denied_receipt(
  v_task_id text,
  v_budget_account text,
  sql_text text,
  reason text,
  v_receipts_enabled boolean
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
    '(task_id, budget_account, query_digest, decision, reason) ' ||
    'VALUES (%L, %L, encode(public.digest(%L, ''sha256''), ''hex''), ''denied'', %L)',
    v_task_id,
    COALESCE(v_budget_account, v_task_id),
    COALESCE(sql_text, ''),
    reason
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
BEGIN
  p := taskbound.current_payload();
  v_receipts_enabled := COALESCE((p #>> ARRAY['runtime_options', 'receipts_enabled'])::boolean, true);
  IF p IS NOT NULL THEN
    PERFORM taskbound.native_denied_receipt(
      p->>'task_id',
      COALESCE(p->>'budget_account', p->>'task_id'),
      sql_text,
      reason,
      v_receipts_enabled
    );
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.native_reserve_query(
  v_task_id text,
  v_budget_account text,
  sql_text text,
  v_max_queries int,
  v_budget_accounting_enabled boolean,
  v_receipts_enabled boolean
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
      WITH updated AS (
        UPDATE taskbound.task_execution_state
        SET query_count = query_count + 1
        WHERE task_id = %L
          AND revoked = false
          AND query_count < %s
        RETURNING task_id
      ),
      existing AS (
        SELECT query_count, revoked
        FROM taskbound.task_execution_state
        WHERE task_id = %L
      )
      SELECT CASE
        WHEN EXISTS (SELECT 1 FROM updated) THEN 'ok'
        WHEN EXISTS (SELECT 1 FROM existing WHERE revoked) THEN 'task is revoked'
        WHEN EXISTS (SELECT 1 FROM existing WHERE query_count >= %s) THEN 'query budget exhausted'
        ELSE 'task execution state is missing'
      END AS status
      $sql$,
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
      v_receipts_enabled
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
  v_receipts_enabled boolean
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

    SELECT unique_added, unique_after
    INTO v_unique_added, v_unique_after
    FROM public.dblink(
      v_conn,
      format(
        $sql$
        WITH before_count AS (
          SELECT count(*)::bigint AS n
          FROM taskbound.task_rows_seen
          WHERE budget_account = %L
            AND row_kind = 'expense'
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
          RETURNING 1
        )
        SELECT
          (SELECT count(*)::bigint FROM ins) AS unique_added,
          (SELECT n FROM after_count) AS unique_after
        $sql$,
        v_budget_account,
        v_ids_expr,
        v_budget_account,
        GREATEST(COALESCE(v_rows_returned, 0), 0),
        v_task_id
      )
    ) AS t(unique_added bigint, unique_after bigint);

    IF v_unique_after > COALESCE(v_max_rows, 0) THEN
      PERFORM taskbound.native_denied_receipt(
        v_task_id,
        v_budget_account,
        sql_text,
        'unique expense row budget exceeded',
        v_receipts_enabled
      );
      RAISE EXCEPTION 'SessionBoundDB denied query: unique expense row budget exceeded';
    END IF;

    v_remaining_sql := (COALESCE(v_max_rows, 0) - COALESCE(v_unique_after, 0))::text;
  END IF;

  IF COALESCE(v_receipts_enabled, true) THEN
    PERFORM taskbound.audit_exec(format(
      'INSERT INTO taskbound.task_query_receipts ' ||
      '(task_id, budget_account, query_digest, decision, rows_returned, unique_rows_added, remaining_unique_row_budget) ' ||
      'VALUES (%L, %L, encode(public.digest(%L, ''sha256''), ''hex''), ''allowed'', %s, %s, %s)',
      v_task_id,
      v_budget_account,
      COALESCE(sql_text, ''),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      GREATEST(COALESCE(v_unique_added, 0), 0),
      v_remaining_sql
    ));
  END IF;
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
  receipt uuid;
BEGIN
  p := taskbound.require_payload();
  v_task_id := p->>'task_id';
  v_budget_account := COALESCE(p->>'budget_account', v_task_id);
  v_receipts_enabled := COALESCE((p #>> ARRAY['runtime_options', 'receipts_enabled'])::boolean, true);
  v_budget_accounting_enabled := COALESCE((p #>> ARRAY['runtime_options', 'budget_accounting_enabled'])::boolean, true);
  lowered := lower(sql_text);

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

  IF lowered ~ '\m(app_data|pg_catalog|information_schema|signing_keys|active_sessions|task_execution_state|task_rows_seen|safe_view_registry)\M' THEN
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
      rows := array_append(rows, row_json);
      rows_returned := rows_returned + 1;

      IF v_budget_accounting_enabled AND row_json ? 'expense_id' THEN
        INSERT INTO taskbound.task_rows_seen (budget_account, row_kind, row_id)
        VALUES (v_budget_account, 'expense', row_json->>'expense_id')
        ON CONFLICT DO NOTHING;
      END IF;
    END LOOP;
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
    WHERE task_execution_state.task_id = v_task_id;
  END IF;

  IF v_receipts_enabled THEN
    INSERT INTO taskbound.task_query_receipts (
      task_id, budget_account, query_digest, decision, rows_returned,
      unique_rows_added, remaining_unique_row_budget
    )
    VALUES (
      v_task_id,
      v_budget_account,
      encode(public.digest(sql_text, 'sha256'), 'hex'),
      'allowed',
      rows_returned,
      unique_added,
      CASE WHEN v_budget_accounting_enabled THEN max_rows - unique_after ELSE NULL END
    )
    RETURNING receipt_id INTO receipt;
  END IF;

  FOREACH row_json IN ARRAY rows LOOP
    RETURN NEXT row_json;
  END LOOP;
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
  WHERE a.backend_pid = pg_backend_pid()
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
  WHERE a.backend_pid = pg_backend_pid()
  ORDER BY r.created_at DESC
$$;
