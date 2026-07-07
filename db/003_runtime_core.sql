CREATE OR REPLACE FUNCTION taskbound.current_payload()
RETURNS jsonb
LANGUAGE sql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
  SELECT payload
  FROM taskbound.active_sessions
  WHERE backend_pid = pg_backend_pid()
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
  v_credential_id := p->>'credential_id';
  v_token_digest := encode(public.digest(payload_text, 'sha256'), 'hex');
  v_session_user := session_user;
  SELECT backend_start INTO v_backend_start
  FROM pg_catalog.pg_stat_activity
  WHERE pid = pg_backend_pid();

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
  END IF;

  DELETE FROM taskbound.active_sessions a
  WHERE NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_stat_activity s
    WHERE s.pid = a.backend_pid
      AND s.backend_start = a.backend_start
  );

  SELECT task_id, credential_id INTO v_existing_task, v_existing_credential
  FROM taskbound.active_sessions
  WHERE backend_pid = pg_backend_pid();

  IF v_existing_task IS NOT NULL AND v_existing_task <> v_task_id THEN
    RAISE EXCEPTION 'database session is already bound to active task %', v_existing_task;
  END IF;

  IF v_credential_id IS NOT NULL THEN
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

    INSERT INTO taskbound.task_credential_bindings (
      task_id, credential_id, token_digest, first_backend_pid, session_user_name
    )
    VALUES (
      v_task_id, v_credential_id, v_token_digest, pg_backend_pid(), v_session_user
    )
    ON CONFLICT (task_id) DO NOTHING;
  END IF;

  INSERT INTO taskbound.task_execution_state (
    task_id, tenant_id, delegator, actor, purpose, budget_account, expires_at
  )
  VALUES (
    v_task_id,
    p->>'tenant_id',
    p->>'delegator',
    p->>'actor',
    p->>'purpose',
    v_budget_account,
    (p->>'expires_at')::timestamptz
  )
  ON CONFLICT ON CONSTRAINT task_execution_state_pkey DO NOTHING;

  INSERT INTO taskbound.active_sessions (backend_pid, backend_start, task_id, credential_id, payload)
  VALUES (pg_backend_pid(), v_backend_start, v_task_id, v_credential_id, p)
  ON CONFLICT (backend_pid) DO UPDATE
    SET backend_start = EXCLUDED.backend_start,
        task_id = EXCLUDED.task_id,
        credential_id = EXCLUDED.credential_id,
        payload = EXCLUDED.payload,
        bound_at = now();

  SELECT COALESCE(string_agg((database_object::regclass)::oid::text, ',' ORDER BY view_name), '')
  INTO v_allowed_view_oids
  FROM taskbound.safe_view_registry
  WHERE view_name = ANY(taskbound.jsonb_text_array(p->'allowed_views'));

  PERFORM set_config('sessionbound_guard.task_bound', 'on', false);
  PERFORM set_config('sessionbound_guard.task_id', v_task_id, false);
  PERFORM set_config('sessionbound_guard.allowed_view_oids', v_allowed_view_oids, false);
  PERFORM set_config('sessionbound_guard.enabled', 'off', false);

  RETURN jsonb_build_object(
    'bound', true,
    'task_id', v_task_id,
    'credential_id', v_credential_id,
    'budget_account', v_budget_account,
    'purpose', p->>'purpose'
  );
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
      encode(public.digest(COALESCE(pg_catalog.pg_get_viewdef(r.database_object::regclass, true), ''), 'sha256'), 'hex') AS definition_hash
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
      'views', COALESCE(
        jsonb_object_agg(
          view_name,
          jsonb_build_object(
            'policy_version', policy_version,
            'registry_version', registry_version,
            'view_definition_hash', definition_hash
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
