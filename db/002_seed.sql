INSERT INTO taskbound.signing_keys VALUES ('dev', 'dev-secret-change-me');

INSERT INTO app_data.departments VALUES
  ('dep_fin', 'company_a', 'Finance', 'user:alice'),
  ('dep_sales', 'company_a', 'Sales', 'user:alice'),
  ('dep_eng', 'company_a', 'Engineering', 'user:alice'),
  ('dep_fin_b', 'company_b', 'Finance', 'user:diana');

INSERT INTO app_data.departments (
  department_id, tenant_id, department_name, manager_user_id
)
SELECT
  format('dep_%03s', gs),
  CASE WHEN gs % 12 = 0 THEN 'company_b' ELSE 'company_a' END,
  (ARRAY[
    'Customer Success', 'Network Operations', 'Cloud Platform', 'Security',
    'Product', 'Procurement', 'Legal', 'Marketing', 'Field Engineering',
    'Data Platform', 'Partner Alliance', 'Corporate IT'
  ])[((gs - 1) % 12) + 1] || ' ' || lpad(gs::text, 3, '0'),
  'user:manager_' || lpad(gs::text, 3, '0')
FROM generate_series(1, 120) AS gs;

INSERT INTO app_data.employees VALUES
  ('emp_001', 'company_a', 'dep_fin', 'Alice Zhang', 'M2', '13800000001', '6222-0001', 48000),
  ('emp_002', 'company_a', 'dep_fin', 'Ben Liu', 'L4', '13800000002', '6222-0002', 28000),
  ('emp_003', 'company_a', 'dep_sales', 'Cindy Wang', 'L5', '13800000003', '6222-0003', 32000),
  ('emp_004', 'company_a', 'dep_sales', 'David Chen', 'L3', '13800000004', '6222-0004', 19000),
  ('emp_005', 'company_a', 'dep_eng', 'Eva Li', 'L5', '13800000005', '6222-0005', 35000),
  ('emp_006', 'company_b', 'dep_fin_b', 'Frank Wu', 'M1', '13900000006', '6333-0006', 45000);

WITH generated_employees AS (
  SELECT
    gs,
    CASE
      WHEN gs % 18 = 0 THEN 'company_b'
      ELSE 'company_a'
    END AS tenant_id,
    CASE
      WHEN gs % 18 = 0 THEN 'dep_fin_b'
      WHEN gs % 10 IN (0, 1, 2) THEN 'dep_sales'
      WHEN gs % 10 IN (3, 4, 5) THEN 'dep_eng'
      WHEN gs % 10 IN (6, 7) THEN 'dep_fin'
      ELSE format('dep_%03s', ((gs - 1) % 110) + 1)
    END AS department_id
  FROM generate_series(7, 240) AS gs
)
INSERT INTO app_data.employees (
  employee_id, tenant_id, department_id, employee_name, employee_level,
  phone, bank_account, salary
)
SELECT
  format('emp_%03s', gs),
  tenant_id,
  department_id,
  (ARRAY[
    'Aaron Chen', 'Bella Li', 'Calvin Wang', 'Doris Zhang', 'Ethan Liu',
    'Fiona Xu', 'George Huang', 'Helen Zhao', 'Ivan Sun', 'Jenny Wu',
    'Kevin Ma', 'Lily Zhou', 'Martin Gao', 'Nina He', 'Oscar Lin',
    'Phoebe Tang', 'Quentin Luo', 'Rita Yang', 'Samuel Gu', 'Tina Qian'
  ])[((gs - 1) % 20) + 1] || ' ' || lpad(gs::text, 3, '0'),
  (ARRAY['L2', 'L3', 'L4', 'L5', 'M1', 'M2'])[((gs - 1) % 6) + 1],
  '138' || lpad(gs::text, 8, '0'),
  '6222-' || lpad(gs::text, 6, '0'),
  16000 + ((gs % 30) * 1250)
FROM generated_employees;

