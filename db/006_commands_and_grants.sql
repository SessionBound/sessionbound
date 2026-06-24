CREATE OR REPLACE FUNCTION taskbound.command(command_name text, args jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = taskbound, pg_temp
AS $$
DECLARE
  p jsonb;
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
  emp record;
BEGIN
  p := taskbound.require_payload();
  v_task_id := p->>'task_id';
  v_tenant := p->>'tenant_id';
  v_delegator := p->>'delegator';
  v_actor := p->>'actor';
  v_expense_id := args->>'expense_id';
  v_comment := COALESCE(args->>'comment', '');
  v_supervisor := args->>'supervisor_agent_id';

  IF NOT COALESCE(p->'allowed_commands' ? command_name, false) THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: command % is not allowed by task token', command_name;
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

    IF (args->>'amount') IS NULL OR (args->>'amount')::numeric <= 0 THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: amount must be greater than zero';
    END IF;
    IF COALESCE(args->>'category', '') = '' OR COALESCE(args->>'merchant', '') = '' OR COALESCE(args->>'city', '') = '' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: category, merchant, and city are required';
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
      COALESCE(args->>'expense_month', taskbound.claim(ARRAY['row_scope', 'expense_month'])),
      args->>'category',
      args->>'merchant',
      args->>'city',
      (args->>'amount')::numeric,
      now(),
      'submitted'
    );

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, v_new_expense_id, 'submitted', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', v_new_expense_id,
      'new_status', 'submitted',
      'next_role', 'finance_reviewer',
      'next_task_type', 'finance_compliance_review',
      'receipt', 'expense submitted; finance review todo enabled'
    );
  END IF;

  SELECT e.*, d.manager_user_id INTO exp
  FROM app_data.expenses e
  JOIN app_data.departments d ON d.department_id = e.department_id
  WHERE e.expense_id = v_expense_id
    AND e.tenant_id = v_tenant
    AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
    AND (
      taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
      OR e.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
    );

  IF exp.expense_id IS NULL THEN
    RAISE EXCEPTION 'SessionBoundDB denied command: expense is outside task scope';
  END IF;

  SELECT COALESCE(sum(amount), 0) INTO v_monthly_total
  FROM app_data.expenses
  WHERE tenant_id = v_tenant
    AND employee_id = exp.employee_id
    AND date_trunc('month', submitted_at) = date_trunc('month', exp.submitted_at);

  SELECT COALESCE(sum(amount), 0) INTO v_yearly_total
  FROM app_data.expenses
  WHERE tenant_id = v_tenant
    AND employee_id = exp.employee_id
    AND date_trunc('year', submitted_at) = date_trunc('year', exp.submitted_at);

  v_requires_c_level := exp.amount > 10000 OR v_monthly_total > 15000 OR v_yearly_total > 50000;

  IF command_name = 'request_finance_review' THEN
    IF exp.status NOT IN ('submitted', 'resubmitted') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: request_finance_review requires submitted or resubmitted expense';
    END IF;
    UPDATE app_data.expenses
    SET status = 'finance_review_requested'
    WHERE expense_id = exp.expense_id;
    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'finance_review_requested', v_delegator, v_actor, v_supervisor, v_comment
    );
    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'finance_review_requested',
      'next_role', 'finance_reviewer',
      'next_task_type', 'finance_compliance_review',
      'receipt', 'finance review handoff enabled'
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
    WHERE expense_id = exp.expense_id;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'finance_compliant', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'department_approval_requested',
      'next_role', 'department_manager',
      'next_task_type', 'department_expense_approval',
      'receipt', 'finance compliance recorded; department approval handoff enabled'
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
    WHERE expense_id = exp.expense_id;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'returned_for_more_info', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'returned_for_more_info',
      'next_role', 'employee',
      'next_task_type', 'expense_resubmission',
      'receipt', 'employee supplement handoff enabled'
    );
  ELSIF command_name = 'resubmit_expense' THEN
    IF exp.status <> 'returned_for_more_info' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: resubmit_expense requires returned_for_more_info status';
    END IF;

    UPDATE app_data.expenses
    SET status = 'resubmitted'
    WHERE expense_id = exp.expense_id;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'resubmitted', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'resubmitted',
      'next_role', 'finance_reviewer',
      'next_task_type', 'finance_compliance_review',
      'receipt', 'resubmission recorded; finance review can restart'
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
    WHERE expense_id = exp.expense_id;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'department_approved', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
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
    WHERE expense_id = exp.expense_id;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'c_level_approved', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'c_level_approved',
      'next_role', 'finance_reviewer',
      'next_task_type', 'expense_payment',
      'receipt', 'C-level approval recorded; payment can proceed'
    );
  ELSIF command_name = 'pay_expense' THEN
    IF v_delegator <> 'user:fiona' THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: only finance user:fiona can pay expenses in this demo';
    END IF;
    IF exp.status NOT IN ('payable', 'c_level_approved') THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: payment requires payable or c_level_approved status';
    END IF;
    IF EXISTS (SELECT 1 FROM app_data.ledger_entries l WHERE l.expense_id = exp.expense_id) THEN
      RAISE EXCEPTION 'SessionBoundDB denied command: ledger entry already exists for this expense';
    END IF;

    INSERT INTO app_data.ledger_entries (
      tenant_id, expense_id, debit_account, credit_account, amount, memo, created_by
    )
    VALUES (
      v_tenant,
      exp.expense_id,
      'travel_expense',
      'cash',
      exp.amount,
      COALESCE(args->>'memo', 'travel reimbursement payment'),
      v_delegator
    );

    UPDATE app_data.expenses
    SET status = 'paid'
    WHERE expense_id = exp.expense_id;

    INSERT INTO app_data.approval_events (
      tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment
    )
    VALUES (
      v_tenant, exp.expense_id, 'paid', v_delegator, v_actor, v_supervisor, v_comment
    );

    RETURN jsonb_build_object(
      'ok', true,
      'command', command_name,
      'expense_id', exp.expense_id,
      'new_status', 'paid',
      'ledger', 'travel_expense -> cash',
      'amount', exp.amount,
      'receipt', 'payment ledger written in the same database transaction'
    );
  ELSE
    RAISE EXCEPTION 'SessionBoundDB denied command: unknown command %', command_name;
  END IF;
END;
$$;

REVOKE ALL ON SCHEMA app_data FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA app_data FROM PUBLIC;
REVOKE ALL ON SCHEMA taskbound FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA taskbound FROM PUBLIC;

GRANT USAGE ON SCHEMA taskbound TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.bind_task(text, text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.run(text) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.command(text, jsonb) TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.inspect_task_state() TO agent_runtime;
GRANT EXECUTE ON FUNCTION taskbound.receipts() TO agent_runtime;
