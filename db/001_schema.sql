CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE ROLE agent_runtime NOLOGIN;
CREATE ROLE agent_app LOGIN PASSWORD 'agentpass';
GRANT agent_runtime TO agent_app;

CREATE SCHEMA app_data;
CREATE SCHEMA taskbound;

CREATE TABLE app_data.departments (
  department_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  department_name text NOT NULL,
  manager_user_id text NOT NULL
);

CREATE TABLE app_data.employees (
  employee_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  department_id text NOT NULL REFERENCES app_data.departments(department_id),
  employee_name text NOT NULL,
  employee_level text NOT NULL,
  phone text NOT NULL,
  bank_account text NOT NULL,
  salary numeric(12,2) NOT NULL
);

CREATE TABLE app_data.expenses (
  expense_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  employee_id text NOT NULL REFERENCES app_data.employees(employee_id),
  department_id text NOT NULL REFERENCES app_data.departments(department_id),
  expense_month text NOT NULL,
  category text NOT NULL,
  merchant text NOT NULL,
  city text NOT NULL,
  amount numeric(12,2) NOT NULL,
  submitted_at timestamptz NOT NULL,
  status text NOT NULL
);

CREATE TABLE app_data.approval_events (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  expense_id text NOT NULL REFERENCES app_data.expenses(expense_id),
  event_type text NOT NULL,
  actor text NOT NULL,
  agent_actor text NOT NULL,
  supervisor_agent_id text,
  comment text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_data.ledger_entries (
  ledger_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id text NOT NULL,
  expense_id text NOT NULL REFERENCES app_data.expenses(expense_id),
  debit_account text NOT NULL,
  credit_account text NOT NULL,
  amount numeric(12,2) NOT NULL,
  memo text NOT NULL,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE taskbound.signing_keys (
  key_id text PRIMARY KEY,
  secret text NOT NULL
);

CREATE TABLE taskbound.active_sessions (
  backend_pid int PRIMARY KEY,
  task_id text NOT NULL,
  payload jsonb NOT NULL,
  bound_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE taskbound.task_execution_state (
  task_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  delegator text NOT NULL,
  actor text NOT NULL,
  purpose text NOT NULL,
  budget_account text NOT NULL,
  query_count int NOT NULL DEFAULT 0,
  returned_rows bigint NOT NULL DEFAULT 0,
  unique_expense_rows bigint NOT NULL DEFAULT 0,
  revoked boolean NOT NULL DEFAULT false,
  started_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

CREATE TABLE taskbound.task_rows_seen (
  budget_account text NOT NULL,
  row_kind text NOT NULL,
  row_id text NOT NULL,
  PRIMARY KEY (budget_account, row_kind, row_id)
);

CREATE TABLE taskbound.task_query_receipts (
  receipt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id text NOT NULL,
  budget_account text NOT NULL,
  query_digest text NOT NULL,
  decision text NOT NULL,
  rows_returned bigint NOT NULL DEFAULT 0,
  unique_rows_added bigint NOT NULL DEFAULT 0,
  remaining_unique_row_budget bigint,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE taskbound.safe_view_registry (
  view_name text PRIMARY KEY,
  database_object text NOT NULL,
  business_object text NOT NULL,
  maintainer text NOT NULL,
  allowed_tasks text[] NOT NULL,
  scope_fields text[] NOT NULL,
  workflow_fields text[] NOT NULL,
  sensitive_fields_excluded text[] NOT NULL,
  recommended_commands text[] NOT NULL,
  description text NOT NULL
);