INSERT INTO app_data.expenses VALUES
  ('exp_001', 'company_a', 'emp_001', 'dep_fin', '2026-06', 'flight', 'Air China', 'Beijing', 1880, '2026-06-03T10:00:00Z', 'payable'),
  ('exp_002', 'company_a', 'emp_001', 'dep_fin', '2026-06', 'hotel', 'Hilton', 'Shanghai', 2360, '2026-06-04T10:00:00Z', 'submitted'),
  ('exp_003', 'company_a', 'emp_002', 'dep_fin', '2026-06', 'taxi', 'Didi', 'Shanghai', 180, '2026-06-05T10:00:00Z', 'payable'),
  ('exp_004', 'company_a', 'emp_003', 'dep_sales', '2026-06', 'flight', 'China Eastern', 'Shenzhen', 2100, '2026-06-06T10:00:00Z', 'payable'),
  ('exp_005', 'company_a', 'emp_003', 'dep_sales', '2026-06', 'meal', 'Client Dinner', 'Shenzhen', 3680, '2026-06-06T20:00:00Z', 'payable'),
  ('exp_006', 'company_a', 'emp_004', 'dep_sales', '2026-06', 'hotel', 'Marriott', 'Guangzhou', 1680, '2026-06-08T10:00:00Z', 'payable'),
  ('exp_007', 'company_a', 'emp_005', 'dep_eng', '2026-06', 'train', 'CRH', 'Hangzhou', 560, '2026-06-09T10:00:00Z', 'payable'),
  ('exp_008', 'company_a', 'emp_005', 'dep_eng', '2026-06', 'equipment', 'Apple Store', 'Hangzhou', 12999, '2026-06-10T10:00:00Z', 'submitted'),
  ('exp_009', 'company_a', 'emp_003', 'dep_sales', '2026-05', 'hotel', 'Hyatt', 'Chengdu', 1500, '2026-05-12T10:00:00Z', 'approved'),
  ('exp_010', 'company_b', 'emp_006', 'dep_fin_b', '2026-06', 'flight', 'Air China', 'Beijing', 1999, '2026-06-03T10:00:00Z', 'approved');

WITH company_employees AS (
  SELECT
    row_number() OVER (ORDER BY employee_id) AS rn,
    employee_id,
    tenant_id,
    department_id
  FROM app_data.employees
),
generated_expenses AS (
  SELECT
    gs,
    e.employee_id,
    e.tenant_id,
    e.department_id,
    CASE
      WHEN gs % 11 = 0 THEN '2026-05'
      WHEN gs % 17 = 0 THEN '2026-07'
      ELSE '2026-06'
    END AS expense_month
  FROM generate_series(11, 430) AS gs
  JOIN company_employees e
    ON e.rn = ((gs - 11) % (SELECT count(*) FROM company_employees)) + 1
)
INSERT INTO app_data.expenses (
  expense_id, tenant_id, employee_id, department_id, expense_month,
  category, merchant, city, amount, submitted_at, status
)
SELECT
  format('exp_%03s', gs),
  tenant_id,
  employee_id,
  department_id,
  expense_month,
  (ARRAY['flight', 'hotel', 'taxi', 'meal', 'train', 'conference', 'equipment', 'mobile', 'client_event', 'software'])[((gs - 1) % 10) + 1],
  (ARRAY['Air China', 'Hilton', 'Didi', 'Client Dinner', 'CRH', 'AWS Summit', 'Apple Store', 'China Mobile', 'Partner Workshop', 'GitHub'])[((gs - 1) % 10) + 1],
  (ARRAY['Beijing', 'Shanghai', 'Shenzhen', 'Guangzhou', 'Hangzhou', 'Chengdu', 'Nanjing', 'Wuhan', 'Xi''an', 'Hong Kong'])[((gs - 1) % 10) + 1],
  CASE
    WHEN gs % 37 = 0 THEN 12000 + (gs % 9) * 650
    WHEN gs % 13 = 0 THEN 4500 + (gs % 7) * 420
    WHEN gs % 5 = 0 THEN 1600 + (gs % 11) * 180
    ELSE 120 + (gs % 24) * 95
  END,
  (
    CASE
      WHEN expense_month = '2026-05' THEN '2026-05-01T09:00:00Z'::timestamptz
      WHEN expense_month = '2026-07' THEN '2026-07-01T09:00:00Z'::timestamptz
      ELSE '2026-06-01T09:00:00Z'::timestamptz
    END
    + ((gs % 27) || ' days')::interval
    + ((gs % 8) || ' hours')::interval
  ),
  (ARRAY[
    'submitted', 'finance_review_requested', 'finance_compliant',
    'department_approval_requested', 'department_approved',
    'c_level_approval_requested', 'c_level_approved', 'payable',
    'paid', 'returned_for_more_info'
  ])[((gs - 1) % 10) + 1]
