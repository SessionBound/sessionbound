CREATE OR REPLACE FUNCTION taskbound.command_touched_views(command_name text)
RETURNS text[]
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN command_name = 'pay_expense'
      THEN ARRAY['expenses', 'approval_events', 'ledger_entries']::text[]
    WHEN command_name = 'submit_expense'
      THEN ARRAY['expenses', 'approval_events']::text[]
    ELSE ARRAY['expenses', 'approval_events']::text[]
  END
$$;

CREATE OR REPLACE FUNCTION taskbound.command_digest(command_name text, args jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT encode(
    public.digest(
      concat_ws(chr(31), 'CONTROLLED_COMMAND', COALESCE(command_name, ''), COALESCE(args, '{}'::jsonb)::text),
      'sha256'
    ),
    'hex'
  )
$$;

DROP FUNCTION IF EXISTS taskbound.command_allowed_receipt(jsonb, text, jsonb, jsonb, text[]);

CREATE OR REPLACE FUNCTION taskbound.command_connection_name()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT 'sessionbound_command_' || pg_backend_pid()::text
$$;

CREATE OR REPLACE FUNCTION taskbound.command_allowed_receipt_for_binding(
  p jsonb,
  command_name text,
  args jsonb,
  result jsonb,
  v_touched_views text[] DEFAULT ARRAY[]::text[],
  v_binding_id uuid DEFAULT NULL,
  v_fence_token bigint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  active record;
  v_task_id text := COALESCE(p->>'task_id', '');
  v_budget_account text := COALESCE(p->>'budget_account', p->>'task_id', '');
  v_execution_id uuid := gen_random_uuid();
  v_receipt_id uuid := gen_random_uuid();
  v_receipt_sequence bigint;
  v_query_digest text := taskbound.command_digest(command_name, args);
  v_created_at timestamptz := clock_timestamp();
  v_previous_hash text := '';
  v_receipt_hash text;
  v_remaining_unique_row_budget bigint;
  v_actor text := COALESCE(p->>'actor', '');
  v_reason text := 'controlled command allowed: ' || COALESCE(command_name, '');
BEGIN
  IF v_task_id = '' THEN
    RETURN result;
  END IF;

  SELECT *
  INTO active
  FROM taskbound.active_sessions a
  WHERE a.task_id = v_task_id
    AND a.binding_id = v_binding_id
    AND a.fence_token = v_fence_token;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: active binding is missing';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(v_task_id, 0));

  INSERT INTO taskbound.task_receipt_chain_heads (
    task_id, last_sequence, last_receipt_hash
  )
  VALUES (v_task_id, 0, '')
  ON CONFLICT (task_id) DO UPDATE
    SET last_sequence = taskbound.task_receipt_chain_heads.last_sequence
  RETURNING last_sequence + 1, last_receipt_hash
  INTO v_receipt_sequence, v_previous_hash;

  SELECT GREATEST(
    COALESCE((p #>> ARRAY['budgets', 'max_unique_expense_rows'])::bigint, 0)
    - COALESCE(s.unique_expense_rows, 0),
    0
  )
  INTO v_remaining_unique_row_budget
  FROM taskbound.task_execution_state s
  WHERE s.task_id = v_task_id;

  v_receipt_hash := taskbound.receipt_hash(
    v_receipt_id,
    v_execution_id,
    v_receipt_sequence,
    v_task_id,
    v_budget_account,
    active.binding_id,
    active.fence_token,
    v_query_digest,
    'allowed',
    0,
    0,
    v_remaining_unique_row_budget,
    v_reason,
    v_actor,
    v_touched_views,
    v_created_at,
    v_previous_hash
  );

  INSERT INTO taskbound.task_query_receipts (
    receipt_id, execution_id, receipt_sequence, task_id, budget_account,
    binding_id, fence_token, query_digest, decision, rows_returned, unique_rows_added,
    remaining_unique_row_budget, reason, actor, touched_views,
    previous_receipt_hash, receipt_hash, created_at
  )
  VALUES (
    v_receipt_id, v_execution_id, v_receipt_sequence, v_task_id, v_budget_account,
    active.binding_id, active.fence_token,
    v_query_digest, 'allowed', 0, 0,
    v_remaining_unique_row_budget, v_reason, v_actor,
    COALESCE(v_touched_views, ARRAY[]::text[]),
    v_previous_hash, v_receipt_hash, v_created_at
  );

  UPDATE taskbound.task_receipt_chain_heads
  SET last_sequence = v_receipt_sequence,
      last_receipt_hash = v_receipt_hash,
      updated_at = clock_timestamp()
  WHERE task_id = v_task_id;

  RETURN COALESCE(result, '{}'::jsonb) || jsonb_build_object(
    'receipt_id', v_receipt_id,
    'execution_id', v_execution_id,
    'receipt_sequence', v_receipt_sequence,
    'receipt_hash', v_receipt_hash
  );
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.command_denied_receipt(
  p jsonb,
  command_name text,
  args jsonb,
  reason text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  active record;
  v_task_id text := COALESCE(p->>'task_id', '');
  v_budget_account text := COALESCE(p->>'budget_account', p->>'task_id', '');
  v_remaining_unique_row_budget bigint;
  v_backend_start timestamptz;
  v_postmaster_start timestamptz;
BEGIN
  IF v_task_id = '' THEN
    RETURN;
  END IF;

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

  SELECT GREATEST(
    COALESCE((p #>> ARRAY['budgets', 'max_unique_expense_rows'])::bigint, 0)
    - COALESCE(s.unique_expense_rows, 0),
    0
  )
  INTO v_remaining_unique_row_budget
  FROM taskbound.task_execution_state s
  WHERE s.task_id = v_task_id;

  PERFORM taskbound.audit_append_receipt(
    v_task_id,
    v_budget_account,
    active.binding_id,
    active.fence_token,
    taskbound.command_digest(command_name, args),
    'denied',
    0,
    0,
    v_remaining_unique_row_budget,
    COALESCE(reason, 'controlled command denied'),
    true,
    NULL,
    COALESCE(p->>'actor', ''),
    taskbound.command_touched_views(command_name)
  );
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.command_denied_receipt_for_binding(
  p jsonb,
  command_name text,
  args jsonb,
  reason text,
  v_binding_id uuid DEFAULT NULL,
  v_fence_token bigint DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  active record;
  v_task_id text := COALESCE(p->>'task_id', '');
  v_budget_account text := COALESCE(p->>'budget_account', p->>'task_id', '');
  v_execution_id uuid := gen_random_uuid();
  v_receipt_id uuid := gen_random_uuid();
  v_receipt_sequence bigint;
  v_query_digest text := taskbound.command_digest(command_name, args);
  v_created_at timestamptz := clock_timestamp();
  v_previous_hash text := '';
  v_receipt_hash text;
  v_remaining_unique_row_budget bigint;
  v_actor text := COALESCE(p->>'actor', '');
  v_reason text := COALESCE(reason, 'controlled command denied');
BEGIN
  IF v_task_id = '' THEN
    RETURN;
  END IF;

  SELECT *
  INTO active
  FROM taskbound.active_sessions a
  WHERE a.task_id = v_task_id
    AND a.binding_id = v_binding_id
    AND a.fence_token = v_fence_token;

  IF NOT FOUND THEN
    RETURN;
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(v_task_id, 0));

  INSERT INTO taskbound.task_receipt_chain_heads (
    task_id, last_sequence, last_receipt_hash
  )
  VALUES (v_task_id, 0, '')
  ON CONFLICT (task_id) DO UPDATE
    SET last_sequence = taskbound.task_receipt_chain_heads.last_sequence
  RETURNING last_sequence + 1, last_receipt_hash
  INTO v_receipt_sequence, v_previous_hash;

  SELECT GREATEST(
    COALESCE((p #>> ARRAY['budgets', 'max_unique_expense_rows'])::bigint, 0)
    - COALESCE(s.unique_expense_rows, 0),
    0
  )
  INTO v_remaining_unique_row_budget
  FROM taskbound.task_execution_state s
  WHERE s.task_id = v_task_id;

  v_receipt_hash := taskbound.receipt_hash(
    v_receipt_id,
    v_execution_id,
    v_receipt_sequence,
    v_task_id,
    v_budget_account,
    active.binding_id,
    active.fence_token,
    v_query_digest,
    'denied',
    0,
    0,
    v_remaining_unique_row_budget,
    v_reason,
    v_actor,
    taskbound.command_touched_views(command_name),
    v_created_at,
    v_previous_hash
  );

  INSERT INTO taskbound.task_query_receipts (
    receipt_id, execution_id, receipt_sequence, task_id, budget_account,
    binding_id, fence_token, query_digest, decision, rows_returned, unique_rows_added,
    remaining_unique_row_budget, reason, actor, touched_views,
    previous_receipt_hash, receipt_hash, created_at
  )
  VALUES (
    v_receipt_id, v_execution_id, v_receipt_sequence, v_task_id, v_budget_account,
    active.binding_id, active.fence_token,
    v_query_digest, 'denied', 0, 0,
    v_remaining_unique_row_budget, v_reason, v_actor,
    taskbound.command_touched_views(command_name),
    v_previous_hash, v_receipt_hash, v_created_at
  );

  UPDATE taskbound.task_receipt_chain_heads
  SET last_sequence = v_receipt_sequence,
      last_receipt_hash = v_receipt_hash,
      updated_at = clock_timestamp()
  WHERE task_id = v_task_id;
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.command_apply(
  p jsonb,
  command_name text,
  args jsonb,
  v_binding_id uuid,
  v_fence_token bigint,
  v_advisory_lock_key bigint,
  v_owner_backend_pid int,
  v_owner_backend_start timestamptz,
  v_owner_postmaster_start timestamptz,
  v_owner_session_user name
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  v_task_id text;
  v_tenant text;
  v_delegator text;
  v_actor text;
  v_expense_id text;
  v_comment text;
  v_supervisor text;
  exp record;
  v_monthly_total numeric(12,2);
  v_yearly_total numeric(12,2);
  v_requires_c_level boolean;
  v_next_status text;
  v_new_expense_id text;
  v_transition_rows int;
  v_ledger_id uuid;
  emp record;
  v_scope_month text;
  v_scope_department text;
  v_command_created_at timestamptz;
BEGIN
  v_task_id := p->>'task_id';
  v_tenant := p->>'tenant_id';
  v_delegator := p->>'delegator';
  v_actor := p->>'actor';
  v_expense_id := args->>'expense_id';
  v_comment := COALESCE(args->>'comment', '');
  v_supervisor := args->>'supervisor_agent_id';
  v_scope_month := p #>> ARRAY['row_scope', 'expense_month'];
  v_scope_department := NULLIF(p #>> ARRAY['row_scope', 'department_id'], '');

  PERFORM taskbound.validate_active_binding_identity(
    v_task_id,
    v_binding_id,
    v_fence_token,
    v_advisory_lock_key,
    v_owner_backend_pid,
    v_owner_backend_start,
    v_owner_postmaster_start,
    v_owner_session_user
  );

  IF v_scope_month IS NULL THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: task token month scope is missing';
  END IF;
  v_command_created_at := clock_timestamp();

  IF NOT COALESCE(p->'allowed_commands' ? command_name, false) THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: command % is not allowed by task token', command_name;
  END IF;
  IF jsonb_typeof(p->'operations') IS DISTINCT FROM 'array'
     OR NOT EXISTS (
       SELECT 1
       FROM jsonb_array_elements_text(p->'operations') AS operation_item(op)
       WHERE upper(trim(op)) = 'CONTROLLED_COMMAND'
     ) THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: task token operations claim must permit CONTROLLED_COMMAND';
  END IF;

  IF command_name = 'submit_expense' THEN
    IF v_delegator <> 'user:eve' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only employee user:eve can submit a new expense in this demo';
    END IF;

    SELECT e.* INTO emp
    FROM app_data.employees e
    WHERE e.tenant_id = v_tenant
      AND e.employee_id = 'emp_005';

    IF emp.employee_id IS NULL THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: employee profile is not available';
    END IF;
    IF v_scope_department IS NOT NULL AND emp.department_id <> v_scope_department THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: employee profile is outside task scope';
    END IF;

    IF (args->>'amount') IS NULL OR (args->>'amount')::numeric <= 0 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: amount must be greater than zero';
    END IF;
    IF COALESCE(args->>'category', '') = '' OR COALESCE(args->>'merchant', '') = '' OR COALESCE(args->>'city', '') = '' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: category, merchant, and city are required';
    END IF;
    IF args ? 'expense_month'
       AND args->>'expense_month' <> v_scope_month THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: expense month is outside task scope';
    END IF;

    SELECT 'exp_' || lpad((COALESCE(max(substring(expense_id from 5)::int), 0) + 1)::text, 3, '0')
    INTO v_new_expense_id
    FROM app_data.expenses
    WHERE expense_id ~ '^exp_[0-9]+$';

    INSERT INTO app_data.expenses (
      expense_id, tenant_id, employee_id, department_id, expense_month,
      category, merchant, city, amount, submitted_at, status
    )
    VALUES (
      v_new_expense_id,
      v_tenant,
      emp.employee_id,
      emp.department_id,
      v_scope_month,
      args->>'category',
      args->>'merchant',
      args->>'city',
      (args->>'amount')::numeric,
      v_command_created_at,
      'submitted'
    );

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, v_new_expense_id, 'submitted', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', v_new_expense_id,
      'new_status', 'submitted',
      'next_role', 'finance_reviewer',
      'next_task_type', 'finance_compliance_review',
      'receipt', 'expense submitted; finance review todo enabled'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  END IF;

  SELECT e.*, d.manager_user_id INTO exp
  FROM app_data.expenses e
  JOIN app_data.departments d ON d.department_id = e.department_id
  WHERE e.expense_id = v_expense_id
    AND e.tenant_id = v_tenant
    AND e.expense_month = v_scope_month
    AND (
      v_scope_department IS NULL
      OR e.department_id = v_scope_department
    )
  FOR UPDATE OF e;

  IF exp.expense_id IS NULL THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: expense is outside task scope';
  END IF;

  -- Match taskbound.expenses: totals are computed after task row-scope
  -- filtering, so command responses do not expose out-of-scope aggregates.
  SELECT COALESCE(sum(amount), 0) INTO v_monthly_total
  FROM app_data.expenses
  WHERE tenant_id = v_tenant
    AND employee_id = exp.employee_id
    AND expense_month = v_scope_month
    AND (
      v_scope_department IS NULL
      OR department_id = v_scope_department
    )
    AND date_trunc('month', submitted_at) = date_trunc('month', exp.submitted_at);

  SELECT COALESCE(sum(amount), 0) INTO v_yearly_total
  FROM app_data.expenses
  WHERE tenant_id = v_tenant
    AND employee_id = exp.employee_id
    AND expense_month = v_scope_month
    AND (
      v_scope_department IS NULL
      OR department_id = v_scope_department
    )
    AND date_trunc('year', submitted_at) = date_trunc('year', exp.submitted_at);

  v_requires_c_level := exp.amount > 10000 OR v_monthly_total > 15000 OR v_yearly_total > 50000;

  IF command_name = 'request_finance_review' THEN
    IF exp.status NOT IN ('submitted', 'resubmitted') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: request_finance_review requires submitted or resubmitted expense';
    END IF;
    UPDATE app_data.expenses
    SET status = 'finance_review_requested'
    WHERE expense_id = exp.expense_id
      AND status IN ('submitted', 'resubmitted');
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: request_finance_review lost the expense state transition';
    END IF;
    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'finance_review_requested', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );
    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'finance_review_requested',
      'next_role', 'finance_reviewer',
      'next_task_type', 'finance_compliance_review',
      'receipt', 'finance review handoff enabled'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSIF command_name = 'finance_approve' THEN
    IF v_delegator <> 'user:fiona' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only finance reviewer user:fiona can perform finance_approve in this demo';
    END IF;
    IF exp.status NOT IN ('submitted', 'resubmitted', 'finance_review_requested') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: finance_approve requires submitted, resubmitted, or finance_review_requested expense';
    END IF;

    UPDATE app_data.expenses
    SET status = 'department_approval_requested'
    WHERE expense_id = exp.expense_id
      AND status IN ('submitted', 'resubmitted', 'finance_review_requested');
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: finance_approve lost the expense state transition';
    END IF;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'finance_compliant', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'department_approval_requested',
      'next_role', 'department_manager',
      'next_task_type', 'department_expense_approval',
      'receipt', 'finance compliance recorded; department approval handoff enabled'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSIF command_name = 'return_expense_for_more_info' THEN
    IF v_delegator <> 'user:fiona' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only finance reviewer user:fiona can return expenses for more information in this demo';
    END IF;
    IF exp.status NOT IN ('submitted', 'resubmitted', 'finance_review_requested') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only expenses in finance review can be returned for more information';
    END IF;

    UPDATE app_data.expenses
    SET status = 'returned_for_more_info'
    WHERE expense_id = exp.expense_id
      AND status IN ('submitted', 'resubmitted', 'finance_review_requested');
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: return_expense_for_more_info lost the expense state transition';
    END IF;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'returned_for_more_info', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'returned_for_more_info',
      'next_role', 'employee',
      'next_task_type', 'expense_resubmission',
      'receipt', 'employee supplement handoff enabled'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSIF command_name = 'resubmit_expense' THEN
    IF exp.status <> 'returned_for_more_info' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: resubmit_expense requires returned_for_more_info status';
    END IF;

    UPDATE app_data.expenses
    SET status = 'resubmitted'
    WHERE expense_id = exp.expense_id
      AND status = 'returned_for_more_info';
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: resubmit_expense lost the expense state transition';
    END IF;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'resubmitted', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'resubmitted',
      'next_role', 'finance_reviewer',
      'next_task_type', 'finance_compliance_review',
      'receipt', 'resubmission recorded; finance review can restart'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSIF command_name = 'department_approve' THEN
    IF exp.manager_user_id <> v_delegator THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: delegator is not the department manager';
    END IF;
    IF exp.status NOT IN ('finance_compliant', 'department_approval_requested') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: department_approve requires finance-compliant expense';
    END IF;

    v_next_status := CASE WHEN v_requires_c_level THEN 'c_level_approval_requested' ELSE 'payable' END;

    UPDATE app_data.expenses
    SET status = v_next_status
    WHERE expense_id = exp.expense_id
      AND status IN ('finance_compliant', 'department_approval_requested');
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: department_approve lost the expense state transition';
    END IF;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'department_approved', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', v_next_status,
      'requires_c_level_approval', v_requires_c_level,
      'monthly_employee_total', v_monthly_total,
      'yearly_employee_total', v_yearly_total,
      'next_role', CASE WHEN v_requires_c_level THEN 'c_level' ELSE 'finance_reviewer' END,
      'next_task_type', CASE WHEN v_requires_c_level THEN 'c_level_expense_approval' ELSE 'expense_payment' END,
      'receipt', 'department approval recorded'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSIF command_name = 'c_level_approve' THEN
    IF v_delegator <> 'user:carol' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only C-level user:carol can perform c_level_approve in this demo';
    END IF;
    IF exp.status <> 'c_level_approval_requested' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: c_level_approve requires c_level_approval_requested status';
    END IF;
    IF NOT v_requires_c_level THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: C-level approval is not required by aggregate policy';
    END IF;

    UPDATE app_data.expenses
    SET status = 'c_level_approved'
    WHERE expense_id = exp.expense_id
      AND status = 'c_level_approval_requested';
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: c_level_approve lost the expense state transition';
    END IF;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'c_level_approved', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'c_level_approved',
      'next_role', 'finance_reviewer',
      'next_task_type', 'expense_payment',
      'receipt', 'C-level approval recorded; payment can proceed'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSIF command_name = 'pay_expense' THEN
    IF v_delegator <> 'user:fiona' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only finance user:fiona can pay expenses in this demo';
    END IF;
    IF exp.status NOT IN ('payable', 'c_level_approved') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: payment requires payable or c_level_approved status';
    END IF;
    UPDATE app_data.expenses
    SET status = 'paid'
    WHERE expense_id = exp.expense_id
      AND status IN ('payable', 'c_level_approved');
    GET DIAGNOSTICS v_transition_rows = ROW_COUNT;
    IF v_transition_rows <> 1 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: pay_expense lost the expense state transition';
    END IF;

    INSERT INTO app_data.ledger_entries (
      tenant_id, expense_id, debit_account, credit_account, amount, memo, created_by, created_at
    )
    VALUES (
      v_tenant,
      exp.expense_id,
      'travel_expense',
      'cash',
      exp.amount,
      COALESCE(args->>'memo', 'travel reimbursement payment'),
      v_delegator,
      v_command_created_at
    )
    ON CONFLICT (expense_id) DO NOTHING
    RETURNING ledger_id INTO v_ledger_id;
    IF v_ledger_id IS NULL THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: ledger entry already exists for this expense';
    END IF;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
    )
    VALUES (
      v_tenant, exp.expense_id, 'paid', v_delegator, v_actor, v_supervisor, v_comment, v_command_created_at
    );

    RETURN taskbound.command_allowed_receipt_for_binding(
      p,
      command_name,
      args,
      jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'paid',
      'ledger', 'travel_expense -> cash',
      'amount', exp.amount,
      'receipt', 'payment ledger written in the same database transaction'
      ),
      taskbound.command_touched_views(command_name),
      v_binding_id,
      v_fence_token
    );
  ELSE
    RAISE EXCEPTION 'SessionBoundDB denied command: unknown command %', command_name;
  END IF;
EXCEPTION WHEN OTHERS THEN
  IF p IS NOT NULL THEN
    BEGIN
      PERFORM taskbound.command_denied_receipt_for_binding(
        p,
        command_name,
        COALESCE(args, '{}'::jsonb),
        'controlled command denied',
        v_binding_id,
        v_fence_token
      );
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;
  END IF;
  RETURN jsonb_build_object(
    'ok', false,
    'command', command_name,
    'error', 'SessionBoundDB denied command'
  );
END;
$$;

CREATE OR REPLACE FUNCTION taskbound.command(command_name text, args jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, public, pg_temp
AS $$
DECLARE
  p jsonb;
  active record;
  v_backend_start timestamptz;
  v_postmaster_start timestamptz;
  v_conn text := taskbound.command_connection_name();
  v_connections text[];
  v_sql text;
  v_result jsonb;
BEGIN
  p := taskbound.require_payload();

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
    RAISE EXCEPTION 'SessionBoundDB denied command: active binding is missing';
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

  v_sql := format(
    $sql$
    SELECT taskbound.command_apply(
      %L::jsonb,
      %L,
      %L::jsonb,
      %L::uuid,
      %s,
      %s,
      %s,
      %L::timestamptz,
      %L::timestamptz,
      %L::name
    )
    $sql$,
    p::text,
    command_name,
    COALESCE(args, '{}'::jsonb)::text,
    active.binding_id::text,
    active.fence_token,
    active.advisory_lock_key,
    active.owner_backend_pid,
    active.owner_backend_start::text,
	    active.owner_postmaster_start::text,
	    active.owner_session_user::text
  );

  SELECT result
  INTO v_result
  FROM public.dblink(v_conn, v_sql) AS t(result jsonb);

  BEGIN
    PERFORM public.dblink_disconnect(v_conn);
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;

  IF COALESCE(v_result->>'ok', 'false') <> 'true' THEN
    RETURN COALESCE(v_result, '{}'::jsonb) || jsonb_build_object(
      'ok', false,
      'command', command_name,
      'error', 'SessionBoundDB denied command'
    );
  END IF;

  RETURN v_result;
EXCEPTION WHEN OTHERS THEN
  BEGIN
    PERFORM public.dblink_disconnect(v_conn);
  EXCEPTION WHEN OTHERS THEN
    NULL;
  END;
  IF p IS NOT NULL THEN
    BEGIN
      PERFORM taskbound.command_denied_receipt(
        p,
        command_name,
        COALESCE(args, '{}'::jsonb),
        'controlled command denied'
      );
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;
  END IF;
  RETURN jsonb_build_object(
    'ok', false,
    'command', command_name,
    'error', 'SessionBoundDB denied command'
  );
END;
$$;

REVOKE ALL ON SCHEMA app_data FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA app_data FROM PUBLIC;
REVOKE ALL ON SCHEMA taskbound FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA taskbound FROM PUBLIC;
DO $$
BEGIN
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM agent_runtime', current_database());
END;
$$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA taskbound FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.sessionbound_guard_check(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.sessionbound_guard_touched_view_oids(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.sessionbound_guard_install_binding(text, text, text, text, int, int, int, boolean, boolean, text, bigint, bigint, text, text, timestamptz) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.sessionbound_guard_clear_binding() FROM PUBLIC;

REVOKE EXECUTE ON FUNCTION public.sessionbound_guard_touched_view_oids(text) FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.extract_touched_views(text, text[]) FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.fail_receipt(text, text) FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.validate_active_binding_identity(text, uuid, bigint, bigint, int, timestamptz, timestamptz, name) FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.command_connection_name() FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.command_allowed_receipt_for_binding(jsonb, text, jsonb, jsonb, text[], uuid, bigint) FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.command_denied_receipt_for_binding(jsonb, text, jsonb, text, uuid, bigint) FROM agent_runtime;
REVOKE EXECUTE ON FUNCTION taskbound.command_apply(jsonb, text, jsonb, uuid, bigint, bigint, int, timestamptz, timestamptz, name) FROM agent_runtime;
DO $$
BEGIN
  IF to_regprocedure('taskbound.append_query_receipt_local(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,text,text[],uuid)') IS NOT NULL THEN
    EXECUTE 'REVOKE EXECUTE ON FUNCTION taskbound.append_query_receipt_local(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,text,text[],uuid) FROM PUBLIC';
    EXECUTE 'REVOKE EXECUTE ON FUNCTION taskbound.append_query_receipt_local(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,text,text[],uuid) FROM agent_runtime';
  END IF;
  IF to_regprocedure('taskbound.native_record_query_denial_status(text,text,text,text,integer,integer,boolean,boolean,uuid,bigint)') IS NOT NULL THEN
    EXECUTE 'REVOKE EXECUTE ON FUNCTION taskbound.native_record_query_denial_status(text,text,text,text,integer,integer,boolean,boolean,uuid,bigint) FROM PUBLIC';
  END IF;
  IF to_regprocedure('taskbound.audit_append_receipt(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,boolean)') IS NOT NULL THEN
    EXECUTE 'REVOKE EXECUTE ON FUNCTION taskbound.audit_append_receipt(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,boolean) FROM agent_runtime';
  END IF;
  IF to_regprocedure('taskbound.audit_append_receipt(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,boolean,uuid,text,text[])') IS NOT NULL THEN
    EXECUTE 'REVOKE EXECUTE ON FUNCTION taskbound.audit_append_receipt(text,text,uuid,bigint,text,text,bigint,bigint,bigint,text,boolean,uuid,text,text[]) FROM agent_runtime';
  END IF;
END;
$$;

GRANT USAGE ON SCHEMA taskbound TO agent_runtime;
GRANT USAGE ON SCHEMA app_data TO agent_runtime;
GRANT SELECT ON TABLE
  taskbound.expenses,
  taskbound.departments,
  taskbound.employees,
  taskbound.approval_events,
  taskbound.ledger_entries
TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.bind_task(text, text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.unbind_task() TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.validate_active_binding(text, uuid, bigint, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.claim(text[]) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.current_payload() TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.require_payload() TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_denied_receipt(text, text, text, text, boolean, uuid, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_reserve_query_status(text, text, text, int, boolean, boolean, uuid, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_reserve_query(text, text, text, int, boolean, boolean, uuid, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_seen_expense_rows(text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_finish_query_status(text, text, text, bigint, text[], int, int, boolean, boolean, uuid, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_finish_query(text, text, text, bigint, text[], int, int, boolean, boolean, uuid, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.native_partial_denied_receipt(text, text, text, text, bigint, text[], int, boolean, boolean, uuid, bigint) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.run(text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.command(text, jsonb) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.inspect_task_state() TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.receipts() TO agent_runtime;

DO $$
BEGIN
  IF to_regprocedure('taskbound.native_record_query_denial_status(text,text,text,text,integer,integer,boolean,boolean,uuid,bigint)') IS NOT NULL THEN
    EXECUTE 'GRANT EXECUTE ON FUNCTION taskbound.native_record_query_denial_status(text,text,text,text,integer,integer,boolean,boolean,uuid,bigint) TO agent_runtime';
  END IF;
END $$;
