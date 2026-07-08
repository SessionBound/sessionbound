DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tdsc_rls_view_owner') THEN
    CREATE ROLE tdsc_rls_view_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tdsc_rls_safe_audit') THEN
    CREATE ROLE tdsc_rls_safe_audit LOGIN PASSWORD 'tdsc_rls_safe_audit_pass';
  ELSE
    ALTER ROLE tdsc_rls_safe_audit LOGIN PASSWORD 'tdsc_rls_safe_audit_pass';
  END IF;
  EXECUTE format('ALTER ROLE tdsc_rls_safe_audit VALID UNTIL %L', now() + interval '1 hour');
END $$;

CREATE SCHEMA IF NOT EXISTS tdsc_rls_audit;
CREATE SCHEMA IF NOT EXISTS tdsc_rls_safe_view AUTHORIZATION tdsc_rls_view_owner;
ALTER SCHEMA tdsc_rls_safe_view OWNER TO tdsc_rls_view_owner;

CREATE TABLE IF NOT EXISTS tdsc_rls_audit.query_audit (
  audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor text NOT NULL,
  session_id text NOT NULL,
  query_digest text NOT NULL,
  rows_returned bigint,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION tdsc_rls_audit.log_query(
  v_actor text,
  v_session_id text,
  v_sql text,
  v_rows_returned bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = tdsc_rls_audit, public, pg_temp
AS $$
BEGIN
  INSERT INTO tdsc_rls_audit.query_audit (
    actor, session_id, query_digest, rows_returned
  )
  VALUES (
    v_actor,
    v_session_id,
    encode(public.digest(COALESCE(v_sql, ''), 'sha256'), 'hex'),
    v_rows_returned
  );
END;
$$;

REVOKE ALL ON SCHEMA tdsc_rls_audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA tdsc_rls_audit FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA tdsc_rls_audit FROM PUBLIC;
GRANT USAGE ON SCHEMA tdsc_rls_audit TO tdsc_rls_safe_audit;
GRANT EXECUTE ON FUNCTION tdsc_rls_audit.log_query(text, text, text, bigint) TO tdsc_rls_safe_audit;

GRANT USAGE ON SCHEMA app_data TO tdsc_rls_view_owner;
GRANT SELECT ON ALL TABLES IN SCHEMA app_data TO tdsc_rls_view_owner;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
ON ALL TABLES IN SCHEMA app_data FROM tdsc_rls_view_owner;

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

DROP POLICY IF EXISTS tdsc_rls_safe_expenses_scope ON app_data.expenses;
CREATE POLICY tdsc_rls_safe_expenses_scope ON app_data.expenses
  FOR SELECT TO tdsc_rls_view_owner
  USING (
    tenant_id = 'company_a'
    AND expense_month = '2026-06'
    AND department_id = 'dep_sales'
  );

DROP POLICY IF EXISTS tdsc_rls_safe_employees_scope ON app_data.employees;
CREATE POLICY tdsc_rls_safe_employees_scope ON app_data.employees
  FOR SELECT TO tdsc_rls_view_owner
  USING (
    tenant_id = 'company_a'
    AND department_id = 'dep_sales'
  );

DROP POLICY IF EXISTS tdsc_rls_safe_departments_scope ON app_data.departments;
CREATE POLICY tdsc_rls_safe_departments_scope ON app_data.departments
  FOR SELECT TO tdsc_rls_view_owner
  USING (
    tenant_id = 'company_a'
    AND department_id = 'dep_sales'
  );

DROP POLICY IF EXISTS tdsc_rls_safe_approval_events_scope ON app_data.approval_events;
CREATE POLICY tdsc_rls_safe_approval_events_scope ON app_data.approval_events
  FOR SELECT TO tdsc_rls_view_owner
  USING (tenant_id = 'company_a');

DROP POLICY IF EXISTS tdsc_rls_safe_ledger_entries_scope ON app_data.ledger_entries;
CREATE POLICY tdsc_rls_safe_ledger_entries_scope ON app_data.ledger_entries
  FOR SELECT TO tdsc_rls_view_owner
  USING (tenant_id = 'company_a');

CREATE OR REPLACE VIEW tdsc_rls_safe_view.expenses AS
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
JOIN app_data.employees emp ON emp.employee_id = e.employee_id;
ALTER VIEW tdsc_rls_safe_view.expenses OWNER TO tdsc_rls_view_owner;

CREATE OR REPLACE VIEW tdsc_rls_safe_view.employees AS
SELECT employee_id, department_id, employee_name, employee_level
FROM app_data.employees;
ALTER VIEW tdsc_rls_safe_view.employees OWNER TO tdsc_rls_view_owner;

CREATE OR REPLACE VIEW tdsc_rls_safe_view.departments AS
SELECT department_id, department_name, manager_user_id
FROM app_data.departments;
ALTER VIEW tdsc_rls_safe_view.departments OWNER TO tdsc_rls_view_owner;

CREATE OR REPLACE VIEW tdsc_rls_safe_view.approval_events AS
SELECT event_id, expense_id, event_type, actor, agent_actor, supervisor_agent_id, comment, created_at
FROM app_data.approval_events;
ALTER VIEW tdsc_rls_safe_view.approval_events OWNER TO tdsc_rls_view_owner;

CREATE OR REPLACE VIEW tdsc_rls_safe_view.ledger_entries AS
SELECT ledger_id, expense_id, debit_account, credit_account, amount, memo, created_by, created_at
FROM app_data.ledger_entries;
ALTER VIEW tdsc_rls_safe_view.ledger_entries OWNER TO tdsc_rls_view_owner;

REVOKE ALL ON SCHEMA app_data FROM tdsc_rls_safe_audit;
REVOKE ALL ON ALL TABLES IN SCHEMA app_data FROM tdsc_rls_safe_audit;
GRANT USAGE ON SCHEMA tdsc_rls_safe_view TO tdsc_rls_safe_audit;
GRANT SELECT ON ALL TABLES IN SCHEMA tdsc_rls_safe_view TO tdsc_rls_safe_audit;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
ON ALL TABLES IN SCHEMA tdsc_rls_safe_view FROM tdsc_rls_safe_audit;
