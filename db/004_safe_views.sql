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
COMMENT ON COLUMN taskbound.expenses.monthly_employee_total IS
  'Employee total after the approved task row-scope filter for the current month.';
COMMENT ON COLUMN taskbound.expenses.yearly_employee_total IS
  'Employee year-bucket total after the approved task row-scope filter; it intentionally does not expose out-of-scope months.';
COMMENT ON COLUMN taskbound.expenses.requires_finance_review IS
  'True when the claim is submitted or resubmitted and should enter finance compliance review.';
COMMENT ON COLUMN taskbound.expenses.can_department_approve IS
  'True when finance compliance is complete and the delegated user is the department manager.';
COMMENT ON COLUMN taskbound.expenses.can_c_level_approve IS
  'True when C-level approval is requested and the delegated user has the demo C-level identity.';
COMMENT ON COLUMN taskbound.expenses.can_pay IS
  'True when the claim has completed all required approvals and has no payment ledger entry.';

CREATE OR REPLACE VIEW taskbound.departments AS
SELECT DISTINCT d.department_id, d.department_name, d.manager_user_id
FROM app_data.departments d
WHERE d.tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND (
    taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
    OR d.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
  )
  AND EXISTS (
    SELECT 1
    FROM app_data.expenses e
    WHERE e.tenant_id = d.tenant_id
      AND e.department_id = d.department_id
      AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
  );

COMMENT ON VIEW taskbound.departments IS
  'SessionBound safe view for department metadata scoped by tenant, expense month, and optional department.';

CREATE OR REPLACE VIEW taskbound.employees AS
SELECT DISTINCT emp.employee_id, emp.department_id, emp.employee_name, emp.employee_level
FROM app_data.employees emp
WHERE emp.tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND (
    taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
    OR emp.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
  )
  AND EXISTS (
    SELECT 1
    FROM app_data.expenses e
    WHERE e.tenant_id = emp.tenant_id
      AND e.employee_id = emp.employee_id
      AND e.department_id = emp.department_id
      AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
  );

COMMENT ON VIEW taskbound.employees IS
  'SessionBound safe view for employee identity and level scoped to employees with in-scope expense rows; salary, phone, and bank account are excluded.';

CREATE OR REPLACE VIEW taskbound.approval_events AS
SELECT ae.event_id, ae.expense_id, ae.event_type, ae.actor, ae.agent_actor,
       ae.supervisor_agent_id, ae.comment, ae.created_at
FROM app_data.approval_events ae
JOIN app_data.expenses e ON e.expense_id = ae.expense_id
WHERE ae.tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND e.tenant_id = ae.tenant_id
  AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
  AND (
    taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
    OR e.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
  );

COMMENT ON VIEW taskbound.approval_events IS
  'SessionBound safe view for workflow audit events scoped through the corresponding in-scope expense row.';

CREATE OR REPLACE VIEW taskbound.ledger_entries AS
SELECT l.ledger_id, l.expense_id, l.debit_account, l.credit_account, l.amount,
       l.memo, l.created_by, l.created_at
FROM app_data.ledger_entries l
JOIN app_data.expenses e ON e.expense_id = l.expense_id
WHERE l.tenant_id = taskbound.claim(ARRAY['tenant_id'])
  AND e.tenant_id = l.tenant_id
  AND e.expense_month = taskbound.claim(ARRAY['row_scope', 'expense_month'])
  AND (
    taskbound.claim(ARRAY['row_scope', 'department_id']) IS NULL
    OR e.department_id = taskbound.claim(ARRAY['row_scope', 'department_id'])
  );

COMMENT ON VIEW taskbound.ledger_entries IS
  'SessionBound safe view for ledger entries scoped through the corresponding in-scope expense row.';

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
  registry_version,
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
    2,
    'Travel reimbursement claims scoped by tenant, month, and optional department.'
  ),
  (
    'employees',
    'taskbound.employees',
    'employee_dimension',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id', 'expense_month', 'department_id'],
    ARRAY[]::text[],
    ARRAY['phone', 'bank_account', 'salary'],
    ARRAY[]::text[],
    2,
    'Employee dimension scoped to employees with in-scope expense rows; sensitive HR and payment fields are removed.'
  ),
  (
    'departments',
    'taskbound.departments',
    'department_dimension',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id', 'expense_month', 'department_id'],
    ARRAY[]::text[],
    ARRAY[]::text[],
    ARRAY[]::text[],
    2,
    'Department dimension scoped by tenant, month, and optional department through in-scope expense rows.'
  ),
  (
    'approval_events',
    'taskbound.approval_events',
    'approval_audit_event',
    'data-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id', 'expense_month', 'department_id'],
    ARRAY['event_type'],
    ARRAY[]::text[],
    ARRAY[]::text[],
    2,
    'Workflow audit events emitted by SessionBoundDB commands and scoped through in-scope expense rows.'
  ),
  (
    'ledger_entries',
    'taskbound.ledger_entries',
    'payment_ledger_entry',
    'finance-platform',
    ARRAY['monthly_travel_expense_review'],
    ARRAY['tenant_id', 'expense_month', 'department_id'],
    ARRAY[]::text[],
    ARRAY[]::text[],
    ARRAY['pay_expense'],
    2,
    'Ledger entries created by controlled payment commands and scoped through in-scope expense rows.'
  )
ON CONFLICT (view_name) DO UPDATE SET
  database_object = EXCLUDED.database_object,
  business_object = EXCLUDED.business_object,
  maintainer = EXCLUDED.maintainer,
  allowed_tasks = EXCLUDED.allowed_tasks,
  scope_fields = EXCLUDED.scope_fields,
  workflow_fields = EXCLUDED.workflow_fields,
  sensitive_fields_excluded = EXCLUDED.sensitive_fields_excluded,
  recommended_commands = EXCLUDED.recommended_commands,
  registry_version = EXCLUDED.registry_version,
  description = EXCLUDED.description;