FROM generated_expenses;

INSERT INTO app_data.approval_events (
  tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id,
  comment, created_at
)
SELECT
  e.tenant_id,
  e.expense_id,
  'expense_submitted',
  'user:' || lower(split_part(emp.employee_name, ' ', 1)),
  'agent:expense-intake',
  NULL,
  'Initial reimbursement submission',
  e.submitted_at
FROM app_data.expenses e
JOIN app_data.employees emp ON emp.employee_id = e.employee_id;

INSERT INTO app_data.approval_events (
  tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id,
  comment, created_at
)
SELECT
  e.tenant_id,
  e.expense_id,
  CASE
    WHEN e.status IN ('finance_compliant', 'department_approval_requested', 'department_approved', 'c_level_approval_requested', 'c_level_approved', 'payable', 'paid') THEN 'finance_approved'
    WHEN e.status = 'returned_for_more_info' THEN 'returned_for_more_info'
    ELSE 'finance_review_requested'
  END,
  'user:fiona',
  'agent:finance-reviewer',
  'agent:expense-supervisor',
  CASE
    WHEN e.amount > 10000 THEN 'High-value claim flagged for extra approval'
    ELSE 'Reviewed against policy and receipt requirements'
  END,
  e.submitted_at + interval '1 day'
FROM app_data.expenses e
WHERE e.status <> 'submitted';

INSERT INTO app_data.approval_events (
  tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id,
  comment, created_at
)
SELECT
  e.tenant_id,
  e.expense_id,
  'department_approved',
  d.manager_user_id,
  'agent:department-reviewer',
  'agent:expense-supervisor',
  'Department manager approved business purpose',
  e.submitted_at + interval '2 days'
FROM app_data.expenses e
JOIN app_data.departments d ON d.department_id = e.department_id
WHERE e.status IN ('department_approved', 'c_level_approval_requested', 'c_level_approved', 'payable', 'paid');

INSERT INTO app_data.approval_events (
  tenant_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id,
  comment, created_at
)
SELECT
  e.tenant_id,
  e.expense_id,
  'c_level_approved',
  'user:carol',
  'agent:c-level-reviewer',
  'agent:expense-supervisor',
  'C-level approval completed for high-value claim',
  e.submitted_at + interval '3 days'
FROM app_data.expenses e
WHERE e.status IN ('c_level_approved', 'payable', 'paid')
  AND e.amount > 10000;

INSERT INTO app_data.ledger_entries (
  tenant_id, expense_id, debit_account, credit_account, amount, memo, created_by,
  created_at
)
SELECT
  e.tenant_id,
  e.expense_id,
  'travel_expense',
  'cash_clearing',
  e.amount,
  'Travel reimbursement payment for ' || e.expense_id,
  'user:fiona',
  e.submitted_at + interval '4 days'
FROM app_data.expenses e
WHERE e.status IN ('payable', 'paid', 'c_level_approved', 'department_approved')
  AND e.expense_id NOT IN ('exp_008');
