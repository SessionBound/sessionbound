CREATE OR REPLACE FUNCTION taskbound.fail_receipt(sql_text text, reason text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  p jsonb;
BEGIN
  p := taskbound.current_payload();
  IF p IS NOT NULL THEN
    INSERT INTO taskbound.task_query_receipts (
      task_id, budget_account, query_digest, decision, reason
    )
    VALUES (
      p->>'task_id',
      COALESCE(p->>'budget_account', p->>'task_id'),
      encode(public.digest(sql_text, 'sha256'), 'hex'),
      'denied',
      reason
    );
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
  receipt uuid;
BEGIN
  p := taskbound.require_payload();
  v_task_id := p->>'task_id';
  v_budget_account := COALESCE(p->>'budget_account', v_task_id);
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

  IF (SELECT query_count FROM taskbound.task_execution_state WHERE task_execution_state.task_id = v_task_id) >= max_queries THEN
    PERFORM taskbound.fail_receipt(sql_text, 'query budget exhausted');
    RAISE EXCEPTION 'SessionBoundDB denied query: query budget exhausted';
  END IF;

  SELECT count(*) INTO unique_before
  FROM taskbound.task_rows_seen
  WHERE task_rows_seen.budget_account = v_budget_account
    AND row_kind = 'expense';

  FOR row_item IN EXECUTE sql_text LOOP
    row_json := to_jsonb(row_item);
    rows := array_append(rows, row_json);
    rows_returned := rows_returned + 1;

    IF row_json ? 'expense_id' THEN
      INSERT INTO taskbound.task_rows_seen (budget_account, row_kind, row_id)
      VALUES (v_budget_account, 'expense', row_json->>'expense_id')
      ON CONFLICT DO NOTHING;
    END IF;
  END LOOP;

  SELECT count(*) INTO unique_after
  FROM taskbound.task_rows_seen
  WHERE task_rows_seen.budget_account = v_budget_account
    AND row_kind = 'expense';

  unique_added := unique_after - unique_before;

  IF unique_after > max_rows THEN
    PERFORM taskbound.fail_receipt(sql_text, 'unique expense row budget exceeded');
    RAISE EXCEPTION 'SessionBoundDB denied query: unique expense row budget exceeded';
  END IF;

  UPDATE taskbound.task_execution_state
  SET query_count = query_count + 1,
      returned_rows = returned_rows + rows_returned,
      unique_expense_rows = unique_after
  WHERE task_execution_state.task_id = v_task_id;

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
    max_rows - unique_after
  )
  RETURNING receipt_id INTO receipt;

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
