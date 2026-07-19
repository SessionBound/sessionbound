CREATE OR REPLACE FUNCTION taskbound.binding_advisory_lock_key(
  v_task_id text,
  v_token_digest text,
  v_credential_id text
)
RETURNS bigint
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  canonical text;
  digest_hex text;
BEGIN
  canonical :=
    'task_id=' || COALESCE(length(v_task_id)::text, '0') || ':' || COALESCE(v_task_id, '') ||
    E'\x1f' ||
    'token_digest=' || COALESCE(length(v_token_digest)::text, '0') || ':' || COALESCE(v_token_digest, '') ||
    E'\x1f' ||
    'credential_id=' || COALESCE(length(v_credential_id)::text, '0') || ':' || COALESCE(v_credential_id, '');
  digest_hex := substr(encode(public.digest(canonical, 'sha256'), 'hex'), 1, 16);
  RETURN ('x' || digest_hex)::bit(64)::bigint;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.current_database_oid()
RETURNS oid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT oid FROM pg_database WHERE datname = current_database()
$$;

CREATE OR REPLACE FUNCTION taskbound.lock_connection_name()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT 'sessionbound_lock_' || pg_backend_pid()::text
$$;

CREATE OR REPLACE FUNCTION taskbound.advisory_lock_held_by_backend(v_lock_key bigint, v_lock_backend_pid int)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM pg_locks l
    WHERE l.locktype = 'advisory'
      AND l.pid = v_lock_backend_pid
      AND l.granted
      AND l.objsubid = 1
      AND l.classid::bigint = ((v_lock_key >> 32) & 4294967295::bigint)
      AND l.objid::bigint = (v_lock_key & 4294967295::bigint)
  )
$$;

