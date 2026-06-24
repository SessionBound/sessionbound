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

  INSERT INTO taskbound.active_sessions (backend_pid, task_id, payload)
  VALUES (pg_backend_pid(), v_task_id, p)
  ON CONFLICT (backend_pid) DO UPDATE
    SET task_id = EXCLUDED.task_id,
        payload = EXCLUDED.payload,
        bound_at = now();

  RETURN jsonb_build_object(
    'bound', true,
    'task_id', v_task_id,
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
