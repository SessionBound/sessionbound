/* In-place upgrade for artifact databases initialized before receipt-chain
 * fields were added.  Fresh databases get these columns from 001_schema.sql. */
ALTER TABLE taskbound.task_query_receipts
  ADD COLUMN IF NOT EXISTS execution_id uuid NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN IF NOT EXISTS actor text,
  ADD COLUMN IF NOT EXISTS touched_views text[] NOT NULL DEFAULT ARRAY[]::text[],
  ADD COLUMN IF NOT EXISTS previous_receipt_hash text,
  ADD COLUMN IF NOT EXISTS receipt_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS task_query_receipts_task_execution_id_key
  ON taskbound.task_query_receipts (task_id, execution_id);

DROP FUNCTION IF EXISTS taskbound.audit_append_receipt(
  text, text, uuid, bigint, text, text, bigint, bigint, bigint, text, boolean
);

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

CREATE OR REPLACE FUNCTION taskbound.extract_touched_views(
  sql_text text,
  allowed_view_names text[] DEFAULT NULL
)
RETURNS text[]
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  lowered text := lower(COALESCE(sql_text, ''));
  v_allowed_views text[] := allowed_view_names;
  v_touched_oids oid[];
  v_oid_extraction_ok boolean := false;
  v_oid_touched_views text[];
  v_backend_start timestamptz;
  v_postmaster_start timestamptz;
BEGIN
  /*
   * Provenance helper for receipts, not a security predicate.  Bound database
   * paths prefer the native parse/analyze collector, which returns approved
   * safe-view OIDs from the analyzed query tree.  The allowed-name fallback is
   * used for trusted API preflight receipt writes that run on an admin
   * connection without the agent's bound backend-local guard state, and for
   * unsupported syntax that is denied before execution rather than parsed for
   * relation-set attestation.
   */
  IF v_allowed_views IS NULL
     AND lowered ~ '^\s*(select|with)\s'
     AND lowered !~ '\m(union|intersect|except|tablesample)\M'
     AND lowered !~ '\m(group[[:space:]]+by|having|over)\M'
     AND lowered !~ '\m(count|sum|avg|min|max|json_agg|jsonb_agg|array_agg|string_agg|xmlagg|row_to_json|json_build_object|jsonb_build_object|row_number|rank|dense_rank)\s*\('
     AND lowered !~ '\m(bank_account|phone|salary)\M' THEN
    BEGIN
      SELECT public.sessionbound_guard_touched_view_oids(sql_text)
      INTO v_touched_oids;
      v_oid_extraction_ok := true;
    EXCEPTION WHEN OTHERS THEN
      v_touched_oids := NULL;
      v_oid_extraction_ok := false;
    END;

    IF v_oid_extraction_ok THEN
      SELECT COALESCE(ARRAY(
        SELECT r.view_name
        FROM taskbound.safe_view_registry r
        WHERE (r.database_object::regclass)::oid = ANY(COALESCE(v_touched_oids, ARRAY[]::oid[]))
        ORDER BY r.view_name
      ), ARRAY[]::text[])
      INTO v_oid_touched_views;

      RETURN COALESCE(v_oid_touched_views, ARRAY[]::text[]);
    END IF;
  END IF;

  IF v_allowed_views IS NULL THEN
    SELECT backend_start INTO v_backend_start
    FROM pg_catalog.pg_stat_activity
    WHERE pid = pg_backend_pid();
    SELECT pg_catalog.pg_postmaster_start_time() INTO v_postmaster_start;

    SELECT taskbound.jsonb_text_array(a.payload->'allowed_views')
    INTO v_allowed_views
    FROM taskbound.active_sessions a
    WHERE a.owner_backend_pid = pg_backend_pid()
      AND a.owner_backend_start = v_backend_start
      AND a.owner_postmaster_start = v_postmaster_start
      AND a.database_oid = taskbound.current_database_oid()
    ORDER BY a.bound_at DESC
    LIMIT 1;
  END IF;

  v_allowed_views := COALESCE(v_allowed_views, ARRAY[]::text[]);

  RETURN COALESCE(ARRAY(
    WITH allowed AS (
      SELECT DISTINCT lower(view_name) AS view_name
      FROM unnest(v_allowed_views) AS allowed_name(view_name)
    )
    SELECT r.view_name
    FROM taskbound.safe_view_registry r
    JOIN allowed a ON a.view_name = lower(r.view_name)
    WHERE r.view_name ~ '^[a-z_][a-z0-9_]*$'
      AND (
        lowered ~ ('(^|[^a-z0-9_])"?taskbound"?[[:space:]]*\.[[:space:]]*"?'
                   || lower(r.view_name) || '"?([^a-z0-9_]|$)')
        OR
        lowered ~ ('(^|[^a-z0-9_\.])"?' || lower(r.view_name) || '"?([^a-z0-9_]|$)')
      )
    ORDER BY r.view_name
  ), ARRAY[]::text[]);
