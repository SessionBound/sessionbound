CREATE SCHEMA IF NOT EXISTS tdsc_safe_view_only;

CREATE OR REPLACE VIEW tdsc_safe_view_only.expenses AS
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
  e.status
FROM app_data.expenses e
JOIN app_data.departments d ON d.department_id = e.department_id
JOIN app_data.employees emp ON emp.employee_id = e.employee_id
WHERE e.tenant_id = 'company_a'
  AND e.expense_month = '2026-06'
  AND e.department_id = 'dep_sales';

CREATE OR REPLACE VIEW tdsc_safe_view_only.employees AS
SELECT employee_id, department_id, employee_name, employee_level
FROM app_data.employees
WHERE tenant_id = 'company_a'
  AND department_id = 'dep_sales';

CREATE OR REPLACE VIEW tdsc_safe_view_only.departments AS
SELECT department_id, department_name, manager_user_id
FROM app_data.departments
WHERE tenant_id = 'company_a'
  AND department_id = 'dep_sales';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tdsc_safe_view_only') THEN
    CREATE ROLE tdsc_safe_view_only LOGIN PASSWORD 'tdsc_safe_view_only_pass';
  END IF;
END $$;

GRANT USAGE ON SCHEMA tdsc_safe_view_only TO tdsc_safe_view_only;
GRANT SELECT ON ALL TABLES IN SCHEMA tdsc_safe_view_only TO tdsc_safe_view_only;

REVOKE ALL ON SCHEMA app_data FROM tdsc_safe_view_only;
REVOKE ALL ON ALL TABLES IN SCHEMA app_data FROM tdsc_safe_view_only;

