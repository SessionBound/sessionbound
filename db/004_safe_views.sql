CREATE OR REPLACE VIEW taskbound.expenses AS
SELECT
  e.expense_id,
  e.expense_month,
  e.department_id,
  d.department_name,
  emp.employee_id,
  emp.employee_name,
  emp.employee_level,
  e.category,
  e.merchant,
  e.city,
  e.amount,
  e.submitted_at,
  e.status,
  sum(e.amount) OVER (
    PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
  ) AS monthly_employee_total,
  sum(e.amount) OVER (
    PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
  ) AS yearly_employee_total,
  e.status IN ('submitted', 'resubmitted', 'finance_review_requested') AS requires_finance_review,
  e.status IN ('finance_compliant', 'department_approval_requested') AS requires_department_approval,
  (
    e.amount > 10000
    OR sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 15000
    OR sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 50000
  ) AS requires_c_level_approval,
  CASE
    WHEN e.status IN ('submitted', 'resubmitted', 'finance_review_requested') THEN 'finance_reviewer'
    WHEN e.status IN ('finance_compliant', 'department_approval_requested') THEN 'department_manager'
    WHEN e.status = 'department_approved' AND (
      e.amount > 10000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
      ) > 15000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
      ) > 50000
    ) THEN 'c_level'
    WHEN e.status IN ('department_approved', 'c_level_approved', 'payable') THEN 'finance_reviewer'
    ELSE NULL
  END AS next_required_role,
  CASE
    WHEN e.status IN ('submitted', 'resubmitted', 'finance_review_requested') THEN 'finance_compliance_review'
    WHEN e.status IN ('finance_compliant', 'department_approval_requested') THEN 'department_expense_approval'
    WHEN e.status = 'department_approved' AND (
      e.amount > 10000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
      ) > 15000
      OR sum(e.amount) OVER (
        PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
      ) > 50000
    ) THEN 'c_level_expense_approval'
    WHEN e.status IN ('department_approved', 'c_level_approved', 'payable') THEN 'expense_payment'
    ELSE NULL
  END AS next_task_type,
  CASE
    WHEN e.amount > 10000 THEN 2
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 15000 THEN 2
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 50000 THEN 2
    WHEN e.status IN ('finance_compliant', 'department_approval_requested') THEN 1
    ELSE NULL
  END AS approval_tier,
  CASE
    WHEN e.amount > 10000 THEN 'single_amount_over_c_level_limit'
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('month', e.submitted_at)
    ) > 15000 THEN 'monthly_total_over_c_level_limit'
    WHEN sum(e.amount) OVER (
      PARTITION BY e.tenant_id, e.employee_id, date_trunc('year', e.submitted_at)
    ) > 50000 THEN 'yearly_total_over_c_level_limit'
    WHEN e.status IN ('finance_compliant', 'department_approval_requested') THEN 'standard_department_review'
    ELSE NULL
  END AS approval_reason,
  e.status IN ('submitted', 'resubmitted', 'finance_review_requested') AS can_finance_approve,
  (e.status IN ('finance_compliant', 'department_approval_requested') AND d.manager_user_id = taskbound.claim(ARRAY['delegator'])) AS can_department_approve,
  (e.status = 'c_level_approval_requested' AND taskbound.claim(ARRAY['delegator']) = 'user:carol') AS can_c_level_approve,
  e.status IN ('finance_review_requested', 'submitted', 'resubmitted') AS can_request_more_info,
  e.status = 'returned_for_more_info' AS can_resubmit,
  (
    e.status IN ('payable', 'c_level_approved')
    AND NOT EXISTS (
      SELECT 1 FROM app_data.ledger_entries l WHERE l.expense_id = e.expense_id
    )
  ) AS can_pay
FROM app_data.expenses e
JOIN app_data.departments d ON d.department_id = e.department_id
JOIN app_data.employees emp ON emp.employee_id = e.employee_id
WHERE e.tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
  AND (
    taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
    OR e.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
  );

COMMENT ON VIEW taskbound.expenses IS
  'SessionBound safe view for travel reimbursement claims scoped by tenant, expense month, and optional department.';
