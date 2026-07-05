DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tdsc_rls_only') THEN
    CREATE ROLE tdsc_rls_only LOGIN PASSWORD 'tdsc_rls_only_pass';
  END IF;
END $$;

GRANT USAGE ON SCHEMA app_data TO tdsc_rls_only;
GRANT SELECT ON ALL TABLES IN SCHEMA app_data TO tdsc_rls_only;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
ON ALL TABLES IN SCHEMA app_data FROM tdsc_rls_only;

ALTER TABLE app_data.expenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data.employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data.departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data.approval_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data.ledger_entries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tdsc_role_only_expenses_all ON app_data.expenses;
CREATE POLICY tdsc_role_only_expenses_all ON app_data.expenses
  FOR SELECT TO tdsc_role_only
  USING (true);

DROP POLICY IF EXISTS tdsc_role_only_employees_all ON app_data.employees;
CREATE POLICY tdsc_role_only_employees_all ON app_data.employees
  FOR SELECT TO tdsc_role_only
  USING (true);

DROP POLICY IF EXISTS tdsc_role_only_departments_all ON app_data.departments;
CREATE POLICY tdsc_role_only_departments_all ON app_data.departments
  FOR SELECT TO tdsc_role_only
  USING (true);

DROP POLICY IF EXISTS tdsc_role_only_approval_events_all ON app_data.approval_events;
CREATE POLICY tdsc_role_only_approval_events_all ON app_data.approval_events
  FOR SELECT TO tdsc_role_only
  USING (true);

DROP POLICY IF EXISTS tdsc_role_only_ledger_entries_all ON app_data.ledger_entries;
CREATE POLICY tdsc_role_only_ledger_entries_all ON app_data.ledger_entries
  FOR SELECT TO tdsc_role_only
  USING (true);

DROP POLICY IF EXISTS tdsc_expenses_scope ON app_data.expenses;
CREATE POLICY tdsc_expenses_scope ON app_data.expenses
  FOR SELECT TO tdsc_rls_only
  USING (
    tenant_id = 'company_a'
    AND expense_month = '2026-06'
    AND department_id = 'dep_sales'
  );

DROP POLICY IF EXISTS tdsc_employees_scope ON app_data.employees;
CREATE POLICY tdsc_employees_scope ON app_data.employees
  FOR SELECT TO tdsc_rls_only
  USING (
    tenant_id = 'company_a'
    AND department_id = 'dep_sales'
  );

DROP POLICY IF EXISTS tdsc_departments_scope ON app_data.departments;
CREATE POLICY tdsc_departments_scope ON app_data.departments
  FOR SELECT TO tdsc_rls_only
  USING (
    tenant_id = 'company_a'
    AND department_id = 'dep_sales'
  );

DROP POLICY IF EXISTS tdsc_approval_events_scope ON app_data.approval_events;
CREATE POLICY tdsc_approval_events_scope ON app_data.approval_events
  FOR SELECT TO tdsc_rls_only
  USING (tenant_id = 'company_a');

DROP POLICY IF EXISTS tdsc_ledger_entries_scope ON app_data.ledger_entries;
CREATE POLICY tdsc_ledger_entries_scope ON app_data.ledger_entries
  FOR SELECT TO tdsc_rls_only
  USING (tenant_id = 'company_a');
