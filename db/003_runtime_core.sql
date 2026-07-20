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
  lock_key bigint;
BEGIN
  canonical :=
    'task_id=' || COALESCE(length(v_task_id)::text, '0') || ':' || COALESCE(v_task_id, '') ||
    E'\x1f' ||
    'token_digest=' || COALESCE(length(v_token_digest)::text, '0') || ':' || COALESCE(v_token_digest, '') ||
    E'\x1f' ||
    'credential_id=' || COALESCE(length(v_credential_id)::text, '0') || ':' || COALESCE(v_credential_id, '');
  digest_hex := substr(encode(public.digest(canonical, 'sha256'), 'hex'), 1, 16);
  lock_key := ('x' || digest_hex)::bit(64)::bigint;
  IF lock_key = 0 THEN
    RETURN 1;
  END IF;
  RETURN lock_key;
END;
$$;

CREATE TABLE IF NOT EXISTS taskbound.task_policy_registry (
  task_type text PRIMARY KEY,
  policy_version text NOT NULL,
  template_hash text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO taskbound.task_policy_registry (task_type, policy_version, template_hash)
VALUES
  ('monthly_travel_expense_review', 'travel-demo-v1', ''),
  ('finance_compliance_review', 'finance-review-v1', ''),
  ('payment_readiness_audit', 'payment-readiness-v1', '')
ON CONFLICT (task_type) DO UPDATE
SET policy_version = EXCLUDED.policy_version,
    updated_at = clock_timestamp();

CREATE OR REPLACE FUNCTION taskbound.sha256_hex_equals(expected_hex text, supplied_hex text)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  diff int := 0;
  i int;
BEGIN
  IF length(expected_hex) <> 64 OR length(supplied_hex) <> 64 THEN
    RETURN false;
  END IF;
  IF expected_hex !~ '^[0-9a-f]{64}$' OR supplied_hex !~ '^[0-9A-Fa-f]{64}$' THEN
    RETURN false;
  END IF;
  supplied_hex := lower(supplied_hex);
  FOR i IN 1..64 LOOP
    diff := diff | (
      (position(substr(expected_hex, i, 1) in '0123456789abcdef') - 1)
      # (position(substr(supplied_hex, i, 1) in '0123456789abcdef') - 1)
    );
  END LOOP;
  RETURN diff = 0;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.validate_task_policy_claims(v_payload jsonb)
RETURNS void
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  v_task_type text := NULLIF(v_payload->>'task_type', '');
  v_token_policy_version text := NULLIF(v_payload->>'policy_version', '');
  v_expected_policy_version text;
BEGIN
  IF jsonb_typeof(v_payload) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'task token payload must be a JSON object'
      USING ERRCODE = '42501';
  END IF;
  IF v_task_type IS NULL THEN
    RAISE EXCEPTION 'task token task_type claim is required'
      USING ERRCODE = '42501';
  END IF;
  IF v_token_policy_version IS NULL THEN
    RAISE EXCEPTION 'task token policy_version claim is required'
      USING ERRCODE = '42501';
  END IF;

  SELECT policy_version
  INTO v_expected_policy_version
  FROM taskbound.task_policy_registry
  WHERE task_type = v_task_type;

  IF v_expected_policy_version IS NULL THEN
    RAISE EXCEPTION 'task token task_type is not in the authoritative policy registry'
      USING ERRCODE = '42501';
  END IF;
  IF v_token_policy_version <> v_expected_policy_version THEN
    RAISE EXCEPTION 'task token policy_version is stale; re-approval is required'
      USING ERRCODE = '42501';
  END IF;
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
  task_binding record;
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

    SELECT *
    INTO task_binding
    FROM taskbound.task_credential_bindings b
    WHERE b.task_id = v_task_id
    FOR UPDATE;

    IF NOT FOUND
       OR task_binding.credential_id <> v_credential_id
       OR task_binding.token_digest <> v_token_digest THEN
      DELETE FROM taskbound.active_sessions
      WHERE binding_id = v_binding_id
        AND fence_token = v_fence_token;
      PERFORM taskbound.log_binding_event_local(
        'TASK_CREDENTIAL_BINDING_CONFLICT',
        v_task_id,
        v_token_digest,
        v_credential_id,
        v_binding_id,
        v_fence_token,
        v_advisory_lock_key,
        v_owner_backend_pid,
        'task_id is already bound to a different credential or token'
      );
      status := 'conflict';
      recovered := false;
      RETURN NEXT;
      RETURN;
    END IF;
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
  v_expected_snapshot jsonb;
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

  IF active.token_expires_at <= clock_timestamp() THEN
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
      AND (c.revoked OR c.expires_at <= clock_timestamp() OR c.db_user <> session_user)
  ) THEN
    RAISE EXCEPTION 'runtime credential is expired, revoked, or no longer matches the session'
      USING ERRCODE = '42501';
  END IF;

  PERFORM taskbound.validate_task_policy_claims(active.payload);

  IF jsonb_typeof(active.payload->'allowed_views') IS DISTINCT FROM 'array'
     OR NOT (active.payload ? 'safe_view_registry')
     OR NOT (active.payload ? 'database_oid')
     OR NOT (active.payload ? 'safe_view_registry_version')
     OR NOT (active.payload ? 'view_definition_hash')
     OR NOT (active.payload ? 'exposed_column_hash')
     OR NOT (active.payload ? 'view_dependency_hash')
     OR NOT (active.payload ? 'view_option_hash') THEN
    RAISE EXCEPTION 'task token safe-view registry drift claims are missing from active binding'
      USING ERRCODE = '42501';
  END IF;

  v_expected_snapshot := taskbound.safe_view_registry_snapshot(
    taskbound.jsonb_text_array(active.payload->'allowed_views')
  );
  IF active.payload->'safe_view_registry' <> v_expected_snapshot
     OR active.payload->>'database_oid' <> v_expected_snapshot->>'database_oid'
     OR active.payload->>'safe_view_registry_version' <> v_expected_snapshot->>'safe_view_registry_version'
     OR active.payload->>'view_definition_hash' <> v_expected_snapshot->>'view_definition_hash'
     OR active.payload->>'exposed_column_hash' <> v_expected_snapshot->>'exposed_column_hash'
     OR active.payload->>'view_dependency_hash' <> v_expected_snapshot->>'view_dependency_hash'
     OR active.payload->>'view_option_hash' <> v_expected_snapshot->>'view_option_hash' THEN
    RAISE EXCEPTION 'task token safe-view registry snapshot is stale; re-approval is required'
      USING ERRCODE = '42501';
  END IF;

  -- last_seen_at is diagnostic only; do not update it in the caller
  -- transaction because bind/unbind state is maintained autonomously.
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.validate_active_binding_identity(
  v_task_id text,
  v_binding_id uuid,
  v_fence_token bigint,
  v_advisory_lock_key bigint,
  v_owner_backend_pid int,
  v_owner_backend_start timestamptz,
  v_owner_postmaster_start timestamptz,
  v_owner_session_user name
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  active record;
  v_expected_snapshot jsonb;
BEGIN
  SELECT *
  INTO active
  FROM taskbound.active_sessions a
  WHERE a.task_id = v_task_id
    AND a.binding_id = v_binding_id
    AND a.fence_token = v_fence_token
    AND a.advisory_lock_key = v_advisory_lock_key
    AND a.owner_backend_pid = v_owner_backend_pid
    AND a.owner_backend_start = v_owner_backend_start
    AND a.owner_postmaster_start = v_owner_postmaster_start
    AND a.owner_session_user = v_owner_session_user
    AND a.database_oid = taskbound.current_database_oid();

  IF NOT FOUND THEN
    PERFORM taskbound.log_binding_event_local(
      'BINDING_FENCED',
      v_task_id,
      NULL,
      NULL,
      v_binding_id,
      v_fence_token,
      v_advisory_lock_key,
      v_owner_backend_pid,
      'active binding row does not match supplied owner fence'
    );
    RAISE EXCEPTION 'BINDING_FENCED: active binding fence mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF NOT taskbound.owner_is_live(
    active.database_oid,
    active.owner_backend_pid,
    active.owner_backend_start,
    active.owner_postmaster_start
  ) THEN
    RAISE EXCEPTION 'BINDING_LOST: active binding owner backend is no longer live'
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

  IF active.token_expires_at <= clock_timestamp() THEN
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
      AND (
        c.revoked
        OR c.expires_at <= clock_timestamp()
        OR c.db_user <> active.owner_session_user
      )
  ) THEN
    RAISE EXCEPTION 'runtime credential is expired, revoked, or no longer matches the session'
      USING ERRCODE = '42501';
  END IF;

  PERFORM taskbound.validate_task_policy_claims(active.payload);

  IF jsonb_typeof(active.payload->'allowed_views') IS DISTINCT FROM 'array'
     OR NOT (active.payload ? 'safe_view_registry')
     OR NOT (active.payload ? 'database_oid')
     OR NOT (active.payload ? 'safe_view_registry_version')
     OR NOT (active.payload ? 'view_definition_hash')
     OR NOT (active.payload ? 'exposed_column_hash')
     OR NOT (active.payload ? 'view_dependency_hash')
     OR NOT (active.payload ? 'view_option_hash') THEN
    RAISE EXCEPTION 'task token safe-view registry drift claims are missing from active binding'
      USING ERRCODE = '42501';
  END IF;

  v_expected_snapshot := taskbound.safe_view_registry_snapshot(
    taskbound.jsonb_text_array(active.payload->'allowed_views')
  );
  IF active.payload->'safe_view_registry' <> v_expected_snapshot
     OR active.payload->>'database_oid' <> v_expected_snapshot->>'database_oid'
     OR active.payload->>'safe_view_registry_version' <> v_expected_snapshot->>'safe_view_registry_version'
     OR active.payload->>'view_definition_hash' <> v_expected_snapshot->>'view_definition_hash'
     OR active.payload->>'exposed_column_hash' <> v_expected_snapshot->>'exposed_column_hash'
     OR active.payload->>'view_dependency_hash' <> v_expected_snapshot->>'view_dependency_hash'
     OR active.payload->>'view_option_hash' <> v_expected_snapshot->>'view_option_hash' THEN
    RAISE EXCEPTION 'task token safe-view registry snapshot is stale; re-approval is required'
      USING ERRCODE = '42501';
  END IF;
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
STRICT
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
  v_allowed_views text[];
  v_operations text[];
  v_denied_columns text[];
  v_denied_column_names text;
  v_expected_view_count int;
  v_actual_view_count int;
  v_policy_violation text;
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
  IF btrim(payload_text) = '' THEN
    RAISE EXCEPTION 'task token payload is required';
  END IF;
  IF btrim(signature_hex) = '' THEN
    RAISE EXCEPTION 'task token signature is required';
  END IF;
  IF signature_hex !~ '^[0-9A-Fa-f]{64}$' THEN
    RAISE EXCEPTION 'task token signature must be 64 hex characters';
  END IF;

  p := payload_text::jsonb;
  IF jsonb_typeof(p) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'task token payload must be a JSON object';
  END IF;
  SELECT signing_keys.secret INTO secret
  FROM taskbound.signing_keys
  WHERE key_id = COALESCE(p->>'key_id', 'dev');

  IF secret IS NULL THEN
    RAISE EXCEPTION 'unknown signing key';
  END IF;

  expected := encode(public.hmac(convert_to(payload_text, 'utf8'), convert_to(secret, 'utf8'), 'sha256'), 'hex');
  IF NOT taskbound.sha256_hex_equals(expected, signature_hex) THEN
    RAISE EXCEPTION 'invalid task token signature';
  END IF;

  IF (p->>'expires_at')::timestamptz <= clock_timestamp() THEN
    RAISE EXCEPTION 'task token is expired';
  END IF;

  v_task_id := p->>'task_id';
  v_budget_account := COALESCE(p->>'budget_account', v_task_id);
  v_credential_id := COALESCE(p->>'credential_id', '');
  v_token_digest := encode(public.digest(payload_text, 'sha256'), 'hex');
  v_token_nonce := COALESCE(p->>'nonce', p #>> ARRAY['token', 'nonce'], '');
  IF COALESCE(v_token_nonce, '') = '' THEN
    RAISE EXCEPTION 'task token nonce claim is required';
  END IF;
  IF COALESCE((p #>> ARRAY['runtime_options', 'receipts_enabled'])::boolean, true) IS NOT TRUE THEN
    RAISE EXCEPTION 'task token runtime_options may not disable receipts';
  END IF;
  IF COALESCE((p #>> ARRAY['runtime_options', 'budget_accounting_enabled'])::boolean, true) IS NOT TRUE THEN
    RAISE EXCEPTION 'task token runtime_options may not disable budget accounting';
  END IF;
  v_receipts_enabled := true;
  v_budget_accounting_enabled := true;
  v_max_queries := COALESCE((p #>> ARRAY['budgets', 'max_queries'])::int, 100);
  v_max_rows := COALESCE((p #>> ARRAY['budgets', 'max_unique_expense_rows'])::int, 1000000);
  v_min_group_size := COALESCE((p #>> ARRAY['aggregate_policy', 'min_group_size'])::int, 5);
  IF jsonb_typeof(p->'allowed_views') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'task token allowed_views claim must be a non-empty array';
  END IF;
  v_allowed_views := taskbound.jsonb_text_array(p->'allowed_views');
  IF cardinality(v_allowed_views) = 0 THEN
    RAISE EXCEPTION 'task token allowed_views claim must be a non-empty array';
  END IF;
  IF jsonb_typeof(p->'operations') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'task token operations claim must be a non-empty array';
  END IF;
  v_operations := taskbound.jsonb_text_array(p->'operations');
  IF NOT EXISTS (
    SELECT 1
    FROM unnest(v_operations) AS operation_item(op)
    WHERE upper(trim(op)) = 'SELECT'
  ) THEN
    RAISE EXCEPTION 'task token operations claim must permit SELECT for safe-view binding';
  END IF;
  IF p ? 'denied_columns'
     AND jsonb_typeof(p->'denied_columns') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'task token denied_columns claim must be an array when present';
  END IF;
  v_denied_columns := taskbound.jsonb_text_array(COALESCE(p->'denied_columns', '[]'::jsonb));
  SELECT COALESCE(string_agg(lower(trim(d)), ',' ORDER BY lower(trim(d))), '')
  INTO v_denied_column_names
  FROM unnest(v_denied_columns) AS denied(d)
  WHERE NULLIF(trim(d), '') IS NOT NULL;
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

  IF COALESCE(v_task_id, '') = '' THEN
    RAISE EXCEPTION 'task token task_id claim is required';
  END IF;
  PERFORM taskbound.validate_task_policy_claims(p);
  IF COALESCE(p->>'tenant_id', '') = '' THEN
    RAISE EXCEPTION 'task token tenant_id claim is required';
  END IF;
  IF COALESCE(p->>'delegator', '') = '' THEN
    RAISE EXCEPTION 'task token delegator claim is required';
  END IF;
  IF COALESCE(p->>'actor', '') = '' THEN
    RAISE EXCEPTION 'task token actor claim is required';
  END IF;
  IF COALESCE(p->>'purpose', '') = '' THEN
    RAISE EXCEPTION 'task token purpose claim is required';
  END IF;
  IF COALESCE(v_credential_id, '') = '' THEN
    RAISE EXCEPTION 'task token credential_id claim is required';
  END IF;
  IF jsonb_typeof(p->'row_scope') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'task token row_scope claim must be an object';
  END IF;
  IF COALESCE(p #>> ARRAY['row_scope', 'expense_month'], '') = '' THEN
    RAISE EXCEPTION 'task token row_scope.expense_month claim is required';
  END IF;

  SELECT cardinality(v_allowed_views) INTO v_expected_view_count;
  SELECT count(*)::int
  INTO v_actual_view_count
  FROM taskbound.safe_view_registry
  WHERE view_name = ANY(v_allowed_views);
  IF v_actual_view_count <> v_expected_view_count THEN
    RAISE EXCEPTION 'task token references an unknown safe view';
  END IF;

  WITH token_scope AS (
    SELECT 'tenant_id'::text AS scope_key
    UNION
    SELECT key
    FROM jsonb_each_text(p->'row_scope') AS scope_item(key, value)
    WHERE NULLIF(value, '') IS NOT NULL
  ),
  missing AS (
    SELECT r.view_name, token_scope.scope_key
    FROM taskbound.safe_view_registry r
    CROSS JOIN token_scope
    WHERE r.view_name = ANY(v_allowed_views)
      AND NOT token_scope.scope_key = ANY(r.scope_fields)
  )
  SELECT string_agg(view_name || ':' || scope_key, ', ' ORDER BY view_name, scope_key)
  INTO v_policy_violation
  FROM missing;
  IF v_policy_violation IS NOT NULL THEN
    RAISE EXCEPTION 'approved safe view does not enforce required task scope: %', v_policy_violation;
  END IF;

  WITH denied AS (
    SELECT lower(trim(d)) AS raw
    FROM unnest(v_denied_columns) AS denied_item(d)
    WHERE NULLIF(trim(d), '') IS NOT NULL
  ),
  parsed AS (
    SELECT
      raw,
      parts[cardinality(parts)] AS column_name,
      CASE WHEN cardinality(parts) > 1 THEN parts[cardinality(parts) - 1] ELSE NULL END AS qualifier
    FROM denied
    CROSS JOIN LATERAL (SELECT string_to_array(raw, '.') AS parts) AS p
  ),
  exposed AS (
    SELECT r.view_name, a.attname
    FROM taskbound.safe_view_registry r
    JOIN pg_catalog.pg_attribute a
      ON a.attrelid = r.database_object::regclass
     AND a.attnum > 0
     AND NOT a.attisdropped
    JOIN parsed d
      ON lower(a.attname) = d.column_name
     AND (
       d.qualifier IS NULL
       OR d.qualifier = lower(r.view_name)
       OR d.qualifier = lower(split_part(r.database_object, '.', 2))
       OR d.qualifier = lower(r.database_object)
     )
    WHERE r.view_name = ANY(v_allowed_views)
  )
  SELECT string_agg(view_name || '.' || attname, ', ' ORDER BY view_name, attname)
  INTO v_policy_violation
  FROM exposed;
  IF v_policy_violation IS NOT NULL THEN
    RAISE EXCEPTION 'task token denied column remains exposed by an approved safe view: %', v_policy_violation;
  END IF;

  IF NOT (p ? 'safe_view_registry')
     OR NOT (p ? 'database_oid')
     OR NOT (p ? 'safe_view_registry_version')
     OR NOT (p ? 'view_definition_hash')
     OR NOT (p ? 'exposed_column_hash')
     OR NOT (p ? 'view_dependency_hash')
     OR NOT (p ? 'view_option_hash') THEN
    RAISE EXCEPTION 'task token must carry safe-view registry drift claims';
  END IF;

  v_expected_snapshot := taskbound.safe_view_registry_snapshot(v_allowed_views);
  IF p->'safe_view_registry' <> v_expected_snapshot THEN
    RAISE EXCEPTION 'task token safe-view registry snapshot is stale; re-approval is required';
  END IF;
  IF p->>'database_oid' <> v_expected_snapshot->>'database_oid' THEN
    RAISE EXCEPTION 'task token database identity is stale; re-approval is required';
  END IF;
  IF p->>'safe_view_registry_version' <> v_expected_snapshot->>'safe_view_registry_version' THEN
    RAISE EXCEPTION 'task token safe-view registry version is stale; re-approval is required';
  END IF;
  IF p->>'view_definition_hash' <> v_expected_snapshot->>'view_definition_hash' THEN
    RAISE EXCEPTION 'task token safe-view definition hash is stale; re-approval is required';
  END IF;
  IF p->>'exposed_column_hash' <> v_expected_snapshot->>'exposed_column_hash' THEN
    RAISE EXCEPTION 'task token exposed-column hash is stale; re-approval is required';
  END IF;
  IF p->>'view_dependency_hash' <> v_expected_snapshot->>'view_dependency_hash' THEN
    RAISE EXCEPTION 'task token safe-view dependency hash is stale; re-approval is required';
  END IF;
  IF p->>'view_option_hash' <> v_expected_snapshot->>'view_option_hash' THEN
    RAISE EXCEPTION 'task token safe-view option hash is stale; re-approval is required';
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

  BEGIN
    EXECUTE 'CLOSE ALL';
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  BEGIN
    EXECUTE 'DEALLOCATE ALL';
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;

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
    IF v_credential.revoked OR v_credential.expires_at <= clock_timestamp() THEN
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
  WHERE view_name = ANY(v_allowed_views);

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

    PERFORM set_config('search_path', 'taskbound, pg_catalog', false);

    PERFORM public.sessionbound_guard_install_binding(
      v_task_id,
      v_budget_account,
      v_allowed_view_oids,
      v_denied_column_names,
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
    BEGIN
      EXECUTE 'CLOSE ALL';
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;
    BEGIN
      EXECUTE 'DEALLOCATE ALL';
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;
    PERFORM public.sessionbound_guard_clear_binding();
    RETURN;
  END IF;

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
  BEGIN
    EXECUTE 'CLOSE ALL';
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  BEGIN
    EXECUTE 'DEALLOCATE ALL';
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
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
      taskbound.current_database_oid()::text AS database_oid,
      c.oid::text AS view_oid,
      n.nspname AS view_schema,
      c.relname AS view_relname,
      opts.reloptions_json,
      opts.reloptions_text,
      r.registry_version,
      r.policy_version,
      r.scope_fields,
      r.sensitive_fields_excluded,
      COALESCE(pg_catalog.pg_get_viewdef(c.oid, true), '') AS view_sql,
      cols.exposed_column_hash,
      deps.dependency_hash,
      deps.dependencies,
      encode(public.digest(
        concat_ws(chr(31),
          taskbound.current_database_oid()::text,
          c.oid::text,
          n.nspname,
          c.relname,
          c.relkind::text,
          opts.reloptions_text,
          COALESCE(pg_catalog.pg_get_viewdef(c.oid, true), ''),
          cols.exposed_column_hash,
          deps.dependency_hash
        ),
        'sha256'
      ), 'hex') AS definition_hash
    FROM taskbound.safe_view_registry r
    JOIN pg_catalog.pg_class c
      ON c.oid = r.database_object::regclass
    JOIN pg_catalog.pg_namespace n
      ON n.oid = c.relnamespace
    LEFT JOIN LATERAL (
      SELECT
        COALESCE(jsonb_agg(relopt.opt ORDER BY relopt.opt), '[]'::jsonb) AS reloptions_json,
        COALESCE(string_agg(relopt.opt, ',' ORDER BY relopt.opt), '') AS reloptions_text
      FROM unnest(c.reloptions) AS relopt(opt)
    ) opts ON true
    LEFT JOIN LATERAL (
      SELECT encode(
        public.digest(
          COALESCE(string_agg(a.attname || ':' || a.atttypid::regtype::text, '|' ORDER BY a.attnum), ''),
          'sha256'
        ),
        'hex'
      ) AS exposed_column_hash
      FROM pg_catalog.pg_attribute a
      WHERE a.attrelid = c.oid
        AND a.attnum > 0
        AND NOT a.attisdropped
    ) cols ON true
    LEFT JOIN LATERAL (
      SELECT
        encode(
          public.digest(
            COALESCE(
              string_agg(
                dep.depth::text || ':' ||
                dep.refclassid::regclass::text || ':' ||
                dep.refobjid::text || ':' ||
                dep.refobjsubid::text || ':' ||
                dep.deptype::text || ':' ||
                pg_catalog.pg_describe_object(dep.refclassid, dep.refobjid, dep.refobjsubid) || ':' ||
                COALESCE(
                  CASE WHEN dep.refclassid = 'pg_proc'::regclass THEN
                    proc.pronamespace::regnamespace::text || ':' ||
                    proc.proowner::regrole::text || ':' ||
                    proc.prolang::text || ':' ||
                    proc.prokind::text || ':' ||
                    proc.prosecdef::text || ':' ||
                    proc.proleakproof::text || ':' ||
                    proc.provolatile::text || ':' ||
                    proc.proparallel::text || ':' ||
                    COALESCE(proc.proconfig::text, '') || ':' ||
                    COALESCE(proc.proacl::text, '') || ':' ||
                    COALESCE(proc.probin, '') || ':' ||
                    COALESCE(proc.prosqlbody::text, '') || ':' ||
                    COALESCE(proc.prosrc, '')
                  WHEN dep.refclassid = 'pg_class'::regclass THEN
                    rel.relnamespace::regnamespace::text || ':' ||
                    rel.relname || ':' ||
                    rel.relkind::text || ':' ||
                    rel.relowner::regrole::text || ':' ||
                    rel.relrowsecurity::text || ':' ||
                    rel.relforcerowsecurity::text || ':' ||
                    COALESCE(rel.relacl::text, '') || ':' ||
                    COALESCE(rel.reloptions::text, '') || ':' ||
                    COALESCE(pg_catalog.pg_get_viewdef(rel.oid, true), '') || ':' ||
                    COALESCE(policy.policy_text, '')
                  END,
                  ''
                ),
                '|' ORDER BY dep.depth, dep.refclassid::regclass::text, dep.refobjid, dep.refobjsubid, dep.deptype
              ),
              ''
            ),
            'sha256'
          ),
          'hex'
        ) AS dependency_hash,
        COALESCE(
          jsonb_agg(
            jsonb_build_object(
              'depth', dep.depth,
              'refclass', dep.refclassid::regclass::text,
              'refobjid', dep.refobjid::text,
              'refobjsubid', dep.refobjsubid,
              'deptype', dep.deptype::text,
              'description', pg_catalog.pg_describe_object(dep.refclassid, dep.refobjid, dep.refobjsubid)
            )
            ORDER BY dep.refclassid::regclass::text, dep.refobjid, dep.refobjsubid, dep.deptype
          ) FILTER (WHERE dep.refobjid IS NOT NULL),
          '[]'::jsonb
        ) AS dependencies
      FROM (
        WITH RECURSIVE dependency_edges AS (
          SELECT
            1 AS depth,
            dep.refclassid,
            dep.refobjid,
            dep.refobjsubid,
            dep.deptype,
            ARRAY[
              dep.refclassid::oid::text || ':' || dep.refobjid::text || ':' || dep.refobjsubid::text
            ] AS path
          FROM pg_catalog.pg_rewrite rw
          JOIN pg_catalog.pg_depend dep
            ON dep.classid = 'pg_rewrite'::regclass
           AND dep.objid = rw.oid
           AND dep.deptype IN ('n', 'a')
          WHERE rw.ev_class = c.oid
            AND NOT (
              dep.refclassid = 'pg_class'::regclass
              AND dep.refobjid = c.oid
            )

          UNION ALL

          SELECT
            edge.depth + 1,
            next_dep.refclassid,
            next_dep.refobjid,
            next_dep.refobjsubid,
            next_dep.deptype,
            edge.path || (
              next_dep.refclassid::oid::text || ':' || next_dep.refobjid::text || ':' || next_dep.refobjsubid::text
            )
          FROM dependency_edges edge
          JOIN LATERAL (
            SELECT dep.refclassid, dep.refobjid, dep.refobjsubid, dep.deptype
            FROM pg_catalog.pg_depend dep
            WHERE dep.classid = edge.refclassid
              AND dep.objid = edge.refobjid
              AND dep.deptype IN ('n', 'a')

            UNION ALL

            SELECT dep.refclassid, dep.refobjid, dep.refobjsubid, dep.deptype
            FROM pg_catalog.pg_rewrite rw
            JOIN pg_catalog.pg_depend dep
              ON dep.classid = 'pg_rewrite'::regclass
             AND dep.objid = rw.oid
             AND dep.deptype IN ('n', 'a')
            WHERE edge.refclassid = 'pg_class'::regclass
              AND rw.ev_class = edge.refobjid
          ) next_dep ON true
          WHERE edge.depth < 8
            AND NOT (
              next_dep.refclassid = 'pg_class'::regclass
              AND next_dep.refobjid = c.oid
            )
            AND NOT (
              next_dep.refclassid::oid::text || ':' || next_dep.refobjid::text || ':' || next_dep.refobjsubid::text
            ) = ANY(edge.path)
        )
        SELECT DISTINCT ON (refclassid, refobjid, refobjsubid, deptype)
          depth, refclassid, refobjid, refobjsubid, deptype
        FROM dependency_edges
        ORDER BY refclassid, refobjid, refobjsubid, deptype, depth
      ) dep
      LEFT JOIN pg_catalog.pg_proc proc
        ON dep.refclassid = 'pg_proc'::regclass
       AND proc.oid = dep.refobjid
      LEFT JOIN pg_catalog.pg_class rel
        ON dep.refclassid = 'pg_class'::regclass
       AND rel.oid = dep.refobjid
      LEFT JOIN LATERAL (
        SELECT string_agg(
          pol.polname || ':' ||
          pol.polcmd::text || ':' ||
          pol.polpermissive::text || ':' ||
          COALESCE(pol.polroles::text, '') || ':' ||
          COALESCE(pg_catalog.pg_get_expr(pol.polqual, pol.polrelid), '') || ':' ||
          COALESCE(pg_catalog.pg_get_expr(pol.polwithcheck, pol.polrelid), ''),
          '|' ORDER BY pol.polname
        ) AS policy_text
        FROM pg_catalog.pg_policy pol
        WHERE rel.oid IS NOT NULL
          AND pol.polrelid = rel.oid
      ) policy ON true
    ) deps ON true
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
      'database_oid', taskbound.current_database_oid()::text,
      'safe_view_registry_version', COALESCE(max(registry_version), 0),
      'view_definition_hash', encode(
        public.digest(COALESCE(string_agg(view_name || ':' || definition_hash, '|' ORDER BY view_name), ''), 'sha256'
      ), 'hex'),
      'exposed_column_hash', encode(
        public.digest(COALESCE(string_agg(view_name || ':' || exposed_column_hash, '|' ORDER BY view_name), ''), 'sha256'
      ), 'hex'),
      'view_dependency_hash', encode(
        public.digest(COALESCE(string_agg(view_name || ':' || dependency_hash, '|' ORDER BY view_name), ''), 'sha256'
      ), 'hex'),
      'view_option_hash', encode(
        public.digest(COALESCE(string_agg(view_name || ':' || reloptions_text, '|' ORDER BY view_name), ''), 'sha256'
      ), 'hex'),
      'views', COALESCE(
        jsonb_object_agg(
          view_name,
          jsonb_build_object(
            'database_object', database_object,
            'database_oid', database_oid,
            'view_oid', view_oid,
            'view_schema', view_schema,
            'view_relname', view_relname,
            'policy_version', policy_version,
            'registry_version', registry_version,
            'scope_fields', scope_fields,
            'sensitive_fields_excluded', sensitive_fields_excluded,
            'view_definition_hash', definition_hash,
            'exposed_column_hash', exposed_column_hash,
            'view_dependency_hash', dependency_hash,
            'view_options', reloptions_json,
            'dependencies', dependencies
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