COMMENT ON COLUMN taskbound.expenses.requires_finance_review IS
  'True when the claim is submitted or resubmitted and should enter finance compliance review.';
COMMENT ON COLUMN taskbound.expenses.can_department_approve IS
  'True when finance compliance is complete and the delegated user is the department manager.';
COMMENT ON COLUMN taskbound.expenses.can_c_level_approve IS
  'True when C-level approval is requested and the delegated user has the demo C-level identity.';
COMMENT ON COLUMN taskbound.expenses.can_pay IS
  'True when the claim has completed all required approvals and has no payment ledger entry.';

CREATE OR REPLACE VIEW taskbound.departments AS
SELECT department_id, department_name, manager_user_id
FROM app_data.departments
WHERE tenant_id = taskbound.claim(ARRAY['tenant_id']);

COMMENT ON VIEW taskbound.departments IS
  'SessionBound safe view for tenant-scoped department metadata.';

CREATE OR REPLACE VIEW taskbound.employees AS
SELECT employee_id, department_id, employee_name, employee_level
FROM app_data.employees
WHERE tenant_id = taskbound.claim(ARRAY['tenant_id']);

COMMENT ON VIEW taskbound.employees IS
  'SessionBound safe view for employee identity and level with salary, phone, and bank account excluded.';

CREATE OR REPLACE VIEW taskbound.approval_events AS
SELECT event_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
FROM app_data.approval_events
WHERE tenant_id = taskbound.claim(ARRAY['tenant_id']);

COMMENT ON VIEW taskbound.approval_events IS
  'SessionBound safe view for workflow audit events emitted by controlled commands.';

CREATE OR REPLACE VIEW taskbound.ledger_entries AS
SELECT ledger_id, expense_id, debit_account, credit_account, amount, memo, created_by, created_at
FROM app_data.ledger_entries
WHERE tenant_id = taskbound.claim(ARRAY['tenant_id']);

COMMENT ON VIEW taskbound.ledger_entries IS
  'SessionBound safe view for ledger entries created by controlled payment commands.';

INSERT INTO taskbound.safe_view_registry (
  view_name,
  database_object,
  business_object,
  maintainer,
  allowed_tasks,
  scope_fields,
  workflow_fields,
  sensitive_fields_excluded,
  recommended_commands,
  description
) VALUES
  (
    'expenses',
    'taskbound.expenses',
    'travel_expense_claim',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id', 'expense_month', 'department_id'],
    ARRAY['status', 'requires_finance_review', 'requires_department_approval', 'requires_c_level_approval', 'next_required_role', 'next_task_type', 'approval_tier', 'approval_reason', 'can_finance_approve', 'can_department_approve', 'can_c_level_approve', 'can_pay'],
    ARRAY['employees.phone', 'employees.bank_account', 'employees.salary'],
    ARRAY['finance_approve', 'department_approve', 'c_level_approve', 'return_expense_for_more_info', 'resubmit_expense', 'pay_expense'],
    'Travel reimbursement claims scoped by tenant, month, and optional department.'
  ),
  (
    'employees',
    'taskbound.employees',
    'employee_dimension',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id'],
    ARRAY[]::text[],
    ARRAY['phone', 'bank_account', 'salary'],
    ARRAY[]::text[],
    'Employee dimension with sensitive HR and payment fields removed.'
  ),
  (
    'departments',
    'taskbound.departments',
    'department_dimension',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id'],
    ARRAY[]::text[],
    ARRAY[]::text[],
    ARRAY[]::text[],
    'Department dimension scoped by tenant.'
  ),
  (
    'approval_events',
    'taskbound.approval_events',
    'approval_audit_event',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id'],
    ARRAY['event_type'],
    ARRAY[]::text[],
    ARRAY[]::text[],
    'Workflow audit events emitted by SessionBoundDB commands.'
  ),
  (
    'ledger_entries',
    'taskbound.ledger_entries',
    'payment_ledger_entry',
    'finance-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id'],
    ARRAY[]::text[],
    ARRAY[]::text[],
    ARRAY['pay_expense'],
    'Ledger entries created by controlled payment commands.'
  );