END;
$$;

/*
 * Append a task receipt on the autonomous audit connection.  The advisory
 * transaction lock serializes writers for one task, so the previous hash is
 * well-defined even when several agent backends finish concurrently.  This
 * function deliberately does not use the caller's transaction: denials raised
 * by the parser or executor must remain observable after ROLLBACK.
 */
CREATE OR REPLACE FUNCTION taskbound.audit_append_receipt(
  v_task_id text,
  v_budget_account text,
  v_binding_id uuid,
  v_fence_token bigint,
  v_query_digest text,
  v_decision text,
  v_rows_returned bigint DEFAULT 0,
  v_unique_rows_added bigint DEFAULT 0,
  v_remaining_unique_row_budget bigint DEFAULT NULL,
  v_reason text DEFAULT NULL,
  v_receipts_enabled boolean DEFAULT true,
  v_execution_id uuid DEFAULT NULL,
  v_actor text DEFAULT NULL,
  v_touched_views text[] DEFAULT ARRAY[]::text[]
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  v_conn text := taskbound.audit_connection_name();
  v_connections text[];
  v_sql text;
  v_effective_execution_id uuid := COALESCE(v_execution_id, gen_random_uuid());
BEGIN
  IF NOT COALESCE(v_receipts_enabled, true) OR COALESCE(v_task_id, '') = '' THEN
    RETURN;
  END IF;

  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  v_sql := format($sql$
    BEGIN;
    SELECT pg_advisory_xact_lock(hashtextextended(%L, 0));
    WITH owner AS (
      SELECT taskbound.mutation_fence_ok(%L, %L::uuid, %s) AS ok
    ), receipt_input AS (
      SELECT
        %L::uuid AS execution_id,
        COALESCE(NULLIF(%L, ''), (
          SELECT s.actor
          FROM taskbound.task_execution_state s
          WHERE s.task_id = %L
        ), '') AS actor,
        COALESCE(%L::text[], ARRAY[]::text[]) AS touched_views,
        clock_timestamp() AS receipt_created_at
    ), previous AS (
      SELECT COALESCE((
        SELECT r.receipt_hash
        FROM taskbound.task_query_receipts r
        WHERE r.task_id = %L
        ORDER BY r.created_at DESC, r.receipt_id DESC
        LIMIT 1
      ), '') AS previous_hash
    ), material AS (
      SELECT
             receipt_input.execution_id,
             receipt_input.actor,
             receipt_input.touched_views,
             receipt_input.receipt_created_at,
             previous_hash,
             encode(public.digest(
               concat_ws(chr(31),
                 receipt_input.execution_id::text,
                 %L, %L, %L, %s, %L, %L, %s, %s,
                 COALESCE(%s::text, ''),
                 COALESCE(%L, ''),
                 COALESCE(receipt_input.actor, ''),
                 COALESCE(array_to_string(receipt_input.touched_views, ','), ''),
                 receipt_input.receipt_created_at::text,
                 previous_hash
               ), 'sha256'), 'hex') AS current_hash
      FROM previous, receipt_input
    )
    INSERT INTO taskbound.task_query_receipts (
      execution_id, task_id, budget_account, binding_id, fence_token, query_digest,
      decision, rows_returned, unique_rows_added,
      remaining_unique_row_budget, reason, actor, touched_views,
      previous_receipt_hash, receipt_hash, created_at
    )
    SELECT execution_id, %L, %L, %L::uuid, %s, %L, %L, %s, %s,
           %s, %L, actor, touched_views, previous_hash, current_hash,
           receipt_created_at
    FROM material, owner
    WHERE owner.ok;
    COMMIT;
  $sql$,
    COALESCE(v_task_id, ''),
    COALESCE(v_task_id, ''),
    COALESCE(v_binding_id::text, ''),
    COALESCE(v_fence_token, 0),
    v_effective_execution_id::text,
    COALESCE(v_actor, ''),
    COALESCE(v_task_id, ''),
    COALESCE(v_touched_views, ARRAY[]::text[])::text,
    COALESCE(v_task_id, ''),
    COALESCE(v_task_id, ''),
    COALESCE(v_budget_account, v_task_id),
    COALESCE(v_binding_id::text, ''),
    COALESCE(v_fence_token, 0),
    COALESCE(v_query_digest, ''),
    COALESCE(v_decision, ''),
    GREATEST(COALESCE(v_rows_returned, 0), 0),
    GREATEST(COALESCE(v_unique_rows_added, 0), 0),
    COALESCE(v_remaining_unique_row_budget::text, 'NULL'),
    COALESCE(v_reason, ''),
    COALESCE(v_task_id, ''),
    COALESCE(v_budget_account, v_task_id),
    COALESCE(v_binding_id::text, ''),
    COALESCE(v_fence_token, 0),
    COALESCE(v_query_digest, ''),
    COALESCE(v_decision, ''),
    GREATEST(COALESCE(v_rows_returned, 0), 0),
    GREATEST(COALESCE(v_unique_rows_added, 0), 0),
    COALESCE(v_remaining_unique_row_budget::text, 'NULL'),
    COALESCE(v_reason, '')
  );

  PERFORM public.dblink_exec(v_conn, v_sql);
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

  PERFORM taskbound.audit_append_receipt(
    v_task_id,
    COALESCE(v_budget_account, v_task_id),
    v_binding_id,
    v_fence_token,
    encode(public.digest(COALESCE(sql_text, ''), 'sha256'), 'hex'),
    'denied',
    0,
    0,
    NULL,
    COALESCE(reason, 'query denied'),
    v_receipts_enabled,
    NULL,
    NULL,
    taskbound.extract_touched_views(sql_text)
  );
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

  /*
   * A denial is evidence about an attempted operation.  Send it through the
   * autonomous audit channel before raising so an enclosing agent
   * transaction cannot erase the evidence with ROLLBACK.
   */
  PERFORM taskbound.native_denied_receipt(
    active.task_id,
    COALESCE(p->>'budget_account', active.task_id),
    sql_text,
    reason,
    v_receipts_enabled,
    active.binding_id,
    active.fence_token
  );
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
      existing AS (
        SELECT query_count, revoked
        FROM taskbound.task_execution_state
        WHERE task_id = %L
      )
      SELECT CASE
        WHEN NOT (SELECT ok FROM owner) THEN 'BINDING_FENCED'
        WHEN EXISTS (SELECT 1 FROM existing WHERE revoked) THEN 'task is revoked'
        WHEN EXISTS (SELECT 1 FROM existing WHERE query_count >= %s) THEN 'query budget exhausted'
        WHEN EXISTS (SELECT 1 FROM existing) THEN 'ok'
        ELSE 'task execution state is missing'
      END AS status
      $sql$,
      v_task_id,
      v_binding_id,
      v_fence_token,
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
  v_max_queries int,
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
  v_disclosure_added bigint := 0;
  v_unique_after bigint;
  v_remaining_unique_row_budget bigint;
  v_status text := 'ok';
  v_query_digest text := encode(public.digest(COALESCE(sql_text, ''), 'sha256'), 'hex');
  v_touched_views text[] := ARRAY[]::text[];
BEGIN
  v_touched_views := taskbound.extract_touched_views(sql_text);

  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  /*
   * The disclosure unit is an emitted result tuple, not an optionally
   * projected business key. v_new_expense_ids remains only for wire
   * compatibility with pre-revision extension binaries and is ignored.
   *
   * This single remote statement is the release-barrier transition: it takes
   * the per-task receipt-chain lock, validates the binding fence, checks
   * revocation and budgets, applies the counter update for allowed releases,
   * appends exactly one receipt for the release decision, and returns the
   * resulting status to the caller.  No result tuple is released by wrapper or
   * native code until this statement reports ok.
   */
  SELECT status, disclosure_added, unique_after, remaining_unique_row_budget
  INTO v_status, v_disclosure_added, v_unique_after, v_remaining_unique_row_budget
  FROM public.dblink(
    v_conn,
    format(
      $sql$
      WITH receipt_lock AS (
        SELECT pg_advisory_xact_lock(hashtextextended(%L, 0))
      ),
      owner AS (
        SELECT taskbound.mutation_fence_ok(%L, %L::uuid, %s) AS ok
        FROM receipt_lock
      ),
      existing AS (
        SELECT task_id, query_count, returned_rows, unique_expense_rows,
               revoked, actor
        FROM taskbound.task_execution_state
        WHERE task_id = %L
        FOR UPDATE
      ),
      decision AS (
        SELECT
          CASE
            WHEN NOT (SELECT ok FROM owner) THEN 'BINDING_FENCED'
            WHEN NOT EXISTS (SELECT 1 FROM existing) THEN 'task execution state is missing'
            WHEN EXISTS (SELECT 1 FROM existing WHERE revoked) THEN 'task is revoked'
            WHEN %L::boolean
              AND EXISTS (SELECT 1 FROM existing WHERE query_count >= %s)
              THEN 'query budget exhausted'
            WHEN %L::boolean
              AND EXISTS (
                SELECT 1
                FROM existing
                WHERE unique_expense_rows + %s > %s
              )
              THEN 'result tuple budget exceeded'
            ELSE 'ok'
          END AS status
      ),
      updated AS (
        UPDATE taskbound.task_execution_state s
        SET query_count = s.query_count + 1,
            returned_rows = s.returned_rows + %s,
            unique_expense_rows = s.unique_expense_rows + %s
        WHERE s.task_id = %L
          AND %L::boolean
          AND (SELECT status FROM decision) = 'ok'
          AND (SELECT ok FROM owner)
        RETURNING s.unique_expense_rows, s.actor
      ),
      receipt_input AS (
        SELECT
          gen_random_uuid() AS execution_id,
          COALESCE((SELECT actor FROM updated), (SELECT actor FROM existing), '') AS actor,
          COALESCE(%L::text[], ARRAY[]::text[]) AS touched_views,
          clock_timestamp() AS receipt_created_at
      ),
      previous AS (
        SELECT COALESCE((
          SELECT r.receipt_hash
          FROM taskbound.task_query_receipts r
          WHERE r.task_id = %L
          ORDER BY r.created_at DESC, r.receipt_id DESC
          LIMIT 1
        ), '') AS previous_hash
      ),
      material AS (
        SELECT
          receipt_input.execution_id,
          receipt_input.actor,
          receipt_input.touched_views,
          receipt_input.receipt_created_at,
          previous.previous_hash,
          (SELECT status FROM decision) AS status,
          CASE WHEN (SELECT status FROM decision) = 'ok' THEN 'allowed' ELSE 'denied' END AS receipt_decision,
          CASE WHEN (SELECT status FROM decision) = 'ok' THEN %s::bigint ELSE 0::bigint END AS rows_for_receipt,
          CASE WHEN (SELECT status FROM decision) = 'ok' AND %L::boolean THEN %s::bigint ELSE 0::bigint END AS unique_added_for_receipt,
          CASE
            WHEN NOT %L::boolean THEN NULL::bigint
            WHEN (SELECT status FROM decision) = 'ok'
              THEN %s::bigint - COALESCE((SELECT unique_expense_rows FROM updated), 0)
            ELSE %s::bigint - COALESCE((SELECT unique_expense_rows FROM existing), 0)
          END AS remaining_budget_for_receipt,
          CASE WHEN (SELECT status FROM decision) = 'ok' THEN NULL::text ELSE (SELECT status FROM decision) END AS reason_for_receipt
        FROM receipt_input, previous
      ),
      hashed AS (
        SELECT
          material.*,
          encode(public.digest(
            concat_ws(chr(31),
              material.execution_id::text,
              %L, %L, %L, %s, %L,
              material.receipt_decision,
              material.rows_for_receipt,
              material.unique_added_for_receipt,
              COALESCE(material.remaining_budget_for_receipt::text, ''),
              COALESCE(material.reason_for_receipt, ''),
              COALESCE(material.actor, ''),
              COALESCE(array_to_string(material.touched_views, ','), ''),
              material.receipt_created_at::text,
              material.previous_hash
            ),
            'sha256'
          ), 'hex') AS current_hash
        FROM material
      ),
      inserted AS (
        INSERT INTO taskbound.task_query_receipts (
          execution_id, task_id, budget_account, binding_id, fence_token,
          query_digest, decision, rows_returned, unique_rows_added,
          remaining_unique_row_budget, reason, actor, touched_views,
          previous_receipt_hash, receipt_hash, created_at
        )
        SELECT
          execution_id, %L, %L, %L::uuid, %s,
          %L, receipt_decision, rows_for_receipt, unique_added_for_receipt,
          remaining_budget_for_receipt, reason_for_receipt, actor, touched_views,
          previous_hash, current_hash, receipt_created_at
        FROM hashed
        WHERE %L::boolean
          AND (SELECT ok FROM owner)
          AND EXISTS (SELECT 1 FROM existing)
        RETURNING 1
      )
      SELECT
        status,
        unique_added_for_receipt AS disclosure_added,
        CASE
          WHEN %L::boolean AND status = 'ok'
            THEN COALESCE((SELECT unique_expense_rows FROM updated), 0)
          ELSE COALESCE((SELECT unique_expense_rows FROM existing), 0)
        END AS unique_after,
        remaining_budget_for_receipt AS remaining_unique_row_budget
      FROM hashed
      $sql$,
      COALESCE(v_task_id, ''),
      COALESCE(v_task_id, ''),
      COALESCE(v_binding_id::text, ''),
      COALESCE(v_fence_token, 0),
      COALESCE(v_task_id, ''),
      COALESCE(v_budget_accounting_enabled, true),
      GREATEST(COALESCE(v_max_queries, 0), 0),
      COALESCE(v_budget_accounting_enabled, true),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      GREATEST(COALESCE(v_max_rows, 0), 0),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      COALESCE(v_task_id, ''),
      COALESCE(v_budget_accounting_enabled, true),
      COALESCE(v_touched_views, ARRAY[]::text[])::text,
      COALESCE(v_task_id, ''),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      COALESCE(v_budget_accounting_enabled, true),
      GREATEST(COALESCE(v_rows_returned, 0), 0),
      COALESCE(v_budget_accounting_enabled, true),
      GREATEST(COALESCE(v_max_rows, 0), 0),
      GREATEST(COALESCE(v_max_rows, 0), 0),
      COALESCE(v_task_id, ''),
      COALESCE(v_budget_account, v_task_id),
      COALESCE(v_binding_id::text, ''),
      COALESCE(v_fence_token, 0),
      v_query_digest,
      COALESCE(v_task_id, ''),
      COALESCE(v_budget_account, v_task_id),
      COALESCE(v_binding_id::text, ''),
      COALESCE(v_fence_token, 0),
      v_query_digest,
      COALESCE(v_receipts_enabled, true),
      COALESCE(v_budget_accounting_enabled, true)
    )
  ) AS t(status text, disclosure_added bigint, unique_after bigint, remaining_unique_row_budget bigint);

  IF v_status <> 'ok' THEN
    RAISE EXCEPTION 'SessionBoundDB denied query: %', COALESCE(v_status, 'query denied');
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
BEGIN
  /* Compatibility entry point retained for old callers.  Prefix charging is
   * intentionally retired: rejected executions are recorded as zero-release
   * denials and never mutate the disclosure counter. */
  PERFORM taskbound.native_denied_receipt(
    v_task_id,
    v_budget_account,
    sql_text,
    COALESCE(reason, 'query denied; atomic release withheld'),
    v_receipts_enabled,
    v_binding_id,
    v_fence_token
  );
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
  has_aggregate boolean;
  has_group_by boolean;
  has_having boolean;
  has_where boolean;
  has_window boolean;
BEGIN
  has_aggregate := lowered ~ '\m(count|sum|avg|min|max)[[:space:]]*\(';
  has_group_by := lowered ~ '\mgroup[[:space:]]+by\M';
  has_having := lowered ~ '\mhaving\M';
  has_where := lowered ~ '\mwhere\M';
  has_window := lowered ~ '\mover[[:space:]]*\(';

  IF NOT has_aggregate AND NOT has_group_by AND NOT has_window THEN
    RETURN;
  END IF;

  IF has_window THEN
    RAISE EXCEPTION 'window functions require an approved aggregate template';
  END IF;

  IF has_having THEN
    RAISE EXCEPTION 'minimum group-size policy denied HAVING predicates';
  END IF;

  IF lowered ~ '\mgroup[[:space:]]+by[[:space:][:alnum:]_.,"]*\m(employee_id|expense_id)\M' THEN
    RAISE EXCEPTION 'minimum group-size policy denied grouping by sensitive entity identifiers';
  END IF;

  IF has_group_by THEN
    RAISE EXCEPTION 'GROUP BY aggregate release requires an approved aggregate template';
  END IF;

  IF has_aggregate AND has_where THEN
    RAISE EXCEPTION 'filtered aggregate release requires an approved aggregate template';
  END IF;

  IF has_aggregate THEN
    RAISE EXCEPTION 'aggregate release requires an approved aggregate template';
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
  max_queries int;
  max_rows int;
  v_task_id text;
  v_budget_account text;
  v_receipts_enabled boolean;
  v_budget_accounting_enabled boolean;
  v_min_group_size int;
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

  IF lowered ~ '^\s*with[[:space:]]+recursive\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'recursive CTEs are not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: recursive CTEs are not allowed';
  END IF;

  IF lowered ~ '\m(union|intersect|except)\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'UNION, INTERSECT, and EXCEPT are not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: UNION, INTERSECT, and EXCEPT are not allowed';
  END IF;

  IF lowered ~ '\mtablesample\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'TABLESAMPLE is not allowed in task SQL');
    RAISE EXCEPTION 'SessionBoundDB denied query: TABLESAMPLE is not allowed in task SQL';
  END IF;

  IF lowered ~ '\m(insert|update|delete|drop|alter|create|truncate|copy|call|grant|revoke)\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'mutating or administrative keyword is not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: mutating or administrative keyword is not allowed';
  END IF;

  IF lowered ~ '\m(pg_catalog|information_schema|pg_tables|pg_user|pg_stat_activity|pg_prepared_statements|pg_cursors)\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'catalog access is not allowed');
    RAISE EXCEPTION 'SessionBoundDB denied query: catalog access is not allowed';
  END IF;

  IF lowered ~ '\m(app_data|signing_keys|active_sessions|binding_events|task_execution_state|task_rows_seen|safe_view_registry)\M' THEN
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

  /*
   * Conservative aggregate/window gating is a first-class wrapper precheck,
   * not a row-result heuristic.  Keep these direct checks outside PL/pgSQL
   * exception-driven control flow so denied wrapper queries follow the same
   * stable receipt path as other lexical denials.
   */
  IF lowered ~ '\mover[[:space:]]*\(' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'window functions require an approved aggregate template');
    RAISE EXCEPTION 'SessionBoundDB denied query: window functions require an approved aggregate template';
  END IF;

  IF lowered ~ '\mgroup[[:space:]]+by\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'GROUP BY aggregate release requires an approved aggregate template');
    RAISE EXCEPTION 'SessionBoundDB denied query: GROUP BY aggregate release requires an approved aggregate template';
  END IF;

  IF lowered ~ '\m(count|sum|avg|min|max)[[:space:]]*\(' AND lowered ~ '\mwhere\M' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'filtered aggregate release requires an approved aggregate template');
    RAISE EXCEPTION 'SessionBoundDB denied query: filtered aggregate release requires an approved aggregate template';
  END IF;

  IF lowered ~ '\m(count|sum|avg|min|max)[[:space:]]*\(' THEN
    PERFORM taskbound.fail_receipt(sql_text, 'aggregate release requires an approved aggregate template');
    RAISE EXCEPTION 'SessionBoundDB denied query: aggregate release requires an approved aggregate template';
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

  /* Reserve the query counter through the same fenced autonomous path used by
   * native SQL.  This keeps wrapper/native query-budget semantics identical
   * and makes an overflow denial rollback-surviving as well. */
  PERFORM taskbound.native_reserve_query(
    v_task_id,
    v_budget_account,
    sql_text,
    max_queries,
    v_budget_accounting_enabled,
    v_receipts_enabled,
    active.binding_id,
    active.fence_token
  );

  BEGIN
    PERFORM public.sessionbound_guard_check(sql_text);

    FOR row_item IN EXECUTE sql_text LOOP
      row_json := to_jsonb(row_item);
      /* Materialize before release so wrapper and native paths are atomic. */
      rows := array_append(rows, row_json);
      rows_returned := rows_returned + 1;
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

  /* Use the same autonomous commit path as native SQL.  This keeps wrapper
   * accounting and the allow receipt alive when the caller later rolls back
   * its business transaction, and makes wrapper/native semantics identical. */
  PERFORM taskbound.native_finish_query(
    v_task_id,
    v_budget_account,
    sql_text,
    rows_returned,
    ARRAY[]::text[],
    max_queries,
    max_rows,
    v_budget_accounting_enabled,
    v_receipts_enabled,
    active.binding_id,
    active.fence_token
  );

  /* This is intentionally last: neither a rejected query nor a failed
   * accounting/receipt write exposes a result prefix to the caller. */
  FOREACH row_json IN ARRAY rows LOOP
    RETURN NEXT row_json;
  END LOOP;
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
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
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
    AND a.database_oid = taskbound.current_database_oid();

  IF NOT FOUND THEN
    RETURN;
  END IF;

  PERFORM taskbound.validate_active_binding(
    active.task_id,
    active.binding_id,
    active.fence_token,
    active.advisory_lock_key
  );

  RETURN QUERY
  SELECT s.task_id, s.budget_account, s.query_count, s.returned_rows,
         s.unique_expense_rows, s.revoked
  FROM taskbound.task_execution_state s
  WHERE s.task_id = active.task_id;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.receipts()
RETURNS SETOF taskbound.task_query_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
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
    AND a.database_oid = taskbound.current_database_oid();

  IF NOT FOUND THEN
    RETURN;
  END IF;

  PERFORM taskbound.validate_active_binding(
    active.task_id,
    active.binding_id,
    active.fence_token,
    active.advisory_lock_key
  );

  RETURN QUERY
  SELECT r.*
  FROM taskbound.task_query_receipts r
  WHERE r.task_id = active.task_id
  ORDER BY r.created_at DESC;
END;
$$;