CREATE OR REPLACE FUNCTION taskbound.log_binding_event_local(
  v_event_type text,
  v_task_id text,
  v_token_digest text,
  v_credential_id text,
  v_binding_id uuid,
  v_fence_token bigint,
  v_advisory_lock_key bigint,
  v_owner_backend_pid int,
  v_reason text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
BEGIN
  INSERT INTO taskbound.binding_events (
    event_type, task_id, token_digest, credential_id, binding_id,
    fence_token, advisory_lock_key, owner_backend_pid, reason
  )
  VALUES (
    v_event_type, v_task_id, v_token_digest, COALESCE(v_credential_id, ''),
    v_binding_id, v_fence_token, v_advisory_lock_key, v_owner_backend_pid, v_reason
  );
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.owner_is_live(
  v_database_oid oid,
  v_owner_backend_pid int,
  v_owner_backend_start timestamptz,
  v_owner_postmaster_start timestamptz
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT pg_postmaster_start_time() = v_owner_postmaster_start
     AND EXISTS (
       SELECT 1
       FROM pg_stat_activity s
       JOIN pg_database d ON d.datname = s.datname
       WHERE s.pid = v_owner_backend_pid
         AND s.backend_start = v_owner_backend_start
         AND d.oid = v_database_oid
     )
$$;

CREATE OR REPLACE FUNCTION taskbound.claim_active_binding_row(
  v_task_id text,
  v_token_digest text,
  v_token_nonce text,
  v_credential_id text,
  v_binding_id uuid,
  v_fence_token bigint,
  v_advisory_lock_key bigint,
  v_database_oid oid,
  v_lock_backend_pid int,
  v_owner_backend_pid int,
  v_owner_backend_start timestamptz,
  v_owner_postmaster_start timestamptz,
  v_owner_session_user name,
  v_payload jsonb,
  v_token_expires_at timestamptz,
  v_tenant_id text,
  v_delegator text,
  v_actor text,
  v_purpose text,
  v_budget_account text
)
RETURNS TABLE(status text, recovered boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  existing record;
  stale_backend record;
BEGIN
  FOR stale_backend IN
    SELECT *
    FROM taskbound.active_sessions a
    WHERE a.backend_pid = v_owner_backend_pid
      AND NOT (
        a.task_id = v_task_id
        AND a.token_digest = v_token_digest
        AND a.credential_id = COALESCE(v_credential_id, '')
      )
      AND NOT (
        a.owner_backend_start = v_owner_backend_start
        AND a.owner_postmaster_start = v_owner_postmaster_start
        AND a.database_oid = v_database_oid
      )
  LOOP
    IF taskbound.owner_is_live(
      stale_backend.database_oid,
      stale_backend.owner_backend_pid,
      stale_backend.owner_backend_start,
      stale_backend.owner_postmaster_start
    ) THEN
      PERFORM taskbound.log_binding_event_local(
        'BINDING_STATE_INCONSISTENT',
        stale_backend.task_id,
        stale_backend.token_digest,
        stale_backend.credential_id,
        stale_backend.binding_id,
        stale_backend.fence_token,
        stale_backend.advisory_lock_key,
        stale_backend.owner_backend_pid,
        'backend pid is already owned by a live binding'
      );
      status := 'backend_already_bound';
      recovered := false;
      RETURN NEXT;
      RETURN;
    END IF;

    DELETE FROM taskbound.active_sessions
    WHERE binding_id = stale_backend.binding_id
      AND fence_token = stale_backend.fence_token;

    PERFORM taskbound.log_binding_event_local(
      'STALE_BINDING_REAPED',
      stale_backend.task_id,
      stale_backend.token_digest,
      stale_backend.credential_id,
      stale_backend.binding_id,
      stale_backend.fence_token,
      stale_backend.advisory_lock_key,
      stale_backend.owner_backend_pid,
      'stale row for reused backend pid was removed'
    );
  END LOOP;

  SELECT *
  INTO existing
  FROM taskbound.active_sessions a
  WHERE a.task_id = v_task_id
    AND a.token_digest = v_token_digest
    AND a.credential_id = COALESCE(v_credential_id, '')
  FOR UPDATE;

  IF FOUND THEN
    IF taskbound.owner_is_live(
      existing.database_oid,
      existing.owner_backend_pid,
      existing.owner_backend_start,
      existing.owner_postmaster_start
    ) THEN
      PERFORM taskbound.log_binding_event_local(
        'BINDING_STATE_INCONSISTENT',
        v_task_id,
        v_token_digest,
        v_credential_id,
        v_binding_id,
        v_fence_token,
        v_advisory_lock_key,
        v_owner_backend_pid,
        'advisory lock was acquired while the recorded owner is still live'
      );
      status := 'live_owner';
      recovered := false;
      RETURN NEXT;
      RETURN;
    END IF;

    UPDATE taskbound.active_sessions
    SET backend_pid = v_owner_backend_pid,
        backend_start = v_owner_backend_start,
        token_nonce = COALESCE(v_token_nonce, ''),
        binding_id = v_binding_id,
        fence_token = v_fence_token,
        advisory_lock_key = v_advisory_lock_key,
        database_oid = v_database_oid,
        lock_backend_pid = v_lock_backend_pid,
        owner_backend_pid = v_owner_backend_pid,
        owner_backend_start = v_owner_backend_start,
        owner_postmaster_start = v_owner_postmaster_start,
        owner_session_user = v_owner_session_user,
        payload = v_payload,
        bound_at = now(),
        acquired_at = now(),
        last_seen_at = now(),
        token_expires_at = v_token_expires_at
    WHERE task_id = v_task_id
      AND token_digest = v_token_digest
      AND credential_id = COALESCE(v_credential_id, '');

    PERFORM taskbound.log_binding_event_local(
      'STALE_BINDING_RECOVERED',
      v_task_id,
      v_token_digest,
      v_credential_id,
      v_binding_id,
      v_fence_token,
      v_advisory_lock_key,
      v_owner_backend_pid,
      'stale binding row recovered after advisory lock acquisition'
    );
    status := 'ok';
    recovered := true;
    RETURN NEXT;
    RETURN;
  END IF;

  INSERT INTO taskbound.active_sessions (
    backend_pid, backend_start, task_id, token_digest, token_nonce,
    credential_id, binding_id, fence_token, advisory_lock_key, database_oid,
    lock_backend_pid, owner_backend_pid, owner_backend_start, owner_postmaster_start,
    owner_session_user, payload, token_expires_at
  )
  VALUES (
    v_owner_backend_pid, v_owner_backend_start, v_task_id, v_token_digest,
    COALESCE(v_token_nonce, ''), COALESCE(v_credential_id, ''), v_binding_id,
    v_fence_token, v_advisory_lock_key, v_database_oid, v_lock_backend_pid, v_owner_backend_pid,
    v_owner_backend_start, v_owner_postmaster_start, v_owner_session_user,
    v_payload, v_token_expires_at
  );

  IF COALESCE(v_credential_id, '') <> '' THEN
    INSERT INTO taskbound.task_credential_bindings (
      task_id, credential_id, token_digest, first_backend_pid, session_user_name
    )
    VALUES (
      v_task_id, v_credential_id, v_token_digest, v_owner_backend_pid, v_owner_session_user
    )
    ON CONFLICT (task_id) DO NOTHING;
  END IF;

  INSERT INTO taskbound.task_execution_state (
    task_id, tenant_id, delegator, actor, purpose, budget_account, expires_at
  )
  VALUES (
    v_task_id, v_tenant_id, v_delegator, v_actor, v_purpose, v_budget_account,
    v_token_expires_at
  )
  ON CONFLICT ON CONSTRAINT task_execution_state_pkey DO NOTHING;

  PERFORM taskbound.log_binding_event_local(
    'BINDING_ACTIVE',
    v_task_id,
    v_token_digest,
    v_credential_id,
    v_binding_id,
    v_fence_token,
    v_advisory_lock_key,
    v_owner_backend_pid,
    'active binding row claimed'
  );
  status := 'ok';
  recovered := false;
  RETURN NEXT;
EXCEPTION WHEN unique_violation THEN
  PERFORM taskbound.log_binding_event_local(
    'ACTIVE_BINDING_EXISTS',
    v_task_id,
    v_token_digest,
    v_credential_id,
    v_binding_id,
    v_fence_token,
    v_advisory_lock_key,
    v_owner_backend_pid,
    SQLERRM
  );
  status := 'conflict';
  recovered := false;
  RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.release_active_binding_row(
  v_binding_id uuid,
  v_fence_token bigint,
  v_event_type text DEFAULT 'BINDING_RELEASED'
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  released record;
BEGIN
  DELETE FROM taskbound.active_sessions
  WHERE binding_id = v_binding_id
    AND fence_token = v_fence_token
  RETURNING * INTO released;

  IF NOT FOUND THEN
    PERFORM taskbound.log_binding_event_local(
      'BINDING_FENCED',
      NULL,
      NULL,
      NULL,
      v_binding_id,
      v_fence_token,
      NULL,
      NULL,
      'release rejected by binding_id/fence_token mismatch'
    );
    RETURN false;
  END IF;

  PERFORM taskbound.log_binding_event_local(
    v_event_type,
    released.task_id,
    released.token_digest,
    released.credential_id,
    released.binding_id,
    released.fence_token,
    released.advisory_lock_key,
    released.owner_backend_pid,
    'active binding row released'
  );
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.reap_stale_bindings()
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  row_item record;
  removed int := 0;
BEGIN
  FOR row_item IN SELECT * FROM taskbound.active_sessions LOOP
    IF NOT taskbound.owner_is_live(
      row_item.database_oid,
      row_item.owner_backend_pid,
      row_item.owner_backend_start,
      row_item.owner_postmaster_start
    ) THEN
      DELETE FROM taskbound.active_sessions
      WHERE binding_id = row_item.binding_id
        AND fence_token = row_item.fence_token;
      removed := removed + 1;
      PERFORM taskbound.log_binding_event_local(
        'STALE_BINDING_REAPED',
        row_item.task_id,
        row_item.token_digest,
        row_item.credential_id,
        row_item.binding_id,
        row_item.fence_token,
        row_item.advisory_lock_key,
        row_item.owner_backend_pid,
        'maintenance reaper removed a row whose owner backend no longer exists'
      );
    END IF;
  END LOOP;
  RETURN removed;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.validate_active_binding(
  v_task_id text,
  v_binding_id uuid,
  v_fence_token bigint,
  v_advisory_lock_key bigint
)
RETURNS void
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
    AND a.database_oid = taskbound.current_database_oid()
    AND a.task_id = v_task_id
    AND a.binding_id = v_binding_id
    AND a.fence_token = v_fence_token
    AND a.advisory_lock_key = v_advisory_lock_key;

  IF NOT FOUND THEN
    PERFORM taskbound.log_binding_event_local(
      'BINDING_FENCED',
      v_task_id,
      NULL,
      NULL,
      v_binding_id,
      v_fence_token,
      v_advisory_lock_key,
      pg_backend_pid(),
      'active binding row does not match backend-local fence'
    );
    RAISE EXCEPTION 'BINDING_FENCED: active binding fence mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF NOT taskbound.advisory_lock_held_by_backend(v_advisory_lock_key, active.lock_backend_pid) THEN
    PERFORM taskbound.log_binding_event_local(
      'BINDING_LOST',
      active.task_id,
      active.token_digest,
      active.credential_id,
      active.binding_id,
      active.fence_token,
      active.advisory_lock_key,
      active.owner_backend_pid,
      'session advisory lock is no longer held by the owner backend'
    );
    RAISE EXCEPTION 'BINDING_LOST: active binding advisory lock is no longer held'
      USING ERRCODE = '42501';
  END IF;

  IF active.token_expires_at <= now() THEN
    RAISE EXCEPTION 'task token is expired'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM taskbound.task_execution_state s
    WHERE s.task_id = active.task_id
      AND s.revoked
  ) THEN
    RAISE EXCEPTION 'task is revoked'
      USING ERRCODE = '42501';
  END IF;

  IF active.credential_id <> '' AND EXISTS (
    SELECT 1
    FROM taskbound.credential_ledger c
    WHERE c.credential_id = active.credential_id
      AND (c.revoked OR c.expires_at <= now() OR c.db_user <> session_user)
  ) THEN
    RAISE EXCEPTION 'runtime credential is expired, revoked, or no longer matches the session'
      USING ERRCODE = '42501';
  END IF;

  -- last_seen_at is diagnostic only; do not update it in the caller
  -- transaction because bind/unbind state is maintained autonomously.
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.current_payload()
RETURNS jsonb
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
    RETURN NULL;
  END IF;

  PERFORM taskbound.validate_active_binding(
    active.task_id,
    active.binding_id,
    active.fence_token,
    active.advisory_lock_key
  );
  RETURN active.payload;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.require_payload()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  p jsonb;
BEGIN
  SELECT taskbound.current_payload() INTO p;
  IF p IS NULL THEN
    RAISE EXCEPTION 'no task is bound to this database session';
  END IF;
  RETURN p;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.bind_task(payload_text text, signature_hex text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  p jsonb;
  expected text;
  secret text;
  v_task_id text;
  v_budget_account text;
  v_credential_id text;
  v_token_digest text;
  v_existing_task text;
  v_existing_credential text;
  v_session_user name;
  v_backend_start timestamptz;
  v_credential record;
  v_binding record;
  v_expected_snapshot jsonb;
  v_allowed_view_oids text;
  v_receipts_enabled boolean;
  v_budget_accounting_enabled boolean;
  v_max_queries int;
  v_max_rows int;
  v_min_group_size int;
  v_token_nonce text;
  v_binding_id uuid := gen_random_uuid();
  v_fence_token bigint;
  v_advisory_lock_key bigint;
  v_database_oid oid;
  v_postmaster_start timestamptz;
  v_conn text := taskbound.lock_connection_name();
  v_connections text[];
  v_claim_status text;
  v_recovered boolean;
  v_lock_acquired boolean := false;
  v_lock_backend_pid int;
BEGIN
  p := payload_text::jsonb;
  SELECT signing_keys.secret INTO secret
  FROM taskbound.signing_keys
  WHERE key_id = COALESCE(p->>'key_id', 'dev');

  IF secret IS NULL THEN
    RAISE EXCEPTION 'unknown signing key';
  END IF;

  expected := encode(public.hmac(convert_to(payload_text, 'utf8'), convert_to(secret, 'utf8'), 'sha256'), 'hex');
  IF expected <> signature_hex THEN
    RAISE EXCEPTION 'invalid task token signature';
  END IF;

  IF (p->>'expires_at')::timestamptz <= now() THEN
    RAISE EXCEPTION 'task token is expired';
  END IF;

  v_task_id := p->>'task_id';
  v_budget_account := COALESCE(p->>'budget_account', v_task_id);
  v_credential_id := COALESCE(p->>'credential_id', '');
  v_token_digest := encode(public.digest(payload_text, 'sha256'), 'hex');
  v_token_nonce := COALESCE(p->>'nonce', p #>> ARRAY['token', 'nonce'], '');
  v_receipts_enabled := COALESCE((p #>> ARRAY['runtime_options', 'receipts_enabled'])::boolean, true);
  v_budget_accounting_enabled := COALESCE((p #>> ARRAY['runtime_options', 'budget_accounting_enabled'])::boolean, true);
  v_max_queries := COALESCE((p #>> ARRAY['budgets', 'max_queries'])::int, 100);
  v_max_rows := COALESCE((p #>> ARRAY['budgets', 'max_unique_expense_rows'])::int, 1000000);
  v_min_group_size := COALESCE((p #>> ARRAY['aggregate_policy', 'min_group_size'])::int, 5);
  v_fence_token := nextval('taskbound.binding_fence_token_seq');
  v_advisory_lock_key := taskbound.binding_advisory_lock_key(v_task_id, v_token_digest, v_credential_id);
  v_session_user := session_user;
  SELECT backend_start INTO v_backend_start
  FROM pg_catalog.pg_stat_activity
  WHERE pid = pg_backend_pid();
  SELECT pg_catalog.pg_postmaster_start_time() INTO v_postmaster_start;
  v_database_oid := taskbound.current_database_oid();

  IF COALESCE(p->>'audience', 'sessionbounddb') <> 'sessionbounddb' THEN
    RAISE EXCEPTION 'task token audience is not valid for SessionBoundDB';
  END IF;

  IF p ? 'safe_view_registry' THEN
    v_expected_snapshot := taskbound.safe_view_registry_snapshot(taskbound.jsonb_text_array(p->'allowed_views'));
    IF p->'safe_view_registry' <> v_expected_snapshot THEN
      RAISE EXCEPTION 'task token safe-view registry snapshot is stale; re-approval is required';
    END IF;
    IF p->>'safe_view_registry_version' <> v_expected_snapshot->>'safe_view_registry_version' THEN
      RAISE EXCEPTION 'task token safe-view registry version is stale; re-approval is required';
    END IF;
    IF p->>'view_definition_hash' <> v_expected_snapshot->>'view_definition_hash' THEN
      RAISE EXCEPTION 'task token safe-view definition hash is stale; re-approval is required';
    END IF;
    IF p ? 'exposed_column_hash'
       AND p->>'exposed_column_hash' <> v_expected_snapshot->>'exposed_column_hash' THEN
      RAISE EXCEPTION 'task token exposed-column hash is stale; re-approval is required';
    END IF;
  END IF;

  SELECT task_id, credential_id
  INTO v_existing_task, v_existing_credential
  FROM taskbound.active_sessions
  WHERE owner_backend_pid = pg_backend_pid()
    AND owner_backend_start = v_backend_start
    AND owner_postmaster_start = v_postmaster_start
    AND database_oid = v_database_oid;

  IF v_existing_task IS NOT NULL THEN
    RAISE EXCEPTION 'database session is already bound to another active task; unbind before rebinding';
  END IF;

  IF v_credential_id <> '' THEN
    SELECT *
    INTO v_credential
    FROM taskbound.credential_ledger
    WHERE credential_id = v_credential_id;

    IF v_credential.credential_id IS NULL THEN
      RAISE EXCEPTION 'unknown runtime credential';
    END IF;
    IF v_credential.db_user <> v_session_user THEN
      RAISE EXCEPTION 'runtime credential does not match the signed task token';
    END IF;
    IF v_credential.actor <> p->>'actor' THEN
      RAISE EXCEPTION 'runtime credential actor does not match the signed task token';
    END IF;
    IF v_credential.audience <> COALESCE(p->>'audience', 'sessionbounddb') THEN
      RAISE EXCEPTION 'runtime credential audience does not match the signed task token';
    END IF;
    IF v_credential.revoked OR v_credential.expires_at <= now() THEN
      RAISE EXCEPTION 'runtime credential is expired or revoked';
    END IF;

    SELECT *
    INTO v_binding
    FROM taskbound.task_credential_bindings
    WHERE task_id = v_task_id;

    IF v_binding.task_id IS NOT NULL
       AND (v_binding.credential_id <> v_credential_id OR v_binding.token_digest <> v_token_digest) THEN
      RAISE EXCEPTION 'task token replay or credential rebinding is denied';
    END IF;

  END IF;

  SELECT COALESCE(string_agg((database_object::regclass)::oid::text, ',' ORDER BY view_name), '')
  INTO v_allowed_view_oids
  FROM taskbound.safe_view_registry
  WHERE view_name = ANY(taskbound.jsonb_text_array(p->'allowed_views'));

  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  SELECT locked, lock_backend_pid
  INTO v_lock_acquired, v_lock_backend_pid
  FROM public.dblink(
    v_conn,
    format('SELECT pg_catalog.pg_try_advisory_lock(%s) AS locked, pg_backend_pid() AS lock_backend_pid', v_advisory_lock_key)
  ) AS t(locked boolean, lock_backend_pid int);

  IF NOT v_lock_acquired THEN
    PERFORM taskbound.audit_exec(format(
      'INSERT INTO taskbound.binding_events ' ||
      '(event_type, task_id, token_digest, credential_id, binding_id, fence_token, advisory_lock_key, owner_backend_pid, reason) ' ||
      'VALUES (%L, %L, %L, %L, %L::uuid, %s, %s, %s, %L)',
      'ACTIVE_BINDING_EXISTS',
      v_task_id,
      v_token_digest,
      v_credential_id,
      v_binding_id,
      v_fence_token,
      v_advisory_lock_key,
      pg_backend_pid(),
      'session advisory lock is already held for this BindingKey'
    ));
    RAISE EXCEPTION 'ACTIVE_BINDING_EXISTS'
      USING ERRCODE = '55P03';
  END IF;

  BEGIN
    SELECT status, recovered
    INTO v_claim_status, v_recovered
    FROM public.dblink(
      v_conn,
      format(
        $sql$
        SELECT status, recovered
        FROM taskbound.claim_active_binding_row(
          %L, %L, %L, %L, %L::uuid, %s, %s, %L::oid, %s, %s,
          %L::timestamptz, %L::timestamptz, %L::name, %L::jsonb,
          %L::timestamptz, %L, %L, %L, %L, %L
        )
        $sql$,
        v_task_id,
        v_token_digest,
        v_token_nonce,
        v_credential_id,
        v_binding_id,
        v_fence_token,
        v_advisory_lock_key,
        v_database_oid::text,
        v_lock_backend_pid,
        pg_backend_pid(),
        v_backend_start,
        v_postmaster_start,
        v_session_user,
        p::text,
        (p->>'expires_at')::timestamptz,
        p->>'tenant_id',
        p->>'delegator',
        p->>'actor',
        p->>'purpose',
        v_budget_account
      )
    ) AS t(status text, recovered boolean);

    IF v_claim_status <> 'ok' THEN
      PERFORM public.dblink_exec(
        v_conn,
        format('DO $do$ BEGIN PERFORM pg_catalog.pg_advisory_unlock(%s); END $do$', v_advisory_lock_key)
      );
      v_lock_acquired := false;
      IF v_claim_status IN ('live_owner', 'backend_already_bound', 'conflict') THEN
        RAISE EXCEPTION 'ACTIVE_BINDING_EXISTS'
          USING ERRCODE = '55P03';
      END IF;
      RAISE EXCEPTION 'BINDING_STATE_INCONSISTENT: %', v_claim_status
        USING ERRCODE = '42501';
    END IF;

    PERFORM set_config('search_path', 'taskbound, pg_temp', false);

    PERFORM public.sessionbound_guard_install_binding(
      v_task_id,
      v_budget_account,
      v_allowed_view_oids,
      v_max_queries,
      v_max_rows,
      v_min_group_size,
      v_receipts_enabled,
      v_budget_accounting_enabled,
      v_binding_id::text,
      v_fence_token,
      v_advisory_lock_key,
      v_token_digest,
      v_credential_id,
      (p->>'expires_at')::timestamptz
    );
  EXCEPTION WHEN OTHERS THEN
    PERFORM public.dblink_exec(v_conn, format(
      'DO $do$ BEGIN PERFORM taskbound.release_active_binding_row(%L::uuid, %s, %L); PERFORM pg_catalog.pg_advisory_unlock(%s); END $do$',
      v_binding_id,
      v_fence_token,
      'BINDING_BIND_FAILED',
      v_advisory_lock_key
    ));
    PERFORM public.sessionbound_guard_clear_binding();
    PERFORM set_config('search_path', '"$user", public', false);
    RAISE;
  END;

  RETURN jsonb_build_object(
    'bound', true,
    'task_id', v_task_id,
    'credential_id', NULLIF(v_credential_id, ''),
    'budget_account', v_budget_account,
    'purpose', p->>'purpose',
    'binding_id', v_binding_id,
    'fence_token', v_fence_token,
    'advisory_lock_key', v_advisory_lock_key,
    'stale_recovered', COALESCE(v_recovered, false)
  );
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.unbind_task()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  active record;
  v_backend_start timestamptz;
  v_postmaster_start timestamptz;
  v_conn text := taskbound.lock_connection_name();
  v_connections text[];
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
    PERFORM public.sessionbound_guard_clear_binding();
    RETURN;
  END IF;

  PERFORM taskbound.validate_active_binding(
    active.task_id,
    active.binding_id,
    active.fence_token,
    active.advisory_lock_key
  );

  SELECT public.dblink_get_connections() INTO v_connections;
  IF NOT v_conn = ANY(COALESCE(v_connections, ARRAY[]::text[])) THEN
    PERFORM public.dblink_connect(v_conn, 'dbname=' || current_database());
  END IF;

  PERFORM public.dblink_exec(v_conn, format(
    'DO $do$ BEGIN PERFORM taskbound.release_active_binding_row(%L::uuid, %s, %L); PERFORM pg_catalog.pg_advisory_unlock(%s); END $do$',
    active.binding_id,
    active.fence_token,
    'BINDING_UNBOUND',
    active.advisory_lock_key
  ));

  PERFORM public.sessionbound_guard_clear_binding();
  PERFORM set_config('search_path', '"$user", public', false);
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.claim(path text[])
RETURNS text
LANGUAGE sql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
  SELECT taskbound.require_payload() #>> path
$$;

CREATE OR REPLACE FUNCTION taskbound.jsonb_text_array(value jsonb)
RETURNS text[]
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT COALESCE(array_agg(x), ARRAY[]::text[]) FROM jsonb_array_elements_text(value) AS t(x)
$$;

CREATE OR REPLACE FUNCTION taskbound.safe_view_registry_snapshot(view_names text[])
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_catalog, pg_temp
AS $$
DECLARE
  expected_count int;
  actual_count int;
  snapshot jsonb;
BEGIN
  SELECT cardinality(COALESCE(view_names, ARRAY[]::text[])) INTO expected_count;

  WITH selected_views AS (
    SELECT
      r.view_name,
      r.database_object,
      r.registry_version,
      r.policy_version,
      encode(public.digest(COALESCE(pg_catalog.pg_get_viewdef(r.database_object::regclass, true), ''), 'sha256'), 'hex') AS definition_hash,
      (
        SELECT encode(
          public.digest(
            COALESCE(string_agg(a.attname || ':' || a.atttypid::regtype::text, '|' ORDER BY a.attnum), ''),
            'sha256'
          ),
          'hex'
        )
        FROM pg_catalog.pg_attribute a
        WHERE a.attrelid = r.database_object::regclass
          AND a.attnum > 0
          AND NOT a.attisdropped
      ) AS exposed_column_hash
    FROM taskbound.safe_view_registry r
    WHERE r.view_name = ANY(COALESCE(view_names, ARRAY[]::text[]))
  ),
  ordered_views AS (
    SELECT *
    FROM selected_views
    ORDER BY view_name
  )
  SELECT
    count(*)::int,
    jsonb_build_object(
      'safe_view_registry_version', COALESCE(max(registry_version), 0),
      'view_definition_hash', encode(
        public.digest(COALESCE(string_agg(view_name || ':' || definition_hash, '|' ORDER BY view_name), ''), 'sha256'
      ), 'hex'),
      'exposed_column_hash', encode(
        public.digest(COALESCE(string_agg(view_name || ':' || exposed_column_hash, '|' ORDER BY view_name), ''), 'sha256'
      ), 'hex'),
      'views', COALESCE(
        jsonb_object_agg(
          view_name,
          jsonb_build_object(
            'policy_version', policy_version,
            'registry_version', registry_version,
            'view_definition_hash', definition_hash,
            'exposed_column_hash', exposed_column_hash
          )
        ),
        '{}'::jsonb
      )
    )
  INTO actual_count, snapshot
  FROM ordered_views;

  IF actual_count <> expected_count THEN
    RAISE EXCEPTION 'task token references an unknown safe view';
  END IF;

  RETURN snapshot;
END;
$$;
